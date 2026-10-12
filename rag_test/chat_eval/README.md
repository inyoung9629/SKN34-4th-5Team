# 챗봇 일괄 평가 문항 (`chat_eval`)

로컬 챗봇에서 한 문항씩 치던 테스트를 명령 하나로 실행하고, 결정적 규칙으로 채점한다.
실제 v2 그래프(모델·도구·DB·Qdrant)를 쓰므로 비용이 든다. 채팅 세션과 크레딧은 쓰지 않는다.

```bat
type rag_test\chat_eval\questions_1012.jsonl | docker compose exec -T backend python manage.py chat_eval --questions - --label A --category 규정
type rag_test\chat_eval\questions_1012.jsonl | docker compose exec -T backend python manage.py chat_eval --questions - --label A
```

결과는 `backend/artifacts/chat_eval/<label>_<시각>.jsonl`에 저장된다. 문항마다 도구 호출, 도구 결과 앞부분, 답변, 영상 카드, 토큰, 지연, 실패 사유가 들어간다.

## 문항 (`questions_1012.jsonl`, 59개)

| 카테고리 | 수 | 출처 | 보는 것 |
|---|---|---|---|
| 규정 | 8 | `rag_test/golden/golden_rule.jsonl` | RAG 문서 검색 호출, 필수 키워드(`must`) |
| 구장 | 8 | `golden_venue.jsonl`, PR #79 평가셋 | 구장 도구·RAG 호출, 필수 키워드, 대상이 불명확하면 되묻기 |
| 먹거리 | 7 | `golden_venue.jsonl`, PR #79 | 구장 내부 먹거리, 주변 장소, 복합 요청 |
| 루트 | 6 | PR #79 | 길찾기, 코스 생성·수정(멀티턴), 숙소, 대상이 불명확할 때 조회 없이 되묻기 |
| 일정 | 7 | PR #79 | `get_games` 호출, 기간, 일정+날씨, 앞 대화 이어받기 |
| 기록 | 8 | `golden_club.jsonl`, PR #79 | 순위·선수 도구, 틀린 전제에 맞장구치지 않기 |
| 하이라이트 | 10 | 신규(#67) | `ask_highlight` 호출, 영상 카드, 답변 URL 금지, 없는 장면을 지어내지 않기 |
| 가드 | 5 | PR #79 | 비도메인·공격·혼합 요청 거절 |

- 채점 기준은 PR #79 `EVALUATION_CRITERIA.md`를 따른다.
  - 필수와 금지를 나눈다.
  - 대상이 불명확하면 조회 없이 되묻는다.
  - 비도메인 작업이 섞인 요청은 전체를 거절한다.
  - 지어내지 않는다.
- 하이라이트 정답은 장면 ID가 아니라 조건(선수·이벤트·날짜)으로 쓴다. 다시 수집하면 장면 ID가 바뀌기 때문이다.
- `note`는 규칙으로 판정할 수 없는 의미 확인 항목이다. 콘솔에 `확인:`으로 출력되며 사람이 본다.
- 골든셋 v28(날씨)은 v1 기준 `refuse`로 남아 있다. v2에는 weather capability가 있어서 이 문항 파일에서는 답변(`get_weather`)을 기대한다. 골든셋 파일 자체는 `eval_retrieval`·`exp_chunking`이 함께 쓰므로 바꾸지 않았다.

## 필드

| 필드 | 의미 |
|---|---|
| `turns` | 질문 목록. 마지막 질문을 채점하고, 앞 질문은 같은 대화로 먼저 실행한다 |
| `expect` | `answer` / `clarify`(조회 없이 되묻기) / `refuse` / `no_lookup` / `any` |
| `must` | `[["a","b"],["c"]]` = (a 또는 b) 그리고 c. 공백을 무시한다 |
| `forbid` | 답변에 있으면 실패하는 표현 |
| `tools_any` | 이 중 하나 이상 호출 |
| `tools_all` | 묶음마다 하나 이상 호출 |
| `tools_none` | 호출하면 실패 |
| `no_url` | 답변 본문에 URL 금지 |
| `videos` | 영상 카드 필수(`required`) 또는 금지(`none`) |
| `max_videos` | 영상 카드 최대 개수 |
| `note` | 사람이 확인할 의미 항목 |
| `src` | 출처 |

## A/B

같은 문항 파일을 설정만 바꿔 `--label`을 달리해서 실행한 뒤, 두 결과 파일의 문항별 통과·실패를 비교한다. 예: `.env`의 `RAG_SEARCH_MODE=vector`와 `rerank`.

## 한계

- 규칙 채점은 키워드·도구·형식만 본다. 의미 정확성과 답변 충실도(답변 주장이 검색 문서에 있는지)는 결과 파일의 `tool_results`와 `answer`로 별도 평가한다(DeepEval Faithfulness 예정).
- 앞 턴은 질문과 최종 답만 대화 기록으로 넘긴다. 실제 서비스처럼 도구 메시지까지 넘기지는 않는다.
- LLM 출력은 실행마다 달라질 수 있다. 차이를 판단하려면 같은 문항을 2~3회 실행한다.
