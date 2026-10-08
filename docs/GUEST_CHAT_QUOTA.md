# 비회원 채팅 이용 제한: 과거 정책과 현행 wallet

## 과거 정책 (history-only)

이 문서가 이전에 설명한 IP별 누적 2회 제한은 현행 채팅 정책이 아닙니다.
`GuestChatUsage`와 `0006_guestchatusage` migration은 과거 IP 해시 기반 누적 횟수의 기록을 보존하는 이력입니다.
현재 `/api/v1/chat/guest/` 경로와 `llm.test_guest_quota` 테스트 모듈은 존재하지 않습니다.
이전의 403 `guest_quota_exhausted`, SECRET_KEY 기반 IP HMAC 및 같은 공인 IP의 누적 한도 공유 설명을 현재 동작으로 적용하지 않습니다.
기존 migration과 데이터는 삭제하지 않으며, `0012_merge_legacy_guest_usage`와 `0013_merge_attachment_and_legacy_usage`는 no-op으로 이력을 연결합니다.

## 현행 v2 채팅과 사용량

- 비회원과 회원은 `POST /api/v2/chat/sessions/`로 대화를 만들고, `POST /api/v2/chat/sessions/<session_id>/messages/`로 질문합니다. 성공 응답은 SSE이며, 질문 수정·재생성은 같은 메시지 경로의 `PUT`입니다.
- `GET /api/v2/chat/usage/`는 본인의 잔액을 조회하며 토큰을 차감하지 않습니다. v1도 동일한 세션·usage URL 구조를 지원합니다.
- 회원은 JWT Bearer로 인증하고, 비회원은 서버가 발급한 HttpOnly `guest_id` 쿠키의 UUID로 식별합니다. 비회원 지갑은 IP가 아닌 이 식별자를 사용합니다.
- 사용량은 `UsageWallet`과 `UsageCharge`에 기록합니다. 1 credit은 provider 입력·출력 합계 1,000 토큰이며 질문 횟수를 고정 1회씩 차감하지 않습니다.
- 기본 한도는 비회원 lifetime 500,000 토큰(`USAGE_GUEST_TOKENS`), 회원 월 999,999,000 토큰(`USAGE_MEMBER_MONTHLY_TOKENS`)입니다. 실제 운영값은 서버 설정을 따릅니다. 회원 월 경계는 `USAGE_TIMEZONE`(기본 `Asia/Seoul`)이며 비회원은 월 초기화가 없습니다.
- 같은 `guest_id`의 새 대화·새로고침·서버 재시작은 저장된 사용량을 초기화하지 않습니다. 쿠키 삭제나 새 식별자를 동일인으로 연결하는 누적 제한은 아닙니다.
- 양수 잔액이면 한 턴을 허용하고, producer 종료 후 알려진 실제 사용량을 정산합니다. 오류·연결 종료·사용자 중단에서도 알려진 사용량은 보존하며, 미확인 호출은 `settled_unknown`으로 기록하고 예상 비용을 만들지 않습니다.
- 잔액이 없으면 모델 호출 전에 402 `usage_exhausted`, 같은 지갑의 진행 중 턴이 있으면 409 `usage_busy`를 반환합니다. 지갑 잠금은 provider 작업이 끝난 뒤 해제합니다.
- 별도로 비회원 세션 생성·질문·재생성에는 IP 기반 요청 빈도 제한(`GuestChatThrottle`, `CHAT_GUEST_RATE_LIMIT`, `CHAT_GUEST_RATE_WINDOW`)이 적용됩니다. 이는 누적 2회 정책이 아니며 DRF 프록시·공유 캐시 설정에 의존합니다. 입력 검증 실패 및 요청 빈도 제한은 provider 토큰 사용량을 만들지 않습니다.

## 근거와 검증

- URL: `backend/llm/urls.py`
- 응답·요청 빈도 제한: `backend/llm/views/message.py`, `backend/llm/views/sesstion.py`
- 지갑·정산: `backend/llm/service/usage.py`
- 현행 회귀 테스트: `llm.tests.test_usage_credits`, `llm.tests.test_usage_compose`, `llm.tests.test_guest_identity_regressions` (외부 provider는 모킹)

배포 시 기존 이력을 포함한 `python manage.py migrate`가 필요합니다. 이 문서 정정은 새로운 제한 기능이나 데이터 이관을 추가하지 않습니다.
