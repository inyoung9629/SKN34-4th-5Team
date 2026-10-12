# Multimodal preview chatbot

## Run / disable

Production behavior is unchanged unless `HIGHLIGHT_MULTIMODAL_CORPUS` is a nonempty backend environment variable. Preview uses the existing v2 main agent → `ask_highlight` → multimodal Specialist; no second API, chatbot, UI, database scenes or reembedding. It verifies three fused candidates per delegation: direct Google `gemini-embedding-2` handles all embeddings, while direct Google `gemini-3.8-flash` verifies actual clip video plus separate WAV audio using `generateContent` and `responseJsonSchema`. The shared Google transport replaces the OpenRouter adapter; no verifier retry or provider fallback occurs. Successful decisions record actual response `modelVersion`; HTTP errors expose only sanitized status/reasons and are not automatically attributed to quota exhaustion. The existing card renderer consumes nested `select_highlight_scenes` artifacts; start is floor(original segment offset + verified clip-local seconds), with no guessed lead-in. Evidence and full retrieval/verification diagnostics remain in the checkpoint tool artifact. Errors produce failed tools, never semantic no-match or production fallback.

The isolated runtime override is `/Users/yunseongho/.hermes/runtimes/kbo-preview/override.yml`. It sets `HIGHLIGHT_MULTIMODAL_CORPUS=/highlight-multimodal` and mounts a durable copy at `/Users/yunseongho/.hermes/runtimes/kbo-preview/corpus-v2`. The original experiment corpus remains untouched. Local Qdrant needs writable storage for locking; a corpus-scoped `fcntl.flock` serializes workers, with a fresh SQLite/Qdrant client closed after every request. Do not run the original standalone CLI against the mounted copy concurrently (it does not use the web lock). No new dependencies.

From this worktree, recreate only the preview backend (existing image and source mounts):

```sh
docker compose -p kbo-preview -f docker-compose.yml -f /Users/yunseongho/.hermes/runtimes/kbo-preview/override.yml --profile local up -d --no-build --no-deps --force-recreate backend
```

Use the existing `GOOGLE_API_KEY` for embedding and verification, without editing or printing secrets; OpenRouter credentials are no longer required. To opt out, remove/empty the preview override's `HIGHLIGHT_MULTIMODAL_CORPUS` and recreate the same backend. Do not change `feat-separate-workspace` or run compose down.

Open `http://localhost:8081/chat` anonymously and send `왼손 타자의 홈런 하이라이트 장면 보여줘`. Existing guest flow: POST `/api/v2/chat/sessions/` returns a session and HttpOnly `guest_id` cookie; POST `/api/v2/chat/sessions/<uuid>/messages/` with cookie and JSON `{"content":"왼손 타자의 홈런 하이라이트 장면 보여줘"}` returns SSE. History GET at the same message endpoint includes accepted `videos` cards. Guest ownership, throttling, token reservation, SSE projection and checkpoint persistence are unchanged.

## Direct Google 3.8 verification 2026-10-12

- One bounded actual guest request used the requested KT 7:1 → 9:1 query, session `289235bc-2e4f-44fa-878b-6328deebffa2`. All three persisted provider decisions report actual `model_version=gemini-3.8-flash`; the first (12:1) and third (pregame) candidates were rejected, and the second was accepted with observed two-RBI hit evidence. `complete=true`, `no_match=false`, no provider errors, total specialist latency 18.746 s.
- Original segment offset **646.079 + local 4.0 = 650.079**, floored by the existing renderer to card `SlvDBmr4Bd8` at **650**; SSE completed and public GET history retains that card. This verifies actual API/card/history, not browser player playback or independent baseball ground truth.
- Pinned preview image standalone tests: **9 passed**; focused Django tests: **35 passed**, unique DB `test_mm_direct38_dd8c062f`, no skips. Only preview backend recreated, with no reembedding, secret edits, commits/push or unrelated container changes.
- Evidence report and sanitized request/checkpoint files: `/Users/yunseongho/.hermes/cache/scratch/mm-direct38-dd8c062f/`; guest cookie is private and excluded from report artifacts. Historical runs below are preserved, not evidence for current routing.

## Historical OpenRouter follow-up 2026-10-12 (superseded)

The user subsequently selected direct Google `gemini-3.8-flash`; the following evidence records an earlier run, not the current provider path.

