# 챗봇 답변 평가 구현·검증 인계

검증일: 2026-10-02. 브랜치 `feat/llmops-feedback-admin`. 커밋·Push·PR·배포는 실행하지 않았습니다.

## 기능과 API

- 전체 채팅·팝업에서 저장된 완료 답변에 좋아요/아쉬워요, 변경/취소, 선택 사유·의견을 제공합니다. 두 화면은 기존 ChatProvider 상태를 공유하며 서버 히스토리에서 평가를 복원합니다.
- `PUT /api/v2/chat/sessions/{session_uuid}/feedback/`: `message_id`는 실제 공개 메시지 번호(저장 UUID도 기존 MessageIdField로 지원), `rating`은 `up`, `down`, 취소는 `null`. `reason`은 빈 문자열 또는 `incorrect`, `irrelevant`, `incomplete`, `other`; `comment`는 문자열, 최대 1000자입니다. 사유·의견은 down일 때만 허용합니다. 응답은 `{ "feedback": null }` 또는 rating/reason/comment입니다.
- v1에도 동일 경로가 제공됩니다. 기존 JWT 회원 인증과 기존 guest_id 세션 소유권을 사용합니다. 새 인증 쿠키나 쿠키 기반 회원 인증 fallback은 추가하지 않았습니다.
- 소유하지 않은 세션, human/tool/planner/실패/미완료 답변, 편집으로 제거된 stale 답변은 404입니다. 잘못된 입력은 400입니다. 체크포인트 갱신과 동일한 세션 행 잠금 안에서 최신 완료 답변을 확인합니다. `(session, answer_id)` 유일 제약으로 답변당 평가 하나를 유지합니다.
- `GET /api/v2/chat/admin/feedback/?page=1&rating=down&reason=incomplete`: 최신 갱신 순, 페이지당 20건. DRF count/next/previous/results 응답입니다. 필터는 선택사항이며 잘못된 필터는 400입니다.
- `GET /api/v2/chat/admin/feedback/{id}/`: 상세. 두 관리 API는 서버에서 `is_superuser === True`만 허용하며 is_staff만으로는 접근할 수 없습니다. `/admin` 링크와 `/admin/feedback` 화면을 추가했습니다.

## 보관·개인정보·메타데이터

- 질문·답변 스냅샷과 실제 저장 답변 ID, 공개 메시지 번호는 서버가 현재 checkpoint에서 추출합니다. 클라이언트의 질문/답변/메타데이터를 신뢰하지 않습니다.
- 질문 편집 또는 일부 메시지 삭제로 기존 답변이 제거되어도 평가 당시 스냅샷은 관리자 검토용으로 남습니다. 제거된 답변에는 다시 평가할 수 없으며 해당 평가 취소도 현재 답변 검증을 통과하지 못합니다. 세션 삭제 또는 회원 삭제에는 FK CASCADE로 평가와 스냅샷을 함께 삭제합니다. 별도의 기간 기반 자동 만료는 없습니다. 게스트 평가도 세션 삭제까지 동일하게 보관합니다.
- 평가는 자동 학습, 학습 데이터 사용 동의, 모델 개선 데이터 재사용 동의로 해석하지 않습니다. 자동 학습/재사용 파이프라인·LangSmith 연동은 구현하지 않았습니다.
- 회원·게스트에게는 자기 답변의 rating/reason/comment만 반환합니다. 관리자 상세에는 plain 질문·답변 및 제한된 실제 메타데이터만 제공하며 private prompt/tool 인자·결과를 평가 레코드에 저장하지 않습니다.
- 실제 저장 답변 response_metadata의 chain_version(v1/v2), model_name/model/run_id만 허용합니다. 새 완료 답변에는 실제 dispatch chain version을 기록합니다. 현재 생성 경로가 공급자 모델·trace 메타데이터를 보존하지 않는 경우 해당 값은 비어 있습니다. UUID 답변 ID는 모델 trace ID가 아닙니다. 과거 fixture/답변의 메타데이터는 `{}`일 수 있습니다.

## 실제 검증

런타임: Python 3.13.5, Django 6.1.1, Node v22.22.0, Next.js 16.3.4. backend/requirements.txt를 작업 공간의 격리된 backend/.venv에 설치했습니다. 의존성 manifest는 변경하지 않았습니다. DB는 별도 `pgvector/pgvector:pg18` 컨테이너입니다.

아래 backend 명령은 `DB_HOST=127.0.0.1 DB_PORT=55439 DB_NAME=feedback DB_USER=feedback DB_PASSWORD=<로컬 일회용 값>` 환경으로 실행했습니다. 인증정보는 문서에 기록하지 않습니다.

