# 챗봇 연결과 수정

## 실제 연결 경로

챗봇은 별도 Next.js 중계나 프런트 API 키 없이 팀 Django API를 직접 사용합니다.

```text
브라우저 ChatProvider
  → 회원 memberFetch("/api/v1/chat/...") 또는 게스트 fetch("/api/v1/chat/guest/")
  → Nginx /api/v1/ 프록시
  → Django /chat/...
  → ChatService
```

회원 요청은 `memberFetch`가 access token을 넣고 401이면 한 번 갱신합니다. 인증 상태 확인 자체가 실패하면 게스트로 전환하지 않습니다. Nginx가 `/api/v1/` 접두어를 제거하므로 별도 Next relay는 없습니다.

## 요청 순서와 계약

회원은 보호된 `GET /api/v1/chat/sessions/`로 연결을 확인합니다. 게스트는 최근 `user`/`assistant` 기록을 제한된 body로 `/api/v1/chat/guest/`에 보내며 `ChatSession`, `ChatMessage`, 브라우저 저장소에 쓰지 않습니다. 새로고침·로그인·계정 변경 시 게스트 기록이 사라집니다.

첫 질문은 다음 두 요청을 순서대로 보냅니다.

1. `POST /api/v1/chat/sessions/` — `{ "title": "질문 앞부분" }`
2. `POST /api/v1/chat/sessions/{session_id}/messages/` — `{ "content": "질문" }`

후속 질문은 같은 `session_id`의 메시지 endpoint만 호출하므로 Django에 저장된 이전 대화가 `ChatService`에 전달됩니다. 새 대화를 누르면 새 session을 만들며, 로그인 사용자가 바뀌거나 로그아웃하면 브라우저의 메시지·session 연결·진행 중 요청을 정리합니다.

Django 메시지 POST는 `Accept: text/event-stream`일 때 실제 모델 청크를 SSE로 전달합니다.

```text
event: delta
data: {"text":"답변 일부","turn_id":"...","receipt":"서명된 체크포인트"}

event: done
data: {"turn_id":"...","receipt":"완료 체크포인트"}
```

회원 브라우저는 마지막으로 받은 체크포인트를 `POST /api/v1/chat/turns/{turn_id}/finalize/`에 `{receipt, prefix, status}`로 보냅니다. 서버는 서명·회원·방·prefix 해시를 검증한 뒤 한 번만 저장하고 `turn_id`, `status`, `user_message_id`, `assistant_message_id`를 반환합니다. 완료는 완료 receipt로, Stop은 화면에 실제 표시된 prefix로 저장되며 첫 토큰 전 Stop은 질문만 저장합니다. 원문 assistant 문자열만 보내 위조할 수 없고, 반복 finalize는 같은 결과를 돌려줍니다.

명시적 Stop은 정상 결과이며 네트워크 단절과 구분합니다. 네트워크 단절·시간 초과로 회원 저장 확인이 없을 때만 결과가 불확실하다고 표시합니다. `Accept` 없는 기존 JSON 회원 호출은 HTTP 201 계약을 유지합니다.

서명에는 공개된 Django 기본키가 아닌 필수 비밀값 `CHAT_CHECKPOINT_SIGNING_KEY`를 사용합니다. Compose의 Nginx는 `X-Real-IP`를 덮어쓰므로 `CHAT_TRUST_PROXY_HEADERS=true`인 Compose backend만 이를 게스트 제한 키로 신뢰합니다. 기본 로컬 캐시는 단일 프로세스 제한이므로 여러 backend worker를 배포할 때 공유 cache로 바꿔야 합니다.

## 전용 채팅 페이지

- `/chat`과 작은 팝업은 루트 `ChatProvider`의 같은 대화 상태를 사용합니다.
- 두 화면 모두 청크를 누적하고 완료 finalize 또는 Stop 저장 확인 뒤 대화 기록으로 추가합니다.
- 새 대화와 현재 탭 안의 이전 대화 전환을 지원합니다.
- 루트 작성 화면의 **AI에게 질문하기**는 선택한 구장 문맥을 질문과 함께 전달하지만 작성 중인 본문을 자동 변경하지 않습니다.
- 페이지 새로고침 뒤 서버 대화 목록을 UI에 복원하는 기능은 아직 없습니다.

## 어디를 수정하나요?

| 바꾸려는 것 | 파일 |
| --- | --- |
| 채팅 페이지와 화면 디자인 | `app/chat/page.tsx`, `components/chat-workspace.tsx`, `styles/chat-workspace.css` |
| 작은 채팅 팝업과 디자인 | `components/chat-popup.tsx`, `styles/chat-popup.css` |
| 대화 상태·계정 경계·취소 복구 | `components/chat-provider.tsx` |
| Django session/message 요청과 응답 검증 | `lib/chat/client.ts` |
| Bearer 인증과 refresh | `lib/member-auth-request.ts` |
| 요청·응답 형식과 길이 제한 | `lib/chat/types.ts`, `lib/chat/validation.ts` |
| Django URL과 저장 흐름 | `backend/config/urls.py`, `backend/llm/views.py`, `backend/llm/chat_service.py` |

민감한 토큰이나 API 키는 대화 body, 오류 메시지, Git에 넣지 않습니다.