- Official catalog confirms exact `google/gemini-3.5-flash` supports video, audio and structured outputs. Serialization follows [video](https://openrouter.ai/docs/guides/overview/multimodal/videos), [audio](https://openrouter.ai/docs/guides/overview/multimodal/audio) and [structured JSON](https://openrouter.ai/docs/guides/features/structured-outputs) docs. CLI and web share the same separated verifier path; indexing never requires OpenRouter credentials.
- Real guest API request: `KT가 7대1로 앞서다가 안타로 9대1이 되는 하이라이트 영상 보여줘`. Session `e5be28ae-d611-4871-ae99-cae4483ef594`: first candidate `401c8776-67e5-5820-964e-b4d122a7641d` rejected for 12:1; second `ba49bdb9-0b53-50f1-b19b-bac4338adfa7` accepted for visible 7:1 → 9:1 two-RBI hit; third rejected for pregame/0:0. No provider errors. `complete=true`, `no_match=false`; specialist 18.108 s.
- Original segment offset 646.079 + observed local 4.0 = playback 650.079; saved public card is `SlvDBmr4Bd8` at second **650**. SSE completed and GET history persisted that card and answer. This verifies real API selection/card/history, not browser/player playback.
- Sanitized artifacts: `/Users/yunseongho/.hermes/cache/scratch/mm-openrouter-71d9434fc4b6/{session.json,live.sse,history.json,checkpoint-tools.json}`. Private guest cookie file is not a report artifact. Only preview backend recreated; no corpus indexing, env edit, commit/push or unrelated stack changes.
- Final tests: standalone 9 passed; pinned Docker Django suite 35 passed using unique TEST database `test_mm_router_71d9434fc4b6`, including real mounted-corpus concurrent clients. Initial test launcher failed with `KeyError: MIRROR` before DB creation; fixed launcher preserves Django TEST defaults and overrides NAME only.

## Earlier direct-Google run (superseded)

- Modified code: `backend/config/settings.py`, `backend/llm/v2/agent/highlight_sub_agent.py`, `backend/llm/v2/agent/sub_agents.py`; added `backend/llm/tests/test_highlight_multimodal_web.py` and this document. Runtime override changed outside the repo and corpus copied, not reembedded. The three preexisting untracked experiment files were not edited. No commit/push. Only `kbo-preview-backend-1` recreated; unrelated stack left running and unchanged.
- Branch remained `feat/youtube-highlight-scenes`. Configured `upstream` is absent; fetched actual team repository `origin/develop` read-only and found three newer commits (`fc8f29d`, `9d7a448`, `dc350c2`). No automatic integration of unrelated team changes.
- Container: GOOGLE_API_KEY present (value never printed); mounted manifest has 12 segments, Qdrant has 12 points and 36 named vectors. Concurrent request test opens/closes actual mounted local Qdrant twice under the lock; original offset + local time checked.
- Pinned existing Docker image tests: `python manage.py test llm.tests.test_highlight_multimodal_web llm.tests.test_highlights llm.tests.test_chat_v1_v2_dispatch --noinput`: **34 passed**, no skips. Covers opt-in/out, nested existing card contract, timestamp flooring, real guest HTTP SSE/checkpoint/history with mocked provider, malformed verifier response, embedding error, provider error, no-match, concurrency. Standalone experiment tests: `python -I /app/llm/tests/test_highlight_multimodal.py`: **6 passed**. Mocked success is contract evidence, not real video verification.
- **One real bounded browser guest query** went through localhost:8081 frontend → normal application guest API → v2 main agent → actual ask_highlight → real Google query embedding → all three Qdrant rankings + SQLite FTS → actual Gemini video/audio verification. The specialist task was `왼손 타자의 홈런 하이라이트 장면`; chosen candidate `82d482d7-7a54-5210-8fa8-b2374f7319a1`. Retrieval latency 1.740607959 s; verifier latency 9.917820088 s; total specialist latency 11.659162380 s.
- **Actual provider result: `Google generateContent HTTP 429`** after the existing single retry. Saved `ask_highlight` and nested selection tools have `status=error`; diagnostics have `complete=false`, `no_match=null`, `matches=[]`. Query embedding succeeded; video/audio verification did not. No more provider requests sent after quota failure.
- Browser DOM and screenshot verified guest submission and visible `정보 조회 (서브에이전트) 조회 실패`, followed by a failure explanation rather than a claimed match. Real session checkpoint `844ccc29-516d-4847-9103-4395b9930b18` retains exact sanitized provider status. SSE event data was not independently captured by browser response listener; completion/history persistence and failed tool UI were observed. No privileged account created.

## Earlier run blocker (resolved for API verification above)

A real accepted, playable result **was not verified**: Gemini quota HTTP 429 prevented verification, so there is deliberately no video card. Renew provider quota and explicitly authorize another bounded browser query, then confirm actual accepted evidence, original-offset card and player playback. Do not treat passing mocked card tests or successful query embedding as successful end-to-end playback. The unrelated UI already renders YouTube cards; no frontend modifications were necessary.
