# 백엔드 구성 안내

KBO 직관 가이드의 Django REST API입니다. 회원 인증, 채팅·RAG, 야구 데이터, 직관 코스, 커뮤니티, TVING 데이터 조회를 **6개 앱**으로 나눕니다. 이 문서는 앱의 역할과 연결 관계를 안내하며, 요청·응답과 오류 등 API 상세는 각 앱 README에서 설명합니다.

[프로젝트 README](../README.md) · [OpenAPI 계약](../contracts/openapi.yaml) · [Postman 컬렉션](../docs/postman/)

## 앱별 역할과 API 문서

| 앱 | 주요 기능 | 담당 데이터·처리 | Django 직접 호출 경로 | 상세 문서 |
| --- | --- | --- | --- | --- |
| `accounts` | 회원가입·로그인, JWT 갱신·로그아웃, 계정 복구·정보 변경, 관리자 회원 관리 | 사용자와 권한, 이메일 인증, refresh 폐기 | `/api/v1/auth/…` | [인증·회원 API](../docs/api/accounts.md) |
| `llm` | 회원·게스트 채팅, 답변 스트리밍, 대화 기록, 답변 중단·확정, RAG·도구 호출 | 채팅방·메시지·턴, 문서 검색과 도메인 에이전트 | `/api/v1/chat/…` | [채팅 API](../docs/api/llm.md) |
| `baseball` | 구단·구장·경기·순위·티켓 등 조회, 관리자 야구 데이터 CRUD, 안전한 SQL 조회 서비스 | 야구 도메인 모델, CSV 적재, 읽기 전용 DB 조회 | `/api/v1/baseball/…`, `/api/v1/baseball/manage/…` | [야구 데이터 API](../docs/api/baseball.md) |
| `travel` | 직관 코스 CRUD·반응·조회수, 장소 검색·관리, 길찾기, 구장 날씨, 관광 검색 | 코스·방문 장소, 외부 데이터 스냅샷, 지도·기상·관광 서비스 | `/api/v1/courses/…`, `/api/v1/places/…`, `/api/v1/travel/directions/`, `/api/v1/weather/`, `/api/v1/tourism/` | [코스·장소·외부 정보 API](../docs/api/travel.md) |
| `community` | 게시글·댓글, 추천·신고, 임시저장·게시, 이미지, 승부 예측 | 게시글·초안·이미지 연결, revision 충돌 및 게시 재시도 처리 | `/api/v1/community/…` | [커뮤니티 API](../docs/api/community.md) |
| `tving` | 일별·월별 경기 정보, 구단·선수 상세, 엔티티·스냅샷 관리 | TVING 응답 조회·저장과 관계형 데이터 반영 | `/api/v1/tving/…` | [TVING 데이터 API](../docs/api/tving.md) |

앱 등록은 [config/settings.py](config/settings.py), 실제 공개 URL은 [config/urls.py](config/urls.py)를 기준으로 합니다. 폴더에 서비스 함수나 View가 있다고 해서 모두 HTTP API로 노출되는 것은 아닙니다.

## 디렉터리 구분

```text
backend/
├── config/          # 프로젝트 설정, 최상위 URL, WSGI/ASGI
├── accounts/        # 회원·인증
├── llm/             # 채팅 API·기록·RAG·도구
│   └── rag/         # 질문 분류와 도메인별 답변 파이프라인
├── baseball/        # 야구 데이터 모델·조회·관리
├── travel/          # 코스·장소·길찾기·날씨·관광
├── community/       # 게시판·초안·이미지·승부 예측
├── tving/           # TVING 데이터 서비스
├── crawling/        # 데이터 수집 스크립트 (Django 앱 아님)
├── preprocessing/   # 데이터 전처리 스크립트 (Django 앱 아님)
├── manage.py
└── requirements.txt
```

`config`는 업무 앱이 아닌 프로젝트 설정 패키지입니다. 수집·전처리와 RAG 내부 구성은 기존 문서를 참고합니다.

- [수집 스크립트](crawling/README.md)
- [전처리 스크립트](preprocessing/README.md)
- [RAG 파이프라인](llm/rag/README.md)

## 앱 간 연결

