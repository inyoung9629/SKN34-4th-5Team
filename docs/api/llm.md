# llm — 회원·게스트 채팅, SSE, 저장과 진행 기록

> 현재 V2의 경기 기준·같은 방 기억·응원팀 조건과 활성 SSE 계약은 [직관 코스 백엔드 1차 명세](course-policy-v1.md)를 참고한다. 아래 본문에는 이전 채팅 구조 설명이 포함되어 있으므로 `/api/v2/chat/sessions/<uuid>/messages/`의 현재 코드와 구분한다.

[에이전트 도구 명세](agent-tools.md)는 챗봇이 내부적으로 사용하는 야구·구장·장소·코스·커뮤니티·문서 검색·읽기 전용 SQL 도구를 설명한다.

현재 [프로젝트 URL 등록](../../backend/config/urls.py), [views.py](../../backend/llm/views.py), [serializers.py](../../backend/llm/serializers.py), [chat_service.py](../../backend/llm/chat_service.py) 기준의 HTTP 계약이다. RAG 구성/인덱싱 상세는 기존 [rag/README.md](../../backend/llm/rag/README.md)를 참고한다. 기존 문서의 예시보다 **현재 URLconf와 실행 코드가 우선**이다. 특히 [rag_views.py](../../backend/llm/rag_views.py)는 파일이 존재해도 URLconf에 등록되지 않았으므로 `POST /chat/`를 공개 API로 사용하면 안 된다.

아래 URL은 Django 직접 경로다. [Nginx](../../nginx/nginx.conf)는 `/api/` 접두어를 제거해 전달한다. 브라우저 호출은 `/api/chat/sessions/` 등이며 Django 직접 호출은 `/chat/sessions/`다.

## API 전체

[인증 설정](../../backend/config/settings.py)은 SimpleJWT(`Authorization: Bearer <access-token>`). 회원 API는 로그인 필요(미인증 401)이고 **항상 현재 회원 소유 세션/turn만** 접근한다. 타인 소유와 없는 자원은 404다. staff/superuser도 다른 사람 채팅에 접근할 수 없다.

| Django URL | 메서드 | 요청/조회 조건 | 정상 응답 |
|---|---|---|---|
| `/chat/sessions/` | GET | 본인만, 별도 필터/페이지네이션 없음 | 200 세션 배열, `updated_at` 내림차순 |
| `/chat/sessions/` | POST | 선택 `title` | 201 세션 |
| `/chat/sessions/<int:session_id>/` | PATCH | 선택 `title` | 200 세션 |
| `/chat/sessions/<int:session_id>/` | DELETE | 본인 | 204 본문 없음 |
| `/chat/sessions/<int:session_id>/messages/` | GET | 본인, 별도 필터/페이지네이션 없음 | 200 메시지 배열, `sequence_no` 오름차순 |
| `/chat/sessions/<int:session_id>/messages/` | POST | `{content}`; Accept로 JSON/SSE 선택 | JSON 201 또는 SSE 200 |
| `/chat/sessions/<int:session_id>/turns/` | GET | 본인, `page` | 200 `{count,next,previous,results}` |
| `/chat/turns/<uuid:turn_id>/finalize/` | POST | 본인, `{receipt,prefix,status}` | 200 확정 결과 |
| `/chat/guest/` | POST | 공개, `{messages:[...]}` | SSE 200 |

세션 상세 **GET/PUT은 없다**(405). 메시지 개별 수정/삭제, turn 개별 조회/재시작, guest finalize도 등록되지 않았다. OPTIONS 등 프레임워크 메서드는 별도다.

### 데이터 형태

