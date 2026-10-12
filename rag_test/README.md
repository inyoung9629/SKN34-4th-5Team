# RAG 평가

기존 `eval_retrieval`, `step4_eval_retrieval.py`, `step5_eval_generation.py`는 그대로 유지합니다.
Ragas는 운영 의존성(OpenAI/jiter)과 충돌하므로 **검색·생성 export와 점수 평가를 별도 Python 환경으로 분리**합니다. 운영 requirements/이미지에는 설치하지 않습니다.

## 설치와 실행

저장소 루트에서 Python 3.12 환경을 각각 준비합니다. 기존 backend 환경이 있으면 첫 두 줄은 생략합니다.

```sh
python3.12 -m venv .venv-backend
.venv-backend/bin/pip install -r backend/requirements.txt
python3.12 -m venv .venv-ragas
.venv-ragas/bin/pip install -r rag_test/requirements-ragas.txt
export RAGAS_DO_NOT_TRACK=true
# OPENAI_API_KEY: 생성·검색 임베딩·Ragas judge/embedding
# OPENROUTER_API_KEY: 기존 rerank helper. 키는 환경으로만 공급하고 저장하지 않습니다.
# backend Qdrant 등 기존 환경설정도 필요합니다.
cat rag_test/golden/golden_rule.jsonl rag_test/golden/golden_venue.jsonl | \
  .venv-backend/bin/python backend/manage.py export_ragas --ids r16,r25,v04 \
  --limit 3 --k 5 --mode rerank --generation-model gpt-4.1-mini \
  --output /tmp/ragas-export.jsonl
.venv-ragas/bin/python rag_test/eval_ragas.py --input /tmp/ragas-export.jsonl \
  --limit 3 --evaluation-model gpt-4.1-mini --output /tmp/ragas-scores.json
```

**두 단계 모두 실제 유료 API 호출이 발생합니다.** export는 질문 임베딩·기존 검색/리랭커·문항당 답변 생성 1회, 평가는 문맥/문장 수에 따라 여러 judge 호출 및 임베딩을 수행합니다. 생성/평가 모델은 독립 설정입니다. 클라이언트 timeout 30초, HTTP retry 0회, judge 구조화 retry 최대 1회(단일 시도), 지표당 timeout 120초로 제한합니다. 리랭커는 기존 helper의 제한/실패 fallback을 따르므로 `mode=rerank`가 리랭커 성공 자체를 보증하지는 않습니다.

출력은 기본 `artifacts/rag_eval/`의 마이크로초 타임스탬프 경로이며 명시한 파일이 이미 존재하면 API 호출 전에 거부합니다. 기존 export로 점수만 다시 계산할 수 있어 검색/생성 재호출이 필요 없습니다. 오류 시 부분 출력과 nonzero exit를 확인해야 합니다. API 오류는 예외 타입만 기록하며 provider headers, 키, client 객체, 원문 예외를 저장하지 않습니다.

## 입력과 출력

export는 stdin 또는 `--golden file.jsonl`을 받습니다. 기본 limit=3, k=5(1..50), mode=rerank이며 mode는 `knowledge.SEARCH_MODES`만 허용합니다. `--ids`는 쉼표로 구분한 명시 선택입니다. 선택 문항에 reference가 없으면 실패합니다. 미선택 기존 문항에 reference 필드가 없으면 skip ID/count를 출력하고 metadata에 저장합니다. 빈 reference/잘못된 타입/중복 ID/빈 dataset은 유료 호출 전에 거부합니다.

기존 골든 JSONL에 다음 필드를 추가합니다. `reference`는 실제 근거에서 작성한 정답문장이며 키워드 join이나 생성 답변으로 대체하지 않습니다. `reference_doc_ids`는 근거 provenance이며 검색 필터에 쓰지 않습니다.

```json
{"id":"r25","question":"준플레이오프는 몇 선승제야?","reference":"KBO 준플레이오프는 3선승제입니다.","reference_doc_ids":["RULE_COMMON_LR_제34조"]}
```

export JSONL은 `id/question/user_input/reference/reference_doc_ids/response/retrieved_contexts/retrieved_doc_ids/metadata/usage/errors`를 담습니다. 문서별 `retrieved_contexts`는 서비스 `format_documents`의 등급/구장/분류 header와 본문을 포함하며, **동일 byte 내용을 join해서 생성 프롬프트에 넣고 평가에 그대로 전달**합니다. 문서 내용을 절단하지 않고 k만 제한합니다. gold/reference/stadium 정답 필드는 검색에 주입하지 않으며 질문의 detect_stadium/detect_categories 규칙만 사용합니다. corpus embedding 모델 metadata는 수정하지 않습니다.

평가 JSON은 Ragas 버전·모델·dataset·export 설정, 문항별 정답/응답/문맥/검색 ID/4개 점수/오류 및 summary를 저장합니다. 생성 토큰 usage가 제공되면 export에 저장합니다. Ragas 0.4.3 `MetricResult`는 value/reason/traces만 제공하므로 평가 및 검색 token usage는 `unknown`으로 명시합니다. 점수 실패/NaN/범위 밖은 null과 오류로 남기며 평균은 유효한 값만 사용하므로 successful/failed도 함께 확인합니다.

- ContextPrecision: reference 관련 문맥이 상위 검색 순서에 얼마나 정밀하게 배치되었는지 (0..1)
- ContextRecall: reference의 내용을 검색 문맥이 얼마나 뒷받침하는지 (0..1)
- Faithfulness: 생성 응답의 주장이 제공 문맥에 근거하는지 (0..1)
- AnswerRelevancy: 응답에서 재생성한 질문과 원 질문의 embedding cosine 유사도 (-1..1)

현재 reference는 source 검증된 r16·r25·v04 세 문항에만 있습니다. v04는 저장소 `data/preprocessed/구장교통정보.csv`(2026-09-03 확인) 기준이며 최신 웹 교통정보를 보증하지 않습니다.

## 한계와 무료 회귀

이는 실제 `search_kbo_rows`(sparse/rerank 경로) + 서비스 `build_prompt`를 통한 **단일 생성 평가**입니다. 실제 전체 챗봇 응답/agent 도구루프/외부 리서치/브라우저/DB·지갑 쓰기/채팅 세션 E2E 평가가 아닙니다. 3문항은 smoke 평가이며 전체 성능이나 통계적 유의성을 보증하지 않습니다.

```sh
.venv-backend/bin/python backend/manage.py test llm.tests.test_eval_ragas
```

회귀는 mock으로 네트워크 없이 검증합니다. 실제 Ragas API 호환 smoke는 별도 평가 환경에서 HTTP MockTransport를 사용하며 실제 제공자 호출과 구별합니다.