| 명령 | 결과 | 원본 로그 |
| --- | --- | --- |
| `backend/.venv/bin/python backend/manage.py migrate --noinput` | 빈 DB에서 전체 migration 및 0011 성공 | `/tmp/feedback-migrate.log` |
| `backend/.venv/bin/python backend/manage.py makemigrations --check --dry-run` | No changes detected | 터미널 출력 |
| `backend/.venv/bin/python backend/manage.py test llm.tests.test_feedback llm.tests.test_chat_checkpoints llm.tests.test_public_chat_serialization llm.tests.test_usage_credits --noinput --keepdb` | 79 tests OK | `/tmp/feedback-backend-tests.log` |
| `backend/.venv/bin/python backend/manage.py test llm.tests.test_chat_regressions llm.tests.test_chat_service_checkpoint llm.tests.test_guest_identity_regressions llm.tests.test_input_boundary_regressions llm.tests.test_sse_get_regression llm.tests.test_chat_v1_v2_dispatch --noinput --keepdb` | 58 tests OK | `/tmp/feedback-backend-extra.log` |
| `npm --prefix frontend run test:chat` | 67/67 통과 | `/tmp/feedback-chat-tests-final.log` |
| `node --test frontend/tests/chat-provider-race.test.mjs frontend/tests/chat-progress.test.mjs frontend/tests/chat-settings.test.mjs` | 43/43 통과 | `/tmp/feedback-extra-frontend-final.log` |
| `npm --prefix frontend run lint -- app/admin/feedback/page.tsx components/chat-feedback.tsx components/chat-provider.tsx` | 통과 | `/tmp/feedback-lint.log` |
| `npm --prefix frontend run build` | 컴파일·TS·페이지 생성 성공 | `/tmp/feedback-build-final.log` |
| `frontend/node_modules/.bin/tsc --noEmit --project frontend/tsconfig.json` | 통과 | `/tmp/feedback-typecheck-final.log` (빈 성공 로그) |
| `git diff --check` | 통과 | 터미널 출력 |

Backend 검사는 생성/반복/변경/취소/복원, member/guest 소유권, staff 차단/superuser, 유효하지 않은 값과 bool ID, stale/planner/실패 답변 거절, 스냅샷 보존, 세션/회원 삭제, 유일 제약, 필터/페이지를 포함합니다. Provider 검사는 중복 평가 요청 거절과 계정 전환 뒤 늦은 응답 무시를 포함합니다. 실제 동시 다중 DB writer stress 검사는 별도로 실행하지 않았습니다.

처음 추가 frontend 회귀 실행은 새 ChatFeedback import가 기존 임시 transpile 하네스에 포함되지 않아 2건 실패했습니다. 해당 하네스를 업데이트한 후 위 43건이 통과했습니다. 처음 backend 실행의 bool ID 실패는 shared MessageIdField에서 bool을 거절하도록 수정했습니다. 첫 테스트 DB teardown은 saver 연결로 실패하여 작업 전용 test_feedback DB만 확인 후 강제 삭제하고 --keepdb로 재검증했습니다. 테스트가 의도적으로 발생시키는 provider/saver 오류 로그는 최종 테스트 결과 실패가 아닙니다.

## 실제 브라우저 확인

실제 Next production 서버와 실제 Django/DB를 실행하고 Aside에서 클릭·리로드 및 스크린샷을 확인했습니다. 라이브 모델 호출은 하지 않았으며 checkpoint에 명시적으로 표시한 UI 검증용 HumanMessage/AIMessage fixture를 사용했습니다.

- 전체 화면 좋아요 → 아쉬워요 및 선택 사유/의견 저장 → 팝업에 선택 상태 유지 → 취소.
- 리로드 후 취소 상태 및 저장된 좋아요 상태 복원 확인.
- 기존 로그인 UI/JWT로 로컬 QA superuser 로그인 → 관리자 목록·질문/답변 스냅샷 상세 확인.
- 최종 빌드 재시작 후 rating=up의 0건 상태, rating=down의 1건 상태, 상세 열기/닫기 확인.
- 스크린샷 디렉터리: `/Users/yunseongho/.aside/u/0/sessions/2026-10-02_ue4woQSMAZNMnoGN/tmp/`의 `feedback-workspace.png`, `feedback-popup.png`, `feedback-admin.png`, `feedback-admin-final.png`. fullPage 최종 캡처에 반복 타일처럼 보이는 캡처 이상이 있어 관리자 상세의 정상 viewport 캡처와 accessibility snapshot도 함께 확인했습니다.
- 네트워크 오류 UI는 코드/client 회귀로 검증했으며 실제 브라우저 장애 주입·모바일 전용 viewport 검사는 하지 않았습니다.

## 검증자용 로컬 자원

별도 검증을 위해 아래 작업 전용 자원을 남겼고 coordinator에 전달했습니다. 검증 후 coordinator가 소유 자원만 정리해야 합니다. 다른 실행 중인 스택·볼륨은 변경하지 않았습니다.

- 컨테이너 `feedback-admin-db-bc454`, label `feedback.task=task_bc454f810fce`, localhost DB 포트 55439, DB/user feedback. 삭제 전에 컨테이너 이름·label을 다시 확인합니다.
- Next 3109 (background task `bb0u76xcg`), Django 8119 (`bxakkx5ch`), 테스트 프록시 3110 (`bj0bn2z8h`).
- 테스트 프록시 `/tmp/feedback-qa-proxy.py`는 `/api/*`만 Django로, 나머지를 Next로 전달합니다. guest QA에서는 기존 일회용 guest_id capability를 upstream에 넣습니다. 제품 코드·인증 설계 변경이 아니며 실행/배포 자산에 포함하지 않습니다.
- 원본 로그는 위 /tmp 경로와 `/tmp/feedback-frontend-server-final.log`, `/tmp/feedback-backend-server.log`, `/tmp/feedback-proxy.log`에 있습니다. 로컬 서버 로그·fixture에는 검증 데이터가 있으므로 외부 공개하지 않습니다.
- 기존 환경의 PostgresSaver 의존성 누락과 Turbopack 외부 node_modules symlink 오류는 작업 공간의 격리 venv/로컬 dependency copy로 해결했습니다. global config나 요구사항 파일은 수정하지 않았습니다. 이 실행 경로를 반복 사용할 경우 `/run-skill-generator`로 프로젝트 실행 지침을 정리할 수 있습니다.

별도 max verifier 검토는 이 구현 검증과 구분되며 아직 완료로 주장하지 않습니다.