- 세션: `id, title, created_at, updated_at`. title 최대 255자, 빈 문자열 허용, 생략 기본값은 `메세지 제목`. 사용자 ID/소유권은 서버에서 설정한다.
- 메시지: `id, sequence_no, role, content, status, created_at, updated_at`. 생성 입력은 `content`만 쓰기 가능, 공백 정리 후 비어 있지 않아야 하며 최대 2,200자. role/status/sequence는 읽기 전용. DB role은 **`human|ai`**(guest 입력의 `user|assistant`와 다름). human status는 빈 문자열, ai status는 `completed|stopped`.
- turn: `id, question, status, base_sequence, human_message_id, assistant_message_id, progress`. status는 `pending|completed|stopped|failed`, 메시지 ID는 아직 저장 전이면 null.
- turn 목록은 생성시각/ID 오름차순, 항상 페이지당 **20개**. 코드에 `max_page_size=50`은 있으나 `page_size_query_param`이 설정되지 않아 `page_size`로 크기를 바꿀 수 없다. DRF `page` 규칙(잘못되거나 없는 페이지 404)을 따른다. [공통 pagination](../../backend/community/pagination.py)이 next/previous를 `/api/...` 상대 URL로 만든다. 따라서 Django 직접 접속에서도 반환 링크는 프록시용 접두어를 포함한다.
- 이 API의 DRF serializer는 strict unknown-key 거부를 공통 적용하지 않는다. 추가 필드로 사용자/role/staff 권한을 바꾸거나 저장 상태를 강제할 수는 없다.

## 회원 JSON 요청: 생성과 저장을 한 번에

```http
POST /chat/sessions/1/messages/
Authorization: Bearer <access-token>
Content-Type: application/json
Accept: application/json

{"content":"잠실 직관 전에 갈 만한 곳 알려줘"}
```

응답 201:

```json
{
  "session_id": 1,
  "user_message": "질문",
  "assistant_message": "답변",
  "status": "completed",
  "user_message_id": 10,
  "assistant_message_id": 11,
  "turn_id": "00000000-0000-0000-0000-000000000001",
  "progress": []
}
```

위 값은 형식 예시이며 실행 결과가 아니다. 코스 답변에는 아래 코스 metadata가 추가될 수 있다.

처리 순서:

1. 소유권/입력 검증 → 세션 잠금 → 현재 마지막 sequence를 `base_sequence`로 갖는 pending turn 생성.
2. 기존 대화를 읽고 **DB 트랜잭션 밖에서** 모델/도구 실행. 회원 progress는 실행 중 DB에 저장한다.
3. 세션/turn을 잠근 뒤 현재 마지막 sequence와 base_sequence 비교. 같으면 human+ai를 원자적으로 저장하고 turn completed/메시지 ID/세션 updated_at 갱신.
4. 그 사이 다른 메시지가 저장되었으면 409. 생성/저장 예외 경로는 pending turn을 failed로 남기며 성공으로 보고하지 않는다.

`OpenAIError`는 안전한 502 `{detail:"LLM 응답 생성에 실패했습니다. 다시 시도해 주세요."}`로 변환된다. 동시 기록 충돌은 409 `{detail:"이 대화에는 더 최신 메시지가 있습니다."}`. DB 오류나 모든 일반 예외를 동일한 502로 변환하는 구현은 아니다. 생성 실패 때 질문/답변 메시지는 저장되지 않아도 실패 turn과 이미 쌓인 progress는 남을 수 있다.

## 회원 SSE: 생성과 메시지 확정 저장은 별도

POST 메시지에 `Accept: text/event-stream`을 사용한다. 본문은 동일한 `{content}`다. `CHAT_CHECKPOINT_SIGNING_KEY`가 없으면 stream/turn 생성 전 503 `{detail:"채팅 서명 설정이 필요합니다."}`. stream 정상 시작 후 HTTP 상태는 200이며 이후 실패는 SSE `error`로 읽어야 한다.

프레임은 `event: 이름\ndata: JSON\n\n`. `id:`/자동 replay/Last-Event-ID 재개 계약은 없다. 헤더는 `Content-Type: text/event-stream`, `Cache-Control: no-cache, no-transform`, `X-Accel-Buffering: no`다.

| 이벤트 | payload | 의미 |
|---|---|---|
| `checkpoint` | `turn_id, receipt` | 시작 즉시 발급한 빈 prefix 증명 |
| `progress` | 아래 권한별 진행 payload | 모델·문서검색·도구 진행. 답변 문자열에 이어 붙이지 않음 |
| `delta` | `text, turn_id, receipt` | 이번 텍스트 청크와 **누적 답변 prefix**에 대한 증명 |
| `done` | `turn_id, receipt` + 선택 코스 metadata | 전체 prefix를 완료로 확정 가능한 증명. 회원 done에 assistant_message 없음 |
| `error` | `{detail:"답변 생성에 실패했습니다. 다시 시도해 주세요."}` | 공급자/저장 등 스트리밍 내부 예외의 안전한 메시지 |

