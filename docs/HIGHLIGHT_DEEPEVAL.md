# Opt-in multimodal highlight DeepEval sample

This evaluates actual `llm.highlight_multimodal.Specialist.ask` retrieval plus direct Google `gemini-3.8-flash` verification, **not full web/chat E2E**. Production dependencies, runtime settings, model/provider routing, and source corpus are unchanged. No generated source clips, synthetic provider outputs, reembedding or optional judge calls are used. Cases remain sequential; Specialist checks each case's three candidates concurrently with bounded workers and shared generation quota admission.

## Isolated installation and run

From the feature worktree root (do not run Python from downloaded media folders):

```sh
EVAL=/Users/yunseongho/.hermes/cache/scratch/highlight-deepeval
uv venv "$EVAL/venv" --python 3.12
uv pip install --python "$EVAL/venv/bin/python" \
  deepeval==4.2.8 qdrant-client==1.19.1 python-dotenv==1.2.4
"$EVAL/venv/bin/python" -I backend/llm/tests/test_highlight_deepeval.py
"$EVAL/venv/bin/python" -I backend/llm/highlight_deepeval.py \
  --dataset backend/llm/tests/highlight_deepeval_sample.json \
  --corpus /Users/yunseongho/.hermes/runtimes/kbo-preview/corpus-v2 \
  --output "$EVAL/new-run-directory" --env-file .env --run-provider
```

`--run-provider` explicitly opts into paid provider calls. Output must be a new directory. The `.env` is parsed privately for `GOOGLE_API_KEY` only; credentials are not printed or saved. DeepEval telemetry is disabled before import, caching is disabled, no hosted Confident evaluation is configured, and evaluation is sequential. The installed transitive OpenAI SDK belongs only to DeepEval; no OpenAI or judge calls are made.

The runner acquires preview's `web.lock` nonblocking and makes a disposable corpus snapshot, including existing Qdrant vectors, SQLite transcripts and real MP4/WAV clips. It records SHA-256 of every source file, compares copied bytes before opening the copy, and checks source hashes again after evaluation. Live local Qdrant is never opened. Lock contention fails without retry; rerun only after resolving contention. The disposable copy can contain changes made by local Qdrant during reading; the original corpus must remain unchanged.

## Sample label provenance and limitations

The JSON declares 10 queries before provider evaluation: four existing positive references (fence catch, KT 7:1→9:1 hit, KT/Samsung lineup, Lotte/KIA intro), one paraphrase of each, and two negative controls (soccer penalty save, basketball dunk). References bind actual source video and indexed segment UUID. The operator re-inspected the four existing actual reference sheets and read all twelve indexed Korean ASR texts. New actual 1-second contact sheets were extracted from all twelve corpus clips; the remaining eight were inspected to support corpus-scoped negative controls. ASR is noisy and uncorrected.

These are **operator-created weak labels, not independent human truth**. Negative labels assert absence only in the indexed twelve segments, not in full original videos, all KBO highlights, or the world. One-second visual sampling is not exhaustive frame-by-frame annotation. The original-time tolerance is deliberately the full indexed 24-second segment, predeclared in the dataset; this tests offset/bounds, **not exact play-onset accuracy**. Four semantic events and paraphrases are not eight independent events. This is a reproducibility smoke evaluation, not a production quality benchmark.

Existing Graphify graphs were inspected read-only; they describe older highlight components and do not contain the current untracked multimodal module. Implementation contracts were therefore checked directly in the source. No graph regeneration was performed. Team develop's remote tip was checked read-only; this checkout has no `upstream` remote, so no branch/reset/merge was attempted.

## Deterministic DeepEval metrics

`CorpusMetric` subclasses real DeepEval `BaseMetric`, receives real `LLMTestCase` instances and executes through `deepeval.evaluate` with synchronous execution. No semantic LLM judge is needed or used.

- **recall_at_3:** labelled positive segment appears in the actual three fused candidates. Query embedding failure is inconclusive; verifier errors do not erase already-observed retrieval recall.
- **final_selection:** a completed positive result contains at least one accepted match and **every** accepted match binds a labelled video+segment. A completed negative must return no matches. This assesses the complete returned set, not an invented top-one selection.
- **original_time:** positive matches must pass selection and their validated original playback seconds must lie in the predeclared closed interval. Production `decision` validation binds candidate identity, finite local time and evidence; malformed or forged outputs remain inconclusive.
- **no_match:** correct abstention on labelled negative controls, denominator restricted to completed negative cases (negative abstention accuracy).
- **no_match_precision** in summary: completed, valid negative abstentions / all completed, valid predicted abstentions across labelled positive and negative cases. Provider/malformed errors are excluded rather than counted as abstentions. This is precision only on this weak-labelled sample, not global production precision. Summary also exposes all-case final correctness.

Provider errors and malformed output never count as successful abstention. Inconclusive/inapplicable entries are `null` and excluded from each metric denominator; API error case counts remain separate. Actual per-decision `model_version`, rankings, candidates, outputs, accepted matches, retrieval/total latency, and sanitized provider errors are preserved in `raw-rows.json`. Summary includes per-metric numerator/denominator, attempted/planned cases, observed model versions, total case latency, and source integrity. `deepeval-results.json` contains actual framework results.

The bounded pass stops after two observed HTTP 429/503 errors; no runner retries or scheduling. Existing shared Google transport retains its one bounded embedding retry and no verifier retry. Specialist may finish up to three candidates within its current case before the runner halts. Unattempted cases are not scored.