- **회원·권한:** `accounts`의 사용자 모델과 JWT를 다른 앱에서 공유합니다. 공개 조회, 로그인 필수, 작성자 전용, 관리자 전용 정책은 엔드포인트별로 다릅니다.
- **채팅:** `llm`이 질문에 따라 RAG 문서를 검색하거나 야구 조회·코스·장소 등 도메인 서비스를 도구로 호출합니다. 모든 처리가 앱 간 HTTP 호출로 이루어지는 것은 아닙니다.
- **야구 데이터:** `baseball`이 야구 모델과 조회 서비스를 제공하고, `tving`은 외부 경기·팀·선수 데이터 조회 및 스냅샷·관계형 반영을 담당합니다.
- **직관 코스:** `travel`의 코스·장소 기능은 일반 API와 챗봇 도구에서 사용합니다. 카카오·기상청·관광 API 등 외부 서비스 설정이 기능별로 필요합니다.
- **커뮤니티:** 게시글·초안은 DB에, 이미지 파일은 S3 호환 private bucket에 저장합니다. 이미지 읽기는 Django를 거치며 미게시 이미지에는 소유권 검사를 적용합니다.

## API 공통 읽는 법

| 항목 | 규칙 |
| --- | --- |
| Django 직접 호출 | 기본 개발 주소 `http://127.0.0.1:8000`; 예: `/auth/signin` |
| Nginx 경유 | 기본 개발 주소 `http://localhost`; 예: `/api/v1/auth/signin` |
| 경로 변환 | Nginx가 `/api/` 접두어를 제거해 Django에 전달합니다. [프록시 설정](../nginx/nginx.conf) 참고 |
| 인증 | 필요한 API에 `Authorization: Bearer <access_token>` 전달 |
| 요청 형식 | 일반적으로 JSON. 이미지 업로드는 multipart, 채팅 스트리밍 응답은 SSE |
| 끝 슬래시 | URL마다 다릅니다. `/auth/signin`과 `/auth/signup/`처럼 문서의 경로를 그대로 사용합니다 |
| 응답·오류 | 앱별 형식이 다릅니다. 모든 API가 같은 래퍼·페이지네이션·오류 구조를 쓴다고 가정하지 않습니다 |
| OpenAPI | Django·Nginx `GET /api/v1/schema/`; 저장된 계약은 [contracts/openapi.yaml](../contracts/openapi.yaml) |

JWT access 수명은 5분, refresh 수명은 1일이며 refresh rotation은 사용하지 않습니다. 로그아웃은 제출한 refresh만 폐기하고 기존 access를 즉시 폐기하지 않습니다. 비밀번호 변경·재설정 시 토큰의 비밀번호 해시 검증이 적용됩니다. 상세 계약은 [accounts README](../docs/api/accounts.md)를 참고합니다.

## 개발 환경과 실행

모든 명령은 **프로젝트 루트** 기준입니다. Python 의존성은 [requirements.txt](requirements.txt)에서 관리합니다. DB는 PostgreSQL/pgvector를 사용합니다.

### 로컬 Python 실행

```bash
# 환경이 없을 때 한 번 생성
conda create -n skn34-backend python=3.12
conda activate skn34-backend
python -m pip install -r backend/requirements.txt
python -m django --version
```

[환경변수 예시](../.env.example)를 참고해 로컬 환경을 준비합니다. 설정은 프로세스 환경변수를 우선하며 `backend/.env`, 루트 `.env` 순서로 누락값을 읽습니다. 실제 비밀값은 문서·Git·로그에 남기지 않습니다.

| 설정 영역 | 주요 변수·주의사항 |
| --- | --- |
| DB | `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`; Compose 내부는 `DB_HOST=db`, 호스트에서 로컬 Docker DB로 접속하면 `127.0.0.1` |
| 야구 읽기 전용 조회 | `BASEBALL_DB_USER`, `BASEBALL_DB_PASSWORD`; 일반 DB 계정과 분리 |
| 채팅·RAG | `OPENAI_API_KEY`, `LLM_MODEL`, `EMBEDDING_MODEL`, `CHAT_CHECKPOINT_SIGNING_KEY` |
| 외부 정보 | `KAKAO_REST_API_KEY`, `KMA_SERVICE_KEY` 또는 `KMA_API_KEY`, `TOUR_API_KEY`; 각 서비스의 상세 조건은 travel 문서 참고 |
| 이메일 | `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS`, `DEFAULT_FROM_EMAIL`, `AUTH_FRONTEND_ORIGIN` |
| 이미지 | `COMMUNITY_IMAGE_S3_*`; Compose에서는 `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD`를 연결 |