클라이언트는 delta.text를 **수신 순서와 공백 그대로** 이어 붙이고 그 누적 prefix와 짝인 최신 receipt를 보관해야 한다. progress/done 메타데이터를 답변에 합치면 서명 검증이 실패한다.

```text
입력 검증 → pending ChatTurn 생성
  → checkpoint(빈 prefix)
  → [progress / delta(누적 prefix의 receipt)] 반복
  → done(complete receipt)
  → 클라이언트 POST finalize(completed, 누적 prefix, done receipt)
  → 질문 + 답변 메시지 저장

사용자 중단/연결 종료
  → 수신한 prefix와 그 receipt로 finalize(stopped)
  → 질문 + 수신 prefix 저장 (prefix가 비면 질문만)
```

**done 수신 자체는 메시지 저장이 아니다.** SSE 중에는 turn/progress만 영속화하며 메시지 저장은 finalize가 담당한다. 연결을 닫는 것만으로 stopped 메시지가 자동 저장되지 않는다. 확정되지 않은 pending turn이 있다고 새 질문을 막지는 않지만, 그 사이 메시지가 추가되면 이전 turn finalize는 충돌할 수 있다.

생산자 스레드와 크기 64 큐가 progress/텍스트를 전달한다. 연결 종료 시 취소 플래그로 새 작업 시작을 차단하고 interrupted phase를 저장한다. 이미 시작한 외부 호출을 강제로 즉시 종료했다고 보장하지 않으며 그 실제 종료 기록은 남긴다. producer error는 pending turn을 failed로 바꾸지만 연결 종료/모든 기타 SSE 예외가 항상 failed 또는 stopped로 바뀌는 것은 아니다(예: 빈 답변 done 검증 실패).

## finalize: 증명 검증, 멱등 재시도와 중단 경쟁

근거: [ChatFinalizeView](../../backend/llm/views.py), [finalize serializer](../../backend/llm/serializers.py), [저장 history](../../backend/llm/chat_message_histories.py), [회귀 테스트](../../backend/llm/tests.py).

필수 입력:

| 필드 | 조건 |
|---|---|
| `receipt` | 최대 1,000자, 서버가 발급한 서명 문자열 |
| `prefix` | 최대 8,000자, 빈 문자열 허용, **trim하지 않음** |
| `status` | `completed` 또는 `stopped` |

receipt는 turn/user/session, prefix 문자 길이와 SHA-256 digest, complete 여부에 묶이고 발급 후 **10분** 유효하다. 서명키는 서버 설정에서만 사용한다. 다른 소유자는 404, 변조·만료·불일치 400. completed에는 complete receipt와 비어 있지 않은 prefix가 필요하며 stopped는 checkpoint/delta/done receipt로 수신한 prefix를 저장할 수 있다.

응답: `turn_id, session_id, status, user_message_id, assistant_message_id, user_message, assistant_message`. assistant가 없으면 ID null/문자열 `""`다.

- 같은 status 반복, 이미 stopped인 turn은 기존 결과를 반환하며 메시지를 추가하지 않는다. 재호출도 먼저 receipt의 유효성을 검증하므로 **10분 만료 뒤까지 무조건 멱등 성공**하는 것은 아니다.
- pending/failed turn은 현재 마지막 sequence가 base_sequence와 같아야 저장 가능하다. 다르면 409. 질문을 저장하고 prefix가 비지 않으면 assistant를 저장한다. stopped의 빈 prefix는 빈 ai 행을 만들지 않는다.
- 완료 직후 중단 요청이 경쟁하면, 이후 메시지나 해당 assistant 이후를 기준으로 시작한 다른 turn이 없을 때 completed assistant를 수신 prefix로 줄이거나 삭제하여 stopped로 바꿀 수 있다.
- 이후 메시지/새 turn이 있으면 과거 completed를 덮어쓰지 않고 기존 completed 결과를 반환한다. stopped는 다시 completed로 승격하지 않는다.
- 재시도 방법: **같은 receipt/prefix/status finalize 재전송**은 중복 저장을 막는다. 반면 메시지 POST 재전송은 새 turn/새 모델 실행이며 idempotency key나 자동 중복 제거가 없다. 409에서는 메시지/turn 이력을 새로 읽고 사용자에게 재질문 여부를 확인해야 한다. SSE 연결을 다시 POST하는 것은 이어받기가 아니다.

