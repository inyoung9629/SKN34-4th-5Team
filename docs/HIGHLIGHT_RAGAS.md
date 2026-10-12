# Ragas replay of actual multimodal highlight outputs

The user changed the evaluation framework requirement to **Ragas** after the finite real-provider pass had completed. `backend/llm/highlight_ragas.py` runs actual Ragas 0.4.3 `EvaluationDataset`, `SingleTurnSample`, custom deterministic `SingleTurnMetric`, and `ragas.evaluate` on the saved real outputs. The prior DeepEval run remains separately named and is not relabelled as Ragas. No additional provider calls or LLM judge calls are needed.

```sh
EVAL=/Users/yunseongho/.hermes/cache/scratch/highlight-deepeval
uv pip install --python "$EVAL/venv/bin/python" \
  ragas==0.4.3 langchain-community==0.4.1
"$EVAL/venv/bin/python" -I backend/llm/highlight_ragas.py \
  --raw-rows "$EVAL/run-20261012/raw-rows.json" \
  --output "$EVAL/new-ragas-replay-directory"
```

Use the isolated evaluation venv from `HIGHLIGHT_DEEPEVAL.md`; both runners share its tested deterministic scoring helper. No production requirements are changed. Pin `langchain-community==0.4.1`: its 0.4.2 removes `chat_models.vertexai`, which Ragas 0.4.3 imports, causing `ModuleNotFoundError` before evaluation. The pin only fixes isolated evaluation dependencies and does not select VertexAI or change the Google provider.

Ragas telemetry and LangSmith tracing are disabled before import. Scoring uses `max_workers=1`, `max_retries=0`, no LLM/embedding metric dependency, and no credentials are loaded by replay. Real retrieved transcript text and context IDs are mapped into samples; the JSON response/reference retain the verified video+segment/time binding for custom metrics. These are task-specific deterministic Ragas metrics, **not** built-in Ragas faithfulness/answer-relevancy or an LLM's semantic assessment.

Completed replay output: `/Users/yunseongho/.hermes/cache/scratch/highlight-deepeval/ragas-replay-20261012/{ragas-results.json,summary.json}`. Actual Ragas results: positive recall@3 **8/8**, final returned-set correctness **10/10**, original time inside predeclared full indexed segment **8/8**, negative abstention accuracy **2/2**, labelled-sample no-match precision **2/2**; **0** API errors and **0** inconclusive cases in the underlying real outputs. Underlying 30 verification decisions report `gemini-3.8-flash`; provider case latency totals 195.746 s, median 19.326 s. Replay makes **zero** new provider/judge calls and does not open the corpus.

All annotation limitations and error-inconclusive semantics in `HIGHLIGHT_DEEPEVAL.md` apply unchanged: weak operator labels, four distinct positive events with paraphrases, only two easy out-of-sport negatives, absence restricted to the twelve indexed clips, full 24-second time tolerance (not exact action onset), and Specialist-only evaluation (not full chat/web E2E). Dataset name retains `deepeval` for provenance; its content is framework-neutral and was declared before the real provider pass. The small offline semantic regression also tests the Ragas adapter on the same bound result.
