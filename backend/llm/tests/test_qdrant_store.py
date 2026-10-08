"""Run against a disposable Qdrant URL, never the configured application store.
QDRANT_TEST_URL=http://127.0.0.1:PORT python -m unittest llm.tests.test_qdrant_store
"""
import os
import unittest
import uuid
from unittest.mock import patch

from llm import vector_store as store


@unittest.skipUnless(os.getenv("QDRANT_TEST_URL"), "requires disposable QDRANT_TEST_URL")
class QdrantIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.name = "test_documents_" + uuid.uuid4().hex
        self.env = patch.dict(os.environ, {"QDRANT_URL": os.environ["QDRANT_TEST_URL"],
            "QDRANT_API_KEY": os.getenv("QDRANT_TEST_API_KEY", ""), "QDRANT_DOCUMENT_COLLECTION": self.name,
            "EMBEDDING_MODEL": store.DOCUMENT_MODEL})
        self.env.start()
        store.client.cache_clear()
        self.vector = [1.0] + [0.0] * 1535

    def tearDown(self):
        db = store.client()
        if db.collection_exists(self.name):
            db.delete_collection(self.name)
        db.close()
        store.client.cache_clear()
        self.env.stop()

    def document(self, identifier, stadium=None, category="RULE", content="주차 안내", flag="Y"):
        return {"id": identifier, "content": content, "embedding": self.vector,
                "metadata": {"doc_id": identifier, "stadium_code": stadium, "category": category,
                             "in_stadium_flag": flag, "evidence_type": "OFFICIAL", "status": "CONFIRMED",
                             "metadata": {"updated_at": "2026-10-07"}}}

    def test_filter_upsert_paging_and_substring(self):
        docs = [self.document("common"), self.document("local", "JAMSIL"),
                self.document("other", "MUNHAK"), self.document("outside", "JAMSIL", flag="N")]
        store.upsert_documents(docs)
        store.upsert_documents([self.document("local", "JAMSIL", content="새 주차 안내")])
        self.assertEqual(store.client().count(self.name, exact=True).count, 4)
        self.assertEqual(len(list(store.iter_documents(page_size=1))), 4)
        rows = store.search(self.vector, k=10, stadium="JAMSIL", categories=["RULE"], flag="Y")
        self.assertEqual({r["external_id"] for r in rows}, {"common", "local"})
        strict = store.search(self.vector, stadium="JAMSIL", include_common=False, must_text="새 주차")
        self.assertEqual(strict[0]["external_id"], "local")
        row = store.legacy_row(strict[0])
        self.assertEqual((row["dist"], row["updated_at"], row["stadium"]), (0.0, "2026-10-07", "JAMSIL"))
        store.client().close()
        store.client.cache_clear()
        self.assertEqual(store.client().count(self.name, exact=True).count, 4)

    def test_invalid_vectors_and_model_binding(self):
        for vector in ([1.0], [float("nan")] * 1536, [float("inf")] * 1536, [True] * 1536, [0.0] * 1536):
            doc = self.document("invalid")
            doc["embedding"] = vector
            with self.assertRaises(ValueError):
                store.upsert_documents([doc])
        for identifier in (None, "", "   ", True, False, [], {}, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                store.upsert_documents([self.document(identifier)])
        self.assertFalse(store.client().collection_exists(self.name))
        self.assertEqual(store.point_id(1), store.point_id("1"))
        store.upsert_documents([self.document("valid")])
        store.client().update_collection(self.name, metadata={"embedding_model": "wrong-model"})
        with self.assertRaises(ValueError):
            store.search(self.vector)

    def test_crawler_preserves_existing_vectors_and_provenance(self):
        import django
        django.setup()
        from crawling.common.cron_tving import _vector_upsert
        from langchain_openai import OpenAIEmbeddings
        doc = self.document("crawler", "JAMSIL", "TICKET_POLICY")
        doc["metadata"]["source_file"] = "official.csv"
        store.upsert_documents([doc])
        with patch.object(OpenAIEmbeddings, "embed_documents", side_effect=AssertionError("paid call")):
            result = _vector_upsert([{"doc_id": "crawler", "content": doc["content"],
                                      "metadata": {"category": "TICKET_POLICY"}}])
        self.assertEqual(result, {"new": 0, "updated": 0, "skipped": 1})
        point = store.client().retrieve(self.name, [store.point_id("crawler")], with_vectors=True)[0]
        self.assertEqual(point.payload["metadata"]["source_file"], "official.csv")
        self.assertEqual(point.vector, self.vector)
        with patch.object(OpenAIEmbeddings, "embed_documents", return_value=[self.vector]):
            result = _vector_upsert([{"doc_id": "crawler", "content": "변경된 근거", "metadata": {}}])
        self.assertEqual(result["updated"], 1)
        self.assertEqual(store.client().count(self.name, exact=True).count, 1)

    def test_missing_collection_is_failure(self):
        with self.assertRaises(RuntimeError):
            list(store.iter_documents())

    @unittest.skipUnless(os.getenv("QDRANT_TEST_POSTGRES"), "requires isolated migrated PostgreSQL")
    def test_pg_migration_and_build_preserve_source(self):
        import django
        import numpy as np
        django.setup()
        from django.core.management import call_command
        from llm.models import Document, DocumentChunk
        from llm.management.commands.build_index import Command
        from langchain_openai import OpenAIEmbeddings
        document = Document.objects.create(title=self.name, source=self.name)
        row = DocumentChunk.objects.create(document=document, content="Migration source",
            chunk_index=0, metadata={"doc_id": self.name, "category": "RULE"}, embedding=self.vector)
        try:
            with patch.object(OpenAIEmbeddings, "embed_documents", side_effect=AssertionError("paid call")):
                call_command("migrate_vectors_to_qdrant", batch_size=1, dry_run=True)
                self.assertFalse(store.client().collection_exists(self.name))
                call_command("migrate_vectors_to_qdrant", batch_size=1)
                count = store.client().count(self.name, exact=True).count
                call_command("migrate_vectors_to_qdrant", batch_size=1)
                self.assertEqual(store.client().count(self.name, exact=True).count, count)
                Command().load([{"doc_id": "build", "content": "Built", "metadata": {"doc_id": "build"}}],
                               np.array([self.vector]))
            row.refresh_from_db()
            self.assertEqual(list(row.embedding), self.vector)
            self.assertEqual(row.content, "Migration source")
            point = store.client().retrieve(self.name, [store.point_id(self.name)], with_vectors=True)[0]
            self.assertEqual(point.vector, self.vector)
        finally:
            # Only the explicitly created disposable test row, never production input.
            row.delete()
            document.delete()

    @unittest.skipUnless(os.getenv("QDRANT_TEST_POSTGRES"), "requires isolated migrated PostgreSQL")
    def test_migration_identity_preflight_before_any_writes(self):
        import django
        import io
        django.setup()
        from django.core.management import call_command
        from django.core.management.base import CommandError
        from llm.models import Document, DocumentChunk
        document = Document.objects.create(title=self.name, source=self.name)
        try:
            for ids in ((None, "fallback"), (1, "1"), ("same", "same"),
                        ("valid", "   "), ("valid", []), ("valid", {}),
                        ("valid", True), ("valid", False)):
                with self.subTest(ids=ids):
                    first = DocumentChunk.objects.create(document=document, chunk_index=0,
                        content="first", metadata={"doc_id": ids[0]}, embedding=self.vector)
                    second = DocumentChunk.objects.create(document=document, chunk_index=1,
                        content="second", metadata={"doc_id": f"pg:{first.pk}" if ids[1] == "fallback" else ids[1]},
                        embedding=self.vector)
                    before = list(DocumentChunk.objects.filter(document=document).order_by("pk")
                                  .values("pk", "content", "metadata", "embedding"))
                    try:
                        for dry_run in (True, False):
                            # Resume must still preflight identities in the skipped prefix.
                            for after_id in (0, first.pk):
                                with self.assertRaises(CommandError):
                                    call_command("migrate_vectors_to_qdrant", batch_size=1,
                                                 dry_run=dry_run, after_id=after_id, stdout=io.StringIO())
                                self.assertFalse(store.client().collection_exists(self.name))
                        after = list(DocumentChunk.objects.filter(document=document).order_by("pk")
                                     .values("pk", "content", "metadata", "embedding"))
                        self.assertEqual(repr(before), repr(after))
                    finally:
                        first.delete()
                        second.delete()
        finally:
            document.delete()

    @unittest.skipUnless(os.getenv("QDRANT_TEST_POSTGRES"), "requires isolated migrated PostgreSQL")
    def test_migrated_nullable_legacy_keys(self):
        import django
        import io
        django.setup()
        from django.core.management import call_command
        from llm.models import Document, DocumentChunk
        from llm.v1.rag.club import retrieval
        document = Document.objects.create(title=self.name, source=self.name)
        try:
            for index, metadata in enumerate(({}, {"doc_id": None}, {"doc_id": ""})):
                DocumentChunk.objects.create(document=document, chunk_index=index,
                    content="nullable source", metadata=metadata, embedding=self.vector)
            before = list(DocumentChunk.objects.filter(document=document).order_by("pk")
                          .values("pk", "content", "metadata", "embedding"))
            call_command("migrate_vectors_to_qdrant", batch_size=1, dry_run=True, stdout=io.StringIO())
            self.assertFalse(store.client().collection_exists(self.name))
            for _ in range(2):
                call_command("migrate_vectors_to_qdrant", batch_size=1, stdout=io.StringIO())
                self.assertEqual(store.client().count(self.name, exact=True).count, 3)
            rows, _ = retrieval.search(self.vector, k=5)
            self.assertEqual(len(rows), 3)
            for row in rows:
                self.assertEqual([row[key] for key in ("category", "status", "evidence_type")], [None] * 3)
            self.assertEqual(repr(before), repr(list(DocumentChunk.objects.filter(document=document)
                .order_by("pk").values("pk", "content", "metadata", "embedding"))))
        finally:
            document.delete()

    @unittest.skipUnless(os.getenv("QDRANT_TEST_POSTGRES"), "requires isolated migrated PostgreSQL")
    def test_migrated_scalar_nested_metadata(self):
        import django
        import io
        django.setup()
        from django.core.management import call_command
        from llm.models import Document, DocumentChunk
        from llm.v1.rag.club import retrieval
        document = Document.objects.create(title=self.name, source=self.name)
        try:
            for index, nested in enumerate(("source note", 7, True, ["note"], None,
                                            {}, {"updated_at": "2026-10-06"})):
                metadata = {"doc_id": f"{self.name}:{index}", "category": "RULE",
                            "metadata": nested, "updated_at": "2026-10-07"}
                row = DocumentChunk.objects.create(document=document, chunk_index=index,
                    content="scalar source", metadata=metadata, embedding=self.vector)
                call_command("migrate_vectors_to_qdrant", batch_size=1, stdout=io.StringIO())
                rows, _ = retrieval.search(self.vector, k=10)
                found = next(r for r in rows if r["doc_id"] == metadata["doc_id"])
                self.assertEqual(found["updated_at"], "2026-10-06" if isinstance(nested, dict) and nested else "2026-10-07")
                self.assertEqual(found["metadata"], metadata)
                row.refresh_from_db()
                self.assertEqual(row.metadata, metadata)
                self.assertEqual(list(row.embedding), self.vector)
        finally:
            document.delete()

    def test_active_retrieval_callers(self):
        import django
        django.setup()
        from llm.v1.rag.club import retrieval, structured
        from llm.v1.rag.course import agent, transport
        from llm.tools import knowledge
        store.upsert_documents([self.document("transport", "JAMSIL", "TRANSPORT"),
                                self.document("facility", "JAMSIL", "FACILITY")])
        rows, ms = retrieval.search(self.vector, stadium="JAMSIL", categories=["TRANSPORT"])
        self.assertEqual(rows[0]["doc_id"], "transport")
        self.assertGreaterEqual(ms, 0)
        with patch.object(retrieval, "embed", return_value=self.vector):
            self.assertEqual(knowledge.vector_search("주차", "JAMSIL", ["TRANSPORT"])[0]["metadata"]["doc_id"], "transport")
        self.assertEqual(knowledge.keyword_fallback_search("주차", "JAMSIL", ["TRANSPORT"], "Y", 5)[0]["metadata"]["match_type"], "keyword_fallback")
        self.assertEqual(agent.search_places(self.vector, "JAMSIL", "FACILITY", 5)[0]["doc_id"], "facility")
        self.assertEqual(transport.fetch_rows("JAMSIL")[0]["doc_id"], "transport")
        self.assertEqual(structured._rows("TRANSPORT")[0]["content"], "주차 안내")
