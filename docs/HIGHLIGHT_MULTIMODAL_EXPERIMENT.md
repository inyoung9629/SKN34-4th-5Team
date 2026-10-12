# Opt-in multimodal highlight experiment

This standalone callable `llm.highlight_multimodal.Specialist` does not import Django, change scene tables, or contact production Qdrant. No generated scene descriptions are indexed. Requires existing `qdrant-client`, `python-dotenv`, ffmpeg/ffprobe and a direct Google `GOOGLE_API_KEY` for both embedding and verification; no added dependencies. Use isolated Python (`-I`) because `backend/llm/enum.py` shadows the standard library when executing a file directly without isolation.

## Input and execution

Create a JSON manifest of 2–3 legally accessible local originals with real WebVTT subtitles:

```json
[{"video_id":"SlvDBmr4Bd8","media":"/absolute/original.mp4","transcript":"/absolute/original.ko.vtt","provenance":"Official KBO YouTube; ko automatic captions; download date and method"}]
```

The example shows one entry; supply two or three. Media must contain its original synchronized audio. Provenance is operator-supplied; the module cannot prove supplied media belongs to a given YouTube ID. Never accept this manifest from a model or an untrusted public endpoint.

```sh
PY=/path/to/existing/venv/bin/python
$PY -I backend/llm/highlight_multimodal.py --workspace /absolute/disposable/corpus --env-file .env index /absolute/manifest.json --segments 6
$PY -I backend/llm/highlight_multimodal.py --workspace /absolute/disposable/corpus --env-file .env ask '왼손 타자의 홈런' --candidates 3
$PY -I backend/llm/tests/test_highlight_multimodal.py
```

Use a **fresh disposable workspace** to index, outside production data. Indexing refuses an existing corpus instead of overwriting it. A failed partial index requires a new workspace; no deletion/retry service is provided. Saved `segments.json`, `embedding_probe.json`, Qdrant and SQLite are local artifacts. The operator can run `ask` after process restart (persistence readback). Credentials are read only in memory and never included in artifacts; supplying video/audio to direct Google for embedding and Gemini verification is an external provider transfer and should be authorized.

## Actual flow and ceilings

- Parse real VTT cue times, strip markup, bound captions to original duration. Final ASR cue may extend up to two seconds past EOF and is clipped; larger errors fail.
- Select at most 24 evenly sampled transcript anchors per video (default six), build nonoverlapping 24-second clips and select overlapping actual transcript cues. This is **sampled coverage, not full-game recall**; a play can cross a segment boundary or be absent entirely.
- Extract video without audio, and synchronized mono WAV separately; verify extracted duration within 250 ms. Video under 32 seconds stays below Google's 120-second/32-frame embedding limits; audio below 180 seconds. Inline files capped at 12 MB each.
- Independently call direct Google `gemini-embedding-2` for raw video, raw audio and transcript. Validate 768-dimensional finite nonzero vectors. Store three separate named vectors with identical UUID segment IDs and original offsets; read all three back.
- Embed the text query once in the shared model space, then independently search all three vector names. SQLite FTS5 searches literal transcript tokens; RRF fuses four rankings and suppresses overlapping segments. Korean FTS is token-based, not a Korean morphological analyzer.
- Bounded specialist watches up to five actual clips plus synchronized audio (default three), with at most **three concurrent verification workers** and no tool loops. Query embedding, Qdrant and SQLite retrieval remain on the calling thread; every candidate is checked and decisions/matches preserve fused candidate order, not completion order. `verification_latency` measures verification wall time; candidate `latency` includes token counting/admission/provider time, with `quota_wait` and `provider_latency` separately recorded. Google model-version state is thread-local and reset for every invocation; the returned audit version belongs to that candidate. Direct Google `gemini-3.8-flash` is the configurable verifier default (`--verifier`); shared Google transport sends actual video and separate synchronized WAV as two `inlineData` parts to `generateContent`, with `responseMimeType=application/json` and `responseJsonSchema`. Embedding calls remain on direct Google `gemini-embedding-2`, including the query. No OpenRouter adapter or provider fallback exists. Successful decisions retain the provider's actual `modelVersion` for audit. Prompt asks for observed evidence, rejects uncertainty, and provides clip-local duration. Candidate IDs, booleans, finite local times and evidence length are server-validated; original playback is computed exclusively from stored offset. URLs/files never come from model output. Malformed output rejects that candidate as an unknown judgment, not a semantic mismatch. Both malformed-output rejections and provider errors remain explicit per candidate and mark `complete=false` and `no_match=null`; only valid completed judgments with no accepted candidates yield `no_match=true`. Direct Google embedding HTTP 429/503 gets at most one retry (two-second delay); Google verification errors are reported without retry or provider fallback, including sanitized status/reason metadata when present; HTTP 429 alone does not establish quota exhaustion.
- ASR names can be wrong; video sampling can miss rapid events; generation can hallucinate despite the prompt. Verifier decisions are not independent ground truth. No accuracy promise is supported by offline mocks.

## Verification quota admission

`Google.verify` calls actual multimodal `countTokens` with the complete generation request (prompt, video, separate audio and schema), then atomically reserves all generation budgets before `generateContent`. It never estimates tokens from text length. Defaults are RPM **900**, TPM **1,800,000 input tokens**, RPD **10,000**; `HIGHLIGHT_QUOTA_RPM`, `HIGHLIGHT_QUOTA_TPM`, `HIGHLIGHT_QUOTA_RPD` can lower these limits. RPM bucket capacity is three requests (or RPM if smaller); TPM capacity is `min(TPM, 180000)` tokens. This intentionally small token burst leaves headroom below the supplied 2,000,000 TPM limit even across a full rolling minute at the default refill rate. Oversized requests fail immediately. Admission waits at most `HIGHLIGHT_QUOTA_MAX_WAIT` seconds (default 10, configurable 0–60); exhaustion is an explicit incomplete result, never no-match. Failed generation attempts remain spent; no generation retry/refund is introduced.

SQLite `BEGIN IMMEDIATE` coordinates threads and processes, and committed counts survive restarts. All participating workers/CLI instances must share `HIGHLIGHT_QUOTA_PATH` (default `~/.cache/kbo-highlight/verification-quota.sqlite3`) on a local shared filesystem. The file stores model, budget counters and timestamps only, not credentials/media/query. Use one file per Google project with matching configuration; separate files, other hosts/containers without a shared mount, other clients and deleted state are **not** covered. The existing web `web.lock` still serializes whole requests around local Qdrant; internal candidate checks alone are parallel. No runtime mount or deployment was changed.

RPD is a calendar counter resetting at midnight `America/Los_Angeles`, not a refilled daily bucket. Clock rollback does not reset it early. First creation cannot reconstruct requests already made elsewhere that day. These application admissions cover **generation** only: query embeddings and `countTokens` use separate provider operations and are not presented as project-global quota enforcement. Provider limits still apply and errors stay visible. Counting failures do not send generation. Token bucket smoothing is not a replacement for provider rolling-window enforcement or a billing budget.

Official semantics: [Google rate limits](https://ai.google.dev/gemini-api/docs/rate-limits), [multimodal tokens](https://ai.google.dev/gemini-api/docs/tokens), [countTokens API](https://ai.google.dev/api/tokens).

Production `highlights.analyze → clean_scenes → replace_scenes`, `highlight_vectors.index_video → scene_text/embed`, existing tools and v2 highlight agent remain unchanged.
