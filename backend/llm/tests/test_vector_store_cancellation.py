from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase
from qdrant_client import models

from llm import vector_store
from llm.service import chat_runs
from llm.v1 import progress
from llm.v1.rag.course import agent, parallel, preparation


def collection_info():
    return SimpleNamespace(config=SimpleNamespace(
        params=SimpleNamespace(vectors=models.VectorParams(
            size=1536, distance=models.Distance.COSINE)),
        metadata={"embedding_model": "text-embedding-3-small"}))


class VectorStoreCancellationTests(SimpleTestCase):
    def test_preparation_fallback_stops_after_current_vector_operation(self):
        for error in (chat_runs.Stopped, progress.ProgressCancelled, RuntimeError):
            for boundary in ("collection_exists", "get_collection", "scroll"):
                entered, release = Event(), Event()
                calls = []

                def operation(name, result):
                    def run(*args, **kwargs):
                        calls.append(name)
                        if name == boundary:
                            entered.set()
                            self.assertTrue(release.wait(2))
                            for _ in range(200):
                                if chat_runs._cancelled.get()():
                                    break
                                Event().wait(.005)
                            self.assertTrue(chat_runs._cancelled.get()())
                        return result
                    return run

                db = Mock()
                db.collection_exists.side_effect = operation("collection_exists", True)
                db.get_collection.side_effect = operation("get_collection", collection_info())
                db.scroll.side_effect = operation("scroll", ([], "next-page"))

                def interpret():
                    self.assertTrue(entered.wait(2))
                    release.set()
                    raise error("interpret failed")

                with self.subTest(error=error, boundary=boundary), \
                        patch.object(agent, "load_schedule", return_value=({}, 1)), \
                        patch.object(agent, "find_game", return_value=({"id": 1}, False, [])), \
                        patch.object(agent, "invoke_domain_tool", return_value={"item": None}), \
                        patch.object(agent.kakao, "nearby") as nearby, \
                        patch.object(vector_store, "client", return_value=db), \
                        patch.object(parallel.connections, "close_all") as close:
                    with self.assertRaises(error):
                        parallel.alongside(lambda: preparation.read("경기 후 숙박", "JAMSIL"), interpret)
                    expected = ["collection_exists", "get_collection", "scroll"]
                    self.assertEqual(calls, expected[:expected.index(boundary) + 1])
                    nearby.assert_not_called()
                    close.assert_called_once()

    def test_cancelled_scan_does_not_yield_buffered_rows_or_request_next_page(self):
        cancelled = Event()
        point = SimpleNamespace(id="one", payload={"content": "주차", "metadata": {}})
        db = Mock()
        db.collection_exists.return_value = True
        db.get_collection.return_value = collection_info()
        db.scroll.return_value = ([point, point], "next-page")
        token = chat_runs._cancelled.set(cancelled.is_set)
        try:
            with patch.object(vector_store, "client", return_value=db):
                rows = vector_store.iter_documents()
                self.assertEqual(next(rows)["point_id"], "one")
                cancelled.set()
                with self.assertRaises(chat_runs.Stopped):
                    next(rows)
                db.scroll.assert_called_once()
        finally:
            chat_runs._cancelled.reset(token)

    def test_cancelled_search_does_not_return_provider_results(self):
        cancelled = Event()
        db = Mock()
        db.collection_exists.return_value = True
        db.get_collection.return_value = collection_info()
        db.query_points.side_effect = lambda *a, **kw: (cancelled.set() or SimpleNamespace(points=[]))
        token = chat_runs._cancelled.set(cancelled.is_set)
        try:
            with patch.object(vector_store, "client", return_value=db):
                with self.assertRaises(chat_runs.Stopped):
                    vector_store.search([1.0] + [0.0] * 1535)
                db.query_points.assert_called_once()
        finally:
            chat_runs._cancelled.reset(token)
