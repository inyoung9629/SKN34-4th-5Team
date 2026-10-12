"""PR78 회귀: 실제 메모리 Qdrant, 외부 호출은 가짜 응답, Compose는 placeholder만 사용."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from unittest.mock import Mock, patch

from django.test import SimpleTestCase
from qdrant_client import QdrantClient

from llm import reranker, sparse_index, vector_store
from llm.service import usage
from llm.tests.test_hybrid_search import DOCS, vec
from llm.tools import knowledge


class SparseMutationTest(SimpleTestCase):
    def setUp(self):
        self.db = QdrantClient(":memory:")
        self.addCleanup(self.db.close)
        env = patch.dict(os.environ, {"QDRANT_DOCUMENT_COLLECTION": "owned_dense",
                                      "QDRANT_SPARSE_COLLECTION": "owned_sparse"})
        env.start()
        self.addCleanup(env.stop)
        client = patch("llm.vector_store.client", return_value=self.db)
        client.start()
        self.addCleanup(client.stop)
        vector_store.upsert_documents(DOCS)

    def test_collision_refuses_rebuild_recreate_upsert_and_tokenless_delete(self):
        name = vector_store.collection_name()
        before = self.db.scroll(name, with_vectors=True, limit=100)[0]
        payload = {"point_id": before[0].id, "content": "", "metadata": {}}
        with patch.dict(os.environ, {"QDRANT_SPARSE_COLLECTION": name}):
            for action in (lambda: sparse_index.build_from_documents(report=lambda _: None),
                           lambda: sparse_index.recreate(200),
                           lambda: sparse_index.upsert_payloads([payload]),
                           lambda: sparse_index.upsert_payloads([{**payload, "content": "보크"}])):
                with self.subTest(action=action), self.assertRaisesRegex(ValueError, "different names"):
                    action()
                self.assertEqual(self.db.count(name).count, len(DOCS))
                self.assertEqual(self.db.scroll(name, with_vectors=True, limit=100)[0], before)

    def test_tokenless_dense_updates_remove_sparse_points(self):
        for content in ("", " \n\t", "!?...", "알려줘 뭐야 어디"):
            with self.subTest(content=content):
                sparse_index.build_from_documents(report=lambda _: None)
                doc = {**DOCS[0], "content": content, "embedding": vec(8)}
                self.assertEqual(vector_store.upsert_documents([doc]), 1)
                ident = vector_store.point_id(doc["id"])
                self.assertEqual(self.db.retrieve(sparse_index.collection_name(), [ident]), [])
                dense = self.db.retrieve(vector_store.collection_name(), [ident], with_vectors=True)[0]
                self.assertEqual(dense.payload["content"], content)
                self.assertEqual(dense.vector, vec(8))
                self.assertEqual(self.db.count(vector_store.collection_name()).count, len(DOCS))
                vector_store.upsert_documents([DOCS[0]])

    def test_mixed_batch_counts_only_nonempty_points_and_deletes_are_idempotent(self):
        sparse_index.build_from_documents(report=lambda _: None)
        docs = [{"point_id": vector_store.point_id(d["id"]), "content": d["content"], "metadata": d["metadata"]}
                for d in DOCS]
        docs[0]["content"] = ""
        missing = {"point_id": vector_store.point_id("missing"), "content": ""}
        self.assertEqual(sparse_index.upsert_payloads(docs + [missing]), 2)
        self.assertEqual(self.db.count(sparse_index.collection_name()).count, 2)
        self.assertEqual(sparse_index.upsert_payloads([docs[0], missing, missing]), 0)
        self.assertEqual(self.db.count(sparse_index.collection_name()).count, 2)

    def test_duplicate_ids_follow_last_update_in_both_orders(self):
        sparse_index.build_from_documents(report=lambda _: None)
        valid = {**DOCS[0], "content": "새 보크 규정", "embedding": vec(9)}
        empty = {**DOCS[0], "content": "", "embedding": vec(8)}
        ident = vector_store.point_id(valid["id"])
        for batch in ([valid, empty], [empty, valid]):
            with self.subTest(contents=[d["content"] for d in batch]):
                vector_store.upsert_documents(batch)
                dense = self.db.retrieve(vector_store.collection_name(), [ident], with_vectors=True)[0]
                sparse = self.db.retrieve(sparse_index.collection_name(), [ident])
                self.assertEqual(dense.payload["content"], batch[-1]["content"])
                self.assertEqual(dense.vector, batch[-1]["embedding"])
                self.assertEqual(bool(sparse), bool(batch[-1]["content"]))
                if sparse:
                    self.assertEqual(sparse[0].payload["content"], batch[-1]["content"])
        payload = {"point_id": ident, "content": "보크"}
        self.assertEqual(sparse_index.upsert_payloads([payload, {**payload, "content": ""}]), 0)
        self.assertEqual(sparse_index.upsert_payloads([{**payload, "content": ""}, payload]), 1)


class RerankerResponseTest(SimpleTestCase):
    rows = [{"doc_id": "a", "content": "보크"}, {"doc_id": "b", "content": "인필드 플라이"}]
    results = [{"index": 1, "relevance_score": 0.9}, {"index": 0, "relevance_score": 0.2}]

    def setUp(self):
        env = patch.dict(os.environ, {"OPENROUTER_API_KEY": "dummy-not-a-provider-key"})
        env.start()
        self.addCleanup(env.stop)
        self.meter = usage.Meter()
        token = usage._meter.set(self.meter)
        self.addCleanup(usage._meter.reset, token)
        costs = list(reranker.COSTS)
        self.addCleanup(lambda: reranker.COSTS.__setitem__(slice(None), costs))

    def test_normal_mapping_top_n_and_exact_once_known_usage(self):
        ranked = reranker.rerank("q", self.rows, 1, _http=lambda *_: {
            "results": self.results, "usage": {"total_tokens": 150, "cost": "0.001"}})
        self.assertEqual(ranked, [{**self.rows[1], "rerank_score": 0.9}])
        self.assertEqual(reranker.COSTS[-1][1], 0.001)
        self.assertEqual(self.meter.totals(), (150, 0, 1, 0))

    def test_malformed_results_keep_fallback_and_known_usage(self):
        malformed = [{}, {"results": None}, {"results": {}}, {"results": "bad"},
                     {"results": [None]}, {"results": [[]]}, {"results": [{}]}]
        for index in (True, False, -1, 2, "0", None):
            malformed.append({"results": [{"index": index, "relevance_score": 0.9}]})
        for score in (None, True, "bad", {}, [], float("nan"), float("inf")):
            malformed.append({"results": [{"index": 0, "relevance_score": score}]})
        malformed.append({"results": [self.results[0], None]})
        fallback = list(reversed(self.rows))
        for response in malformed:
            with self.subTest(response=response):
                meter = usage.Meter()
                with patch.object(usage, "_meter") as context:
                    context.get.return_value = meter
                    http = Mock(return_value={**response, "usage": {"total_tokens": 150}})
                    real = reranker.rerank
                    with patch.object(reranker, "rerank", side_effect=lambda q, rows, k: real(q, rows, k, _http=http)):
                        self.assertEqual(knowledge.model_rerank("q", self.rows, 2, fallback), fallback)
                    self.assertEqual(meter.totals(), (150, 0, 1, 0))
                    http.assert_called_once()

    def test_transport_json_and_nonobject_responses_count_unknown_once(self):
        for response in (None, [], "bad", 4, {"results": self.results},
                         {"results": self.results, "usage": "bad"},
                         {"results": self.results, "usage": {"total_tokens": True}},
                         {"results": self.results, "usage": {"total_tokens": -1}},
                         {"results": self.results, "usage": {"total_tokens": "150"}},
                         TimeoutError("slow"), OSError("transport"), ValueError("JSON")):
            with self.subTest(response=response):
                meter = usage.Meter()
                with patch.object(usage, "_meter") as context:
                    context.get.return_value = meter
                    http = Mock(side_effect=response) if isinstance(response, Exception) else Mock(return_value=response)
                    ranked = reranker.rerank("q", self.rows, 2, _http=http)
                    if not isinstance(response, dict) or "results" not in response:
                        self.assertIsNone(ranked)
                    self.assertEqual(meter.totals(), (0, 0, 1, 1))
                    http.assert_called_once()

    def test_invalid_optional_cost_does_not_lose_results_or_accounting(self):
        for cost in ("bad", {}, [], True, float("nan"), float("inf"), -1):
            with self.subTest(cost=cost):
                with patch.object(usage, "record_external") as record:
                    ranked = reranker.rerank("q", self.rows, 2, _http=lambda *_: {
                        "results": self.results, "usage": {"total_tokens": 150, "cost": cost}})
                self.assertEqual([r["doc_id"] for r in ranked], ["b", "a"])
                self.assertEqual(reranker.COSTS[-1][1], 0.0)
                record.assert_called_once_with(150, 0)

    def test_no_key_or_candidates_do_not_call_or_record(self):
        http = Mock()
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": ""}):
            self.assertIsNone(reranker.rerank("q", self.rows, 2, _http=http))
        self.assertIsNone(reranker.rerank("q", [], 2, _http=http))
        http.assert_not_called()
        self.assertEqual(self.meter.totals(), (0, 0, 0, 0))

    def test_baseexception_propagates_with_one_attempt_record(self):
        with self.assertRaises(KeyboardInterrupt):
            reranker.rerank("q", self.rows, 2, _http=Mock(side_effect=KeyboardInterrupt))
        self.assertEqual(self.meter.totals(), (0, 0, 1, 1))


class ProductionComposeTest(SimpleTestCase):
    def test_backend_rag_defaults_and_overrides_render_without_real_env(self):
        if not shutil.which("docker"):
            self.skipTest("Docker Compose is unavailable")
        version = subprocess.run(["docker", "compose", "version"], capture_output=True)
        if version.returncode:
            self.skipTest("Docker Compose is unavailable")
        compose = Path(__file__).resolve().parents[3] / "docker-compose.prod.yml"
        defaults = {"OPENROUTER_API_KEY": "", "RAG_SEARCH_MODE": "rerank", "RAG_VENUE_SEARCH_MODE": "hybrid",
                    "RAG_RERANK_MODEL": "voyageai/rerank-2.5", "RAG_RERANK_CANDIDATES": "30",
                    "QDRANT_SPARSE_COLLECTION": "kbo_documents_sparse", "RAG_RRF_K": "60",
                    "RAG_SPARSE_WEIGHT": "0.1", "RAG_SPARSE_RRF_WEIGHT": "1.0"}
        # /dev/null env_file: 저장소 .env와 부모 셸의 비밀값은 읽지 않는다.
        env = {"PATH": os.environ.get("PATH", "")}
        env.update({key: "placeholder" for key in re.findall(r"\$\{([A-Z_]+):\?", compose.read_text())})
        command = ["docker", "compose", "--env-file", os.devnull, "-f", str(compose), "config", "--format", "json"]
        for overrides in ({}, {key: "override-" + key.lower() for key in defaults}):
            with self.subTest(overrides=bool(overrides)):
                rendered = subprocess.run(command, env={**env, **overrides}, capture_output=True, text=True, check=True)
                backend = json.loads(rendered.stdout)["services"]["backend"]["environment"]
                self.assertEqual({key: backend[key] for key in defaults}, {**defaults, **overrides})