## 게스트 채팅

POST `/chat/guest/`는 `AllowAny`이면서 **authentication_classes=()**라 JWT를 해석하지 않는다. staff JWT 또는 입력의 admin 플래그를 보내도 상세 진행 정보 권한을 얻지 못한다. Accept JSON을 보내도 뷰의 정상 응답은 SSE이며 JSON 비스트리밍 모드는 없다.

```json
{"messages":[{"role":"user","content":"잠실 주변 산책 코스 알려줘"}]}
```

- messages 1~12개, 각 role은 `user|assistant`, 마지막은 반드시 user. 교대 순서를 강제하지는 않는다.
- content를 strip한 뒤 user 1~2,000자, assistant 1~8,000자, 합계 최대 32,000자. system/tool role을 받지 않는다. 과거 대화는 클라이언트가 매번 전달한다.
- 마지막 user를 현재 질문으로, 앞 메시지들을 LangChain history로 변환한다.
- 입력 검증 후 IP rate limit, 그 다음 모델 호출. Django cache 기반 기본 60초 창 10건(`CHAT_GUEST_RATE_WINDOW`, `CHAT_GUEST_RATE_LIMIT`). 한도 초과 429 `{detail:"요청이 많습니다. 잠시 후 다시 시도해 주세요."}`.
- 기본 IP는 REMOTE_ADDR. `CHAT_TRUST_PROXY_HEADERS=true`일 때만 X-Real-IP 사용, 유효하지 않거나 없으면 unknown. 신뢰 가능한 프록시가 헤더를 덮어쓰는 배포에서만 활성화한다. 제한 범위는 설정된 캐시 백엔드/프로세스 공유 방식에 좌우된다.

이벤트: `progress`는 공개 형식, `delta={text}`, `done={assistant_message, ...선택 코스 metadata}`, `error={detail}`. checkpoint/receipt/finalize가 없으며 progress의 turn_id는 요청마다 임시 UUID다. **ChatSession/ChatTurn/ChatMessage/ChatProgressEvent를 저장하지 않는다.** 다만 도메인 도구의 장소·관광·길찾기 공급자 동기화 등은 별개이므로 전체 요청에 DB 쓰기가 전혀 없다고 보장하는 것은 아니다.

## 진행 정보의 권한별 공개 범위

근거: [progress.py](../../backend/llm/progress.py), [serializers.py](../../backend/llm/serializers.py), [진행 테스트](../../backend/llm/test_progress.py).

공통 공개 필드:

`turn_id, sequence_no, operation_id, parent_operation_id, kind, status, label, created_at, tool_name, summary`.

- kind: `phase|retrieval|tool`. status: `started|completed|failed|interrupted|unknown`. operation_id로 시작/종료를 짝짓고 sequence_no는 turn 내 순서다. `summary`는 현재 **항상 null**.
- 일반 회원과 게스트에는 도구 이름·진행 상태까지만 공개한다. 도구 입력/출력 원문이나 SQL/schema를 내려주지 않는다.
- `is_staff || is_superuser`인 **회원 본인**의 SSE/JSON 응답과 turn 목록에는 `tool_call_id, arguments, result, truncated`가 추가된다. 이 값도 원문이 아니라 서버에서 정제한 snapshot이다.
- 저장 전 도구별 allowlist만 남기고 URL/키·토큰·SQL 유사 문자열을 가린다. 모델 prompt/messages/reasoning은 수집하지 않는다. arguments 최대 4,096바이트/result 16,384바이트, 문자열 200자, 깊이 4, 목록 20개 제한. 알 수 없는 도구는 허용키가 없어 세부내용을 남기지 않는다.
- 최대 128개는 started 성격의 일반 이벤트 한도이며 이미 시작한 작업의 terminal 기록은 계속 받는다. 전체 이벤트가 정확히 128개 이하라는 의미가 아니다.
- 회원 progress는 **DB 저장 후 큐 전송**한다. 저장 실패는 성공이나 무음 fallback으로 처리하지 않는다. 도구가 예외 대신 error 문자열/객체를 반환해도 failed로 기록한다.
- turn 목록에서 5분 넘게 terminal 없이 남은 started는 응답에서만 `unknown / 결과 확인 불가`로 투영한다. DB started 행을 수정하지 않으며, 실제 취소(interrupted)와 구분한다.