DB와 필요한 외부 서비스가 준비된 개발 환경에서 실행합니다.

```bash
python backend/manage.py migrate
python backend/manage.py check
python backend/manage.py runserver 0.0.0.0:8000
```

마이그레이션은 스키마 준비이며 야구 데이터 적재나 RAG 인덱스 생성을 대신하지 않습니다. 데이터 준비는 각 앱 문서와 [전체 실행 안내](../README.md#12-실행-방법)를 참고합니다.

### Docker Compose

[Compose 설정](../docker-compose.yml)은 백엔드 시작 시 `migrate` → `provision_baseball_reader --prepare-db-permissions` → `runserver`를 실행합니다. PostgreSQL, MinIO, 메일 설정 및 필수 환경변수를 먼저 준비해야 합니다. 전체 서비스 실행 절차는 [루트 README](../README.md#12-실행-방법)에 있습니다.

현재 Compose는 개발용 `runserver`를 사용합니다. 이 실행 예시 자체를 운영 보안·배포 구성이 완료되었다는 의미로 해석하지 않습니다.

## 운영·검증 참고

- **만료 JWT 정리:** 운영 스케줄러에서 아래 명령을 하루 한 번 실행하도록 별도 설정합니다. 저장소가 스케줄러를 자동 설치하지는 않습니다.

  ```bash
  docker compose exec -T backend python manage.py flushexpiredtokens
  ```

- **API 테스트:** DB 테스트는 별도 테스트 DB를 생성·삭제할 수 있습니다. 운영 DB 계정이 아닌 테스트 전용 설정으로 실행합니다. 인증 회귀 테스트 예시는 다음과 같습니다.

  ```bash
  python backend/manage.py test accounts.test.test_logout accounts.test.test_tokens accounts.test.tests --noinput
  ```

- **Postman:** [인증 컬렉션](../docs/postman/auth.postman_collection.json)의 `base_url`은 직접 호출 시 `http://127.0.0.1:8000`, Nginx 경유 시 `http://localhost/api`입니다. 끝에 `/`를 붙이지 않습니다. `01 자동 회귀` 실행은 테스트 계정을 남길 수 있으므로 테스트 전용 환경을 사용하고 저장된 토큰을 공유하지 않습니다.
- **이미지 보존:** 미연결 이미지 자동 정리 명령은 구현되어 있지 않습니다. 파일을 임의로 삭제하면 초안·게시글 연결이 손상될 수 있습니다.
- **문서 범위:** 앱 README는 현재 코드의 계약을 설명합니다. `manage.py check` 통과나 문서 작성만으로 실제 DB·외부 API·LLM 통합 테스트 통과를 의미하지 않습니다.

[VS Code 환경 안내](../docs/guides/환경설정_my_venv.md)

## 채팅 사용량(크레딧)

- 1 credit = 1000 토큰(입력+출력 같은 비율). 기본 제공량은 비회원 500 크레딧(500,000 토큰) 한 번(충전 없음), 회원 `USAGE_TIMEZONE`(기본 Asia/Seoul) 달마다 999,999 크레딧(999,999,000 토큰). `USAGE_GUEST_TOKENS`·`USAGE_MEMBER_MONTHLY_TOKENS` 환경 변수로 변경할 수 있으며, 제공량 변경은 기존 사용량·예약·정산 내역을 초기화하지 않는다.
- `GET /api/v{1,2}/chat/usage/`: 요청자 본인 잔액. POST/PUT 메시지는 생성 전에 예약하고 잔액이 없으면 `402 {"code": "usage_exhausted"}`, 스트림 도중 예약 소진은 SSE `error {"code": "usage_exhausted"}`.
- 계량·정산 규칙과 한계(호출 전 입력+출력 상한 검사, 크래시 예약 수동 정산, JEV usage, 게스트 쿠키 재발급)는 `llm/service/usage.py` 머리말에 있다. 요금·결제는 아직 없다.
