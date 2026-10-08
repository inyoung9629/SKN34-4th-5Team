# Qdrant document-store handoff

Scope: existing PostgreSQL pgvector documents → Qdrant only. OpenAI `text-embedding-3-small`, 1536 dimensions, cosine distance and metadata/provenance remain unchanged. No Gemini, YouTube import, multimodal schema or rank fusion. PostgreSQL models/migrations/tables/data remain intact as rollback input. This is implementation evidence, not final independent acceptance.

## Operational cutover (operator approval required)

1. Back up PostgreSQL and stop/pause crawler vector writes during the copy/cutover window. Do not remove any existing tables or volumes.
2. Install `backend/requirements.txt`. In the operator-owned environment set a generated `QDRANT_API_KEY`, `QDRANT_URL=http://qdrant:6333`, `QDRANT_DOCUMENT_COLLECTION=kbo_documents_openai`, and `EMBEDDING_MODEL=text-embedding-3-small`. Do not put secrets in version control. Base and prod Compose include persistent authenticated Qdrant with **no host ports**. Local/dev overlays inherit base. No separate chat-worker service exists in these files; any externally managed worker must receive those same four variables.
3. Start only the approved Qdrant service using the deployment's existing Compose file set/project name. Run migration from a backend one-off process with the new dependency/configuration; do not expose the new application to traffic until copy/check succeeds.
4. Validate and copy existing embeddings without paid reembedding:
   ```sh
   python manage.py migrate_vectors_to_qdrant --dry-run --batch-size 256
   python manage.py migrate_vectors_to_qdrant --batch-size 256
   python manage.py check_index
   python manage.py rag_check
   ```
   Before any target write (including collection creation), migration preflights the full source's exact deterministic point UUIDs, even with `--after-id`, rejecting duplicate identities including `pg:<pk>` fallback collisions and number/string collisions such as `1` and `"1"`. Missing/null/empty-string `doc_id` uses `pg:<pk>`; other IDs must be nonblank strings or finite numbers (not booleans, arrays or objects). Dry-run uses the same identity contract. Preflight keeps one identity per source row in memory; pause source writers to keep the preflight/copy source stable. Copy uses primary-key keyset paging and validates finite/nonzero 1536 vectors per batch; a later vector/storage failure can leave earlier target batches copied. A completed page prints `after-id`; rerun from the beginning idempotently or resume with `--after-id N`. Source rows are never modified/deleted.
5. Compare source/target unique IDs, counts and sample provenance, then switch/restart backend and any external chat worker together. `rag_check` is read-only by default; `--invoke` makes paid embedding/LLM calls and was not run here. Run approved smoke queries after cutover.
6. Retain PostgreSQL backup/source and old application version for rollback. Switching the old application back requires coordinated writer/crawler rollback; new Qdrant-only writes are not mirrored to PostgreSQL. Back up `qdrant_data` using Qdrant snapshot/storage procedures before production rollout.

`build_index` now upserts Qdrant only and never clears SQL or the collection. It still generates paid embeddings when its matching checkpoint is unavailable; use the migration command for existing vectors. Removed/renamed source documents remain until an operator explicitly reconciles obsolete IDs; there is no delete-all default. Checkpoint fingerprints include model/dimension/content, but preexisting advisory A1 remains unfixed: interrupted fingerprint invalidation can relabel a stale `.npy` checkpoint before replacement. Do not treat fingerprint matching alone as checkpoint safety assurance after an interrupted rebuild; review/quarantine affected checkpoints separately before reuse. Incompatible collection metadata/dimension/cosine settings fail explicitly instead of mixing spaces. Storage errors propagate and tool boundaries retain explicit ToolException behavior, but the preexisting assistant initial-retrieval handler logs ordinary retrieval failures and continues with zero documents; not every caller fails closed.

## Active path inventory

- `v1/rag/club/retrieval.search`: used by club agents, assistant initial retrieval and shared `tools/knowledge.search_kbo_rows`; preserves `(rows, elapsed_ms)` and row keys/distance, including nullable `category`, `status`, `evidence_type`. Scalar/list nested metadata uses the top-level `updated_at` fallback without changing stored metadata.
- `tools/knowledge.vector_search` and keyword fallback: venue/place retrieval used by v1/v2 tools; returns content and complete metadata with match type/distance.
- `v1/rag/course/agent.search_places`, `stadium_anchor`, `course/transport.fetch_rows`, and `club/structured._rows`: Qdrant search/scroll now covers non-vector document enumeration as well as vector queries.
- `build_index`, `check_index`, `rag_check`, `langsmith_eval.number_grounded`: load/read/check paths now use Qdrant.
- `crawling/common/cron_tving._vector_upsert`, reused by `cron_ticket`: Qdrant deterministic upserts, bounded existing-point fetch, unchanged-content vector reuse and provenance preservation. Ticket cleanup no longer deletes SQL vector rows.
- SQL `Document`/`DocumentChunk` models and initial pgvector migration are retained; only the copy command reads them. `rag_test/` remains legacy offline PG experimentation, not application retrieval; it was not converted or run against live services. `tools/place_rag` uses separate relational place knowledge and is unaffected.

Exact substring/keyword compatibility uses bounded-page scans rather than speculative text-search dependencies. Memory remains bounded to top-k/batch candidates; add an indexed text strategy only if real corpus latency warrants it. Scroll enumeration returns all matching documents, not a first-page truncation.

## Reproducible checks (disposable services only)

Use Python 3.14 and pinned requirements. Create distinct disposable Qdrant v1.19.2 and pgvector/pgvector:pg18 containers; bind test ports to loopback only. Set `DB_HOST=127.0.0.1`, the disposable mapped `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, and a disposable `QDRANT_TEST_URL` (never your application URL). Migrate that fresh database first:

```sh
python backend/manage.py check
python backend/manage.py migrate --noinput
PYTHONPATH=backend DJANGO_SETTINGS_MODULE=config.settings OPENAI_API_KEY=isolated-no-api \
  QDRANT_TEST_POSTGRES=1 python -m unittest llm.tests.test_qdrant_store -v
PYTHONPATH=backend OPENAI_API_KEY=isolated-no-api python backend/manage.py test \
  llm.tests.test_tool_packages llm.tests.test_domain_tools llm.tests.test_rag_domain_bindings \
  llm.tests.test_course_candidates llm.tests.test_course_grounding \
  llm.tests.test_v1_tool_migration llm.v2.tests.test_migrated_tools --noinput
```

Provider outputs are mocked/guarded; real model quality is untested and no paid provider API calls were authorized. Qdrant/PostgreSQL storage/query/copy evidence uses real isolated servers, not mocked storage. The integration tests delete only their randomly named test collections and explicitly created disposable PG rows.