## 코스 metadata와 코스 저장

[course_meta](../../backend/llm/views.py)는 RAG 결과에 places 또는 coursePayload가 있을 때만 `places, coursePayload, route, stadiumCode, travel`을 붙인다. 회원 JSON 및 회원/게스트 SSE done이 대상이며, 질문/답변 메시지 목록이나 finalize 응답에 이 metadata를 저장해서 되돌리는 계약은 없다. OpenAPI용 metadata serializer는 stadiumCode/travel을 모두 기술하지 않으므로 런타임 뷰를 기준으로 한다.

- places: 지도용 추천 장소(phase/name/lat/lng/category/placeId/address/placeUrl/distance/reason/time/stayMin 등).
- coursePayload: `title, stadium, content, contentFormat, duration, tags, startLat, startLng, stops`를 갖는 코스 생성용 초안. 상세 조립은 [course/save.py](../../backend/llm/rag/course/save.py).
- 채팅 완료/finalize가 Course를 자동 생성하지 않는다. 사용자가 별도 [travel 코스 API](travel.md) `POST /courses/`로 저장하고 반환받은 editToken을 관리해야 한다.

## 모델·처리 흐름·외부 의존성

[models.py](../../backend/llm/models.py)의 주요 관계:

| 모델 | 저장 역할 |
|---|---|
| `ChatSession` | 회원 FK, title, 생성/갱신 시각 |
| `ChatMessage` | 세션 FK, 순서, human/ai, 본문, 완료/중단 상태 |
| `ChatTurn` | UUID, 질문, 생성 시 base_sequence, 상태, 확정한 두 메시지의 일대일 참조 |
| `ChatProgressEvent` | turn FK, `(turn,sequence_no)` 유일성, 작업 ID·상태·정제된 snapshot |
| `Document` / `DocumentChunk` | RAG 문서와 청크. 1536차원 pgvector embedding, cosine HNSW 인덱스 |

세션 삭제는 메시지/turn/progress를 cascade 삭제하지만 문서와 임베딩은 삭제하지 않는다. [DjangoChatMessageHistory](../../backend/llm/chat_message_histories.py)는 소유권으로 세션을 조회하고 DB ID 순으로 기록을 읽어 human/ai 메시지로 변환한다. 메시지 쓰기는 세션 잠금 후 마지막 sequence 다음부터 bulk_create한다. 회원 history 조회 자체에 guest의 12개/32,000자 제한을 적용하지는 않는다.

```text
회원 DB history 또는 guest 전달 history
 → ChatService (요청 단위 도구 상태/ProgressCollector)
 → rag.pipeline (history 역할 정규화, [선택한 구장: ...] 접두어 분리)
 → dispatcher → assistant 문서 검색 + 모델/도구
 → 필요시 도메인별 처리(course/club/venue/nearby), 답변/코스 metadata
 → JSON 저장 또는 SSE 생성 → 회원 finalize
```

운영에서는 [rag.pipeline](../../backend/llm/rag/pipeline.py)의 `chat_chain()`을 항상 사용한다. 예전 `CHAT_USE_RAG` 스위치는 현재 제어점이 아니다. 테스트 러너에서는 None을 반환해 `ChatService`의 직접 도구 루프를 쓰므로 테스트용 경로를 운영 주 경로와 혼동하지 않는다. [dispatcher](../../backend/llm/rag/dispatcher.py)의 실패 fallback과 진행 저장/취소 예외 전파, [assistant pipeline](../../backend/llm/rag/assistant/pipeline.py)의 제한된 모델/도구 흐름을 함께 참고한다.

- 모델: LangChain/OpenAI. ChatService 기본 `gpt-5.6-luna`, timeout 30초, max_retries=0, Responses API. 실제 assistant 경로는 `LLM_MODEL`(기본 같은 모델), timeout 25초/max_retries=0 등 해당 모듈 설정을 따른다. 도구 라운드/fallback은 HTTP 전송 자동 재시도와 다르다. dispatcher의 SSE fallback은 아직 답변 청크를 하나도 내보내지 않았을 때만 실행하며, 일부 텍스트를 보낸 뒤 실패하면 다른 답변을 이어 붙이지 않고 오류를 전파한다. `ProgressCancelled`/`ProgressStorageError`는 이 fallback에서도 재실행하지 않는다. ChatService 테스트용 루프는 최대 도구 4회/라운드 4회 기준, assistant streaming도 도구 호출 4회 제한. 답변/최종 prefix 상한은 관련 경로에서 8,000자다.
- [공통 도구 등록](../../backend/llm/rag/domain_tools.py)은 도메인별로 같은 통합 도구 집합을 제공하고 이름 충돌 시 specialized 도구를 우선한다. [기본 목록](../../backend/llm/tools/__init__.py), [조회 도메인](../../backend/llm/tools/domain.py), [specialized 도구](../../backend/llm/rag/assistant/tools.py)를 함께 봐야 실제 바인딩을 알 수 있다.
- 조회 도구: 순위/경기/구장/좌석·시야/가격·정책/교통/먹거리/시설·콘텐츠·좌석도, 장소/코스/커뮤니티/승부예측/선수, 길찾기/관광/날씨. specialized 검색·코스 설계와 RAG 문서 검색이 더해진다. 이것들은 내부 LLM 도구이지 동일 이름 HTTP API가 아니다.
- 범용 SQL은 [baseball tools](../../backend/llm/tools/baseball.py) → [읽기 전용 query service](../../backend/baseball/query_service.py)를 사용한다. get_baseball_schema 후 execute_baseball_select 순서 가드, 허용 SELECT/행 제한/읽기 전용 DB 경계가 있다. 도구 등록만으로 관리자 변경 API를 모델에 부여하지 않는다.
- PostgreSQL/pgvector와 문서 인덱스, OpenAI 모델·임베딩, 카카오 장소/길찾기, TourAPI, 기상청 설정이 관련 질문에 필요하다. 외부 여행 데이터의 조회/저장/실패 계약은 [travel README](travel.md). LangSmith tracing은 선택 설정이며 활성화 시 별도 외부 추적 시스템을 사용한다. 이 문서는 키 실제값을 다루지 않는다.

## 오류·운영 주의 및 검증 범위

- 입력 400, JWT 필요/실패 401, 소유자 범위 밖 404, 미지원 메서드 405, 기록 경쟁 409, guest rate limit 429, 비스트림 OpenAI 실패 502, 회원 SSE 서명 미설정 503. DRF content negotiation/parser 오류 등은 별도다.
- stream 시작 후에는 HTTP 200이어도 `error`가 올 수 있다. done 없이 연결이 끝나면 완료 저장으로 처리하지 말고 수신 checkpoint가 있는지와 finalize 결과를 확인한다.
- 비스트림 저장 실패나 progress DB 실패를 성공 응답으로 가리지 않는다. 모든 일반 예외의 사용자 정의 HTTP 포맷이 통일되어 있지는 않다.
- [tests.py](../../backend/llm/tests.py): 소유권, JSON 저장, 모델 실패, SSE finalize 전 미저장, 서명/중단/경쟁/멱등성, guest 무채팅저장.
- [test_progress.py](../../backend/llm/test_progress.py): 권한별 정제·노출, 실제 순차 전달, 저장 실패 전파, 동시 collector 격리, 연결 종료와 unknown 구분.
- [도메인 도구 테스트](../../backend/llm/test_domain_tools.py), [SQL 도구 테스트](../../backend/llm/test_baseball_tools.py), [RAG 바인딩 테스트](../../backend/llm/test_rag_domain_bindings.py): 내부 호출 경계 근거.

이번 문서 작업은 소스/테스트 정적 대조와 로컬 링크 검사만 수행했다. Django 테스트·모델 호출·DB/외부 공급자 동작은 실행 검증하지 않았으며 기존 RAG README는 수정하지 않았다.
