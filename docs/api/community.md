# community — 게시판·임시저장·이미지·경기 예측 API

자유/팀 게시판, 본인 초안과 이미지 게시, 댓글·추천/비추천·신고, 경기 승부 예측을 제공한다. 계약은 [URLconf](../../backend/community/urls.py)의 등록 경로와 실제 view·serializer·service·테스트를 기준으로 한다.

[백엔드 안내](../../backend/README.md) · [인증 계약](accounts.md) · [프로젝트 URLconf](../../backend/config/urls.py)

## 공통 규칙

- 아래 URL은 **Django 직접 호출 기준**이다. Nginx 경유 시 `/api`를 앞에 붙인다. 모든 community 경로는 끝 `/`가 있다.
- 인증 표기의 `JWT`는 `Authorization: Bearer <access>`를 의미한다. 공개 API도 잘못된 JWT 헤더를 보내면 전역 인증 단계에서 `401`이 발생할 수 있다.
- 별도 명시 없으면 JSON body/JSON 응답이며 공통 `data` wrapper는 없다. 검증 오류는 보통 `400` 필드별 오류, 권한/상태 오류는 `detail`이다. 일부 이미지 오류는 본문이 없다. 미지원 메서드는 `405`다.
- 익명 쓰기는 `401`, 인증된 비소유자의 글/댓글 변경은 `403`. 초안과 비공개 이미지 읽기는 존재 여부를 숨기는 `404`를 사용한다. staff/superuser라고 게시글·댓글 소유권 검사를 우회하지 않는다.
- 지원 팀 코드: `LG, HH, SK, SS, NC, KT, LT, HT, OB, WO`.
- 자유게시판 category: `질문`, `잡담`. 팀게시판은 이에 `응원`, `경기토론`, `전력토론`, `소식·정보`, `이적·신인`, `직관후기`, `좌석·예매`, `직관준비`, `굿즈`, `사진·영상`을 추가한다.

## 모델과 저장 경계

근거: [models.py](../../backend/community/models.py), [마이그레이션](../../backend/community/migrations/0003_community_post_api.py), [초안·이미지 마이그레이션](../../backend/community/migrations/0004_community_draft_image.py).

| 모델 | 역할·주요 필드 | DB/운영 제약 |
|---|---|---|
| `CommunityPost` | 문자열 PK `source_id`, 표시 번호 `post_number`, board/team/category/title/content, owner, author 스냅샷, 멱등 키, 집계/조회수, 샘플 여부 | 사용자 삭제 시 owner는 null. `(owner,idempotency_key)` 조건부 unique. free는 빈 팀, teams는 유효 팀. 번호는 PostgreSQL sequence의 6자리 `000001` 이상 문자열. |
| `CommunityDraft` | UUID, owner, 작성 중 본문, revision, `published_post`, 생성/수정 시각 | revision ≥1. 사용자 삭제 시 삭제. 게시글 삭제 시 연결된 소비 초안도 CASCADE 삭제. |
| `CommunityImage` | UUID, owner, private object key, MIME/바이트/크기, draft 또는 post 연결 | object key unique, draft/post 동시 연결 금지. 사용자/초안/게시글 삭제 시 해당 FK는 SET_NULL이며 파일 자동 삭제가 아니다. |
| `CommunityComment` | 게시글·작성자 FK, content, 생성/수정 시각 | 글 또는 사용자 삭제 시 CASCADE. |
| `CommunityVote` | 글·사용자·`up/down` | `(post,user)` unique, 값 CHECK. |
| `CommunityReport` | 글·신고자·사유·상세·생성 시각 | `(post,reporter)` unique, 사유 CHECK. |
| `PredictionGame` | 원천 경기 ID PK, 날짜/시작/구장/홈·원정/점수/상태/결과/수집시각, lock/void 시각 | 팀 코드와 상태/결과 CHECK, 홈·원정 팀 상이. 결과와 잠금은 원천 동기화가 관리한다. |
| `GamePrediction` | 사용자·경기·`home/away`, 생성/수정 시각 | `(user,game)` unique, choice CHECK. |

추천/비추천·댓글 수 응답은 실제 관계 레코드를 집계한다. 과거 `recommendations`, `comment_count` 컬럼만을 응답의 진실로 사용하지 않는다. 샘플/소유자 없는 글은 공개 읽기 가능하지만 API에서 누구도 소유자 변경 권한을 얻지 못한다.

## 전체 등록 API

### 회원 글·댓글 활동 목록 (v1)

- `GET /api/v1/auth/users/{member_id}/public/`: JWT 인증 후 `id`, `nickname`, `activityVisible`만 반환한다. 닉네임이 없으면 username을 사용하며 이메일·생년월일·전체 공개 설정은 반환하지 않는다.
- `GET /api/v1/community/posts/?author_id={member_id}&page=1&page_size=20`: 기존 게시글 목록에 작성자 필터를 적용한다. `board`, `team`, `q`, `search_field`와 함께 사용할 수 있다.
- `GET /api/v1/community/comments/?author_id={member_id}&page=1&page_size=20`: 작성자 댓글을 최신순(`created_at`, `id` 내림차순)으로 조회한다. 각 항목은 `id`, `content`, `createdAt`, `postId`, `postTitle`, `board`, `teamCode`를 제공한다. 자유게시판의 `teamCode`는 빈 문자열이다.

공개 여부는 기존 `PATCH /api/v1/auth/user`의 `visibility.posts`를 재사용한다. 값이 없으면 비공개이며 본인은 공개 설정과 관계없이 조회 가능하다. 다른 회원의 비공개 활동 목록은 `403`, 없는 회원·비활성 회원은 `404`, 미인증 요청은 `401`이다. 프로필 조회 성공 여부와 별개로 두 활동 목록 API 모두 서버에서 권한을 검사한다.

`author_id`는 양의 정수이며 잘못된 값은 `400`이다. 댓글 목록에는 필수다. 작성자별 목록은 페이지 파라미터를 생략해도 기본 20개씩 `{count,next,previous,results}` 형식으로 응답한다. 숨김 게시글과 그 게시글의 댓글은 본인 조회에서도 제외한다. 게시글은 기존 게시글 번호 내림차순을 유지한다.

프론트는 비로그인 시 `/login?next=...`로 이동하며, 비공개 안내에서 본인 예외를 적용한다. 공개 프로필 조회 뒤 설정이 변경되어 활동 API가 `403`을 반환하는 경우에도 비공개 화면을 표시한다. 탭·페이지 파싱과 로그인 복귀 주소는 `member-return-path.ts`, 원문 이동 주소는 `getCommunityPostHref()`를 공통으로 사용한다.

응답 DTO와 세부 검증은 뒤 절을 함께 본다. 표에 없는 REST 기능을 지원한다고 가정하지 않는다.

### 글·댓글·추천·신고

구현: [게시글 view](../../backend/community/views.py), [상호작용 view](../../backend/community/interactions.py), [serializer](../../backend/community/serializers.py), [페이지 처리](../../backend/community/pagination.py).

| 메서드·Django URL | 권한 | 입력 | 성공 | 주요 오류 |
|---|---|---|---|---|
| `GET /community/posts/` | 공개, `mine=1`만 JWT | query `board`, `team`, `mine`, `page`, `page_size`, `q`, `search_field` | `200 Post[]` 또는 페이지 객체 | `400` 필터/검색/페이지 형식, `401` mine, `404` 범위 밖 페이지 |
| `POST /community/posts/` | JWT | PostWrite, 필수 `Idempotency-Key` 헤더 | 새 글 `201 Post`, 동일 재시도 `200 Post` | `400` 검증·키·번호 발급 실패, `401`, `409` 키 충돌 |
| `GET /community/posts/<str:source_id>/` | 공개 | 없음 | `200 Post`, 조회수 1 증가 | `404` |
| `PATCH /community/posts/<str:source_id>/` | JWT 작성자 | PostWrite의 일부 | `200 Post` | `400` 조합/필드 검증, `401`, `403`, `404` |
| `DELETE /community/posts/<str:source_id>/` | JWT 작성자 | 없음 | `204`, 본문 없음 | `401`, `403`, `404` |
| `GET /community/posts/<str:source_id>/comments/` | 공개 | `order=oldest`(기본) 또는 `newest` | `200 Comment[]`, 비페이지 배열 | `400` order, `404` 글 없음 |
| `POST /community/posts/<str:source_id>/comments/` | JWT | `content` | `201 Comment` | `400` 본문 검증, `401`, `404` |
| `PATCH /community/comments/<int:comment_id>/` | JWT 댓글 작성자 | 필수 `content` | `200 Comment` | `400`, `401`, `403`, `404` |
| `DELETE /community/comments/<int:comment_id>/` | JWT 댓글 작성자 | 없음 | `204`, 본문 없음 | `401`, `403`, `404` |
| `GET /community/posts/<str:source_id>/vote/` | JWT | 없음 | `200 VoteState` | `401`, `404` |
| `POST /community/posts/<str:source_id>/vote/` | JWT | `vote: "up" / "down" / null` | `200 VoteState` | `400`, `401`, `404` |
| `POST /community/posts/<str:source_id>/reports/` | JWT | `reason`, `detail` | 첫 신고 `201 {id,created:true}`, 중복 `200 {id,created:false}` | `400`, `401`, `404` |

댓글 단건 GET, 신고 GET/수정/삭제, 추천 DELETE, 게시글 PUT은 등록된 작업이 아니다. 신고 목록/처리/관리자 강제 삭제용 REST 경로도 없다.

### 초안·게시·이미지

구현: [초안 view](../../backend/community/drafts.py), [Pydantic 입력](../../backend/community/draft_schemas.py), [게시 트랜잭션](../../backend/community/publishing.py), [이미지 view](../../backend/community/images.py), [이미지 DTO](../../backend/community/image_schemas.py), [S3 adapter](../../backend/community/image_storage.py).

| 메서드·Django URL | 권한 | 입력 | 성공 | 주요 오류 |
|---|---|---|---|---|
| `GET /community/drafts/` | JWT 본인 | `page`, `page_size` | `200 {count,next,previous,results:[Draft]}` | `401`, `404` 잘못된 페이지 |
| `POST /community/drafts/` | JWT | DraftInput | `201 Draft`, revision=1 | `400`, `401` |
| `GET /community/drafts/<uuid:draft_id>/` | JWT 본인 미게시 초안 | 없음 | `200 Draft` | `401`, `404` 타인/게시됨/없음 |
| `PATCH /community/drafts/<uuid:draft_id>/` | JWT 본인 미게시 초안 | 필수 `revision`, 작성 필드 일부, 선택 `imageIds` | `200 Draft`, revision +1 | `400` 검증/이미지, `401`, `404`, `409` revision 충돌 |
| `DELETE /community/drafts/<uuid:draft_id>/` | JWT 본인 미게시 초안 | 없음; revision 불필요 | `204`, 본문 없음 | `401`, `404` |
| `POST /community/drafts/<uuid:draft_id>/publish/` | JWT 초안 소유자 | 정확히 `{revision}`, 필수 `Idempotency-Key` | 첫 게시 `201 Post`, 동일 재시도 `200 Post` | `400` 검증/키/번호, `401`, `404`, `409` 키/revision/재시도 충돌 |
| `POST /community/images/` | JWT | multipart `image` 파일 정확히 1개, Content-Length | `201 ImageUpload` | `400` 파일/형식, `401`, `411` 길이 없음/해석 불가, `413` 요청 크기, `415` 비multipart, `503` 저장소 |
| `GET /community/images/<uuid:image_id>/` | 게시 이미지는 공개, 그 외 소유자만 | 없음 | `200` 이미지 바이너리 스트림 | `404` 없음/비공개 권한/저장 객체 없음, `503` 저장소 |
| `DELETE /community/images/<uuid:image_id>/` | JWT 이미지 소유자 | 없음 | `204`, 본문 없음 | `401`, `403` 타인, `404`, `409` 게시 이미지, `503` 저장소 |

이미지 목록/수정 API는 없다. 이미지는 게시글 POST/PATCH로 첨부하는 것이 아니라 초안 PATCH의 `imageIds` 후 publish로 연결한다.

### 경기 승부 예측

구현: [예측 view](../../backend/community/predictions.py), [원천 검증·동기화](../../backend/community/prediction_source.py), [예측 serializer](../../backend/community/serializers.py).

| 메서드·Django URL | 권한 | 입력 | 성공 | 주요 오류 |
|---|---|---|---|---|
| `GET /community/predictions/games/` | 공개 | query `date`(기본 KST 오늘), `team` | `200 PredictionGame[]` | `400` 날짜/팀, `503` 원천 실패이며 조회할 DB 경기 없음 |
| `GET /community/predictions/games/<str:game_id>/` | 공개 | 없음 | `200 PredictionGame` | `404` DB에 경기 없음 |
| `POST /community/predictions/games/<str:game_id>/vote/` | JWT | 정확히 `{"choice":"home"}` / `"away"` / `null` | `200 PredictionGame` | `400` 입력, `401`, `404`, `409` 마감/무효/오래된 데이터, `503` 원천 실패 |

예측 생성·결과 수동 변경·점수/보상 지급·랭킹 API는 등록되어 있지 않다.

## 응답 DTO

| DTO | 필드 |
|---|---|
| `Post` | `id`, `sourceId`(둘 다 source_id), `postNumber`(6자리 문자열), `board`, `teamCode`, `authorId`(null 가능), `author`, `title`, `content`, `category`, `createdAt`(null 가능), `views`, `recommendations`, `downvotes`, `commentCount`, `isSample`, `images` |
| `Post.images[]` | `id`(UUID), `contentType`, `size`, `width`, `height`. 업로드 응답과 달리 `url`, `createdAt` 없음. |
| `Comment` | `id`, `postId`(문자열 글 PK), `authorId`, `author`(현재 nickname 또는 username), `content`, `createdAt`, `updatedAt` |
| `VoteState` | `vote`(내 up/down/null), `recommendations`, `downvotes`(전체 사용자 집계) |
| `Draft` | `id`, `board`, `teamCode`, `category`, `title`, `content`, `revision`, `createdAt`, `updatedAt`, `imageIds` |
| `ImageUpload` | `id`, `contentType`, `size`, `width`, `height`, `createdAt`, `url` |
| `PredictionGame` | `gameId`, `date`, `startsAt`(null 가능), `stadium`, `away`, `home`, `status`, `result`, `locked`, `voided`, `stale`, `sourceFetchedAt`, `votes`, `myChoice` |
| `PredictionGame.away/home` | `code`, `name`, `score`(null 가능) |
| `PredictionGame.votes` | `home`, `away`, `total`, `homePercent`, `awayPercent`. 0표면 두 비율 모두 0, 그 외 각각 Python `round` 계산. |

예측 `status`: `scheduled/live/final/cancelled/postponed/suspended/unknown`. `result`: `home/away/draw/null`. `myChoice`: 로그인 사용자의 `home/away/null`, 익명은 null.

**응답 URL 예외:** 이미지 업로드의 `url`과 커뮤니티 페이지의 `next/previous`는 직접 호출해도 `/api/community/...` 상대 URL을 반환한다. Django 직접 호출 클라이언트는 이 `/api` 프록시 접두어를 처리해야 한다. 저장소 object key나 공개 S3 URL은 반환하지 않는다.

## 게시글 조회·작성 상세 계약

### 목록과 페이지

- 기본 정렬은 `post_number` 오름차순이며 최신순이 아니다.
- `board`는 `free/teams`, `team`은 대문자로 정규화 후 팀 코드 검증. `board=free`와 team 동시 지정은 `400`. board 없이 team만 필터링 가능하다.
- `mine`은 문자열 `1`만 허용하며 JWT 본인 글을 필터링한다. 그 외 값은 `400`.
- `q`는 trim 후 최대 200자. `search_field=all`(기본)은 제목 또는 본문 부분 검색, `title`은 제목, `author`는 글의 작성 당시 author 문자열 검색이다.
- `page`와 `page_size`를 모두 생략하면 **전체 배열**을 반환한다. 둘 중 하나라도 주면 `{count,next,previous,results}`로 전환한다. 기본 20개, 최대 100개.
- page는 1~2,147,483,647, page_size는 1~100. ASCII 숫자만 허용하며 0, 선행 0, 부호, 소수 등은 `400`; 존재하지 않는 페이지는 `404`. count는 필터·검색 적용 후 전체 수다.
- 알 수 없는 검색 query 키는 무시한다. 게시글 GET 상세는 매 요청마다 DB `F` 연산으로 조회수를 증가시키며 HEAD에는 증가시키지 않는다.

### 작성·수정·멱등성

`PostWrite`: `board`, `teamCode`, `category`, `title`, `content`. 생성 시 모두 필요하며 free의 `teamCode`는 `""`, teams는 유효 코드여야 한다. teamCode는 대문자로 변환한다. 제목 최대 200자, 본문 최대 20,000자이며 trim 후 빈 값은 거부한다. PATCH는 필드 일부만 보내되 기존 값과 합친 board/team/category 조합을 검증한다.

실제 생성/PATCH는 [CommunityPostSerializer](../../backend/community/serializers.py)를 사용한다. 별도 `CommunityPostWriteSerializer`와 `CommunityPostPatchSerializer`는 스키마 설명용이다. 서버 소유 응답 필드와 미정의 필드를 엄격히 거부하는 allowlist는 아니므로 해당 입력을 쓰기 기능으로 간주하지 않는다. 작성자·ID·번호·조회수·집계·샘플 여부는 서버가 결정한다. author는 생성 시 nickname 또는 username을 복사한다.

- 생성 키는 trim 후 1~128자의 `Idempotency-Key`. 사용자별 unique이며, 동일 키와 정규화된 `board/team_code/category/title/content`가 모두 같으면 기존 글을 `200`으로 반환한다. 다르면 `409`이다.
- 글 수정 후 원래 생성 요청을 재시도하면 현재 글 내용과 비교하기 때문에 `409`가 될 수 있다. 키는 글에 저장된 멱등성이지 영구 별도 요청 이력이 아니다.
- source_id와 표시 번호는 서로 다르다. 상세 URL에 postNumber를 넣는 계약은 없다. 번호는 PostgreSQL sequence 기반이고 롤백/실패로 번호가 건너뛸 수 있다. 발급 범위를 벗어나는 DataError는 `400 postNumber`로 처리한다.
- 글 삭제는 댓글/추천/신고 및 연결 소비 초안을 삭제하지만 이미지는 FK만 해제하므로 파일을 자동 삭제하지 않는다.

## 댓글·추천·신고 상세 계약

- 댓글 내용은 trim 후 1~2,000자. PATCH도 `content`가 필수다. 기본 댓글 순서는 생성 시각·ID 오름차순, newest는 둘 다 내림차순. 글의 소유자가 다른 사람의 댓글을 수정할 수는 없다.
- 추천 POST는 **토글 이벤트가 아닌 원하는 최종 상태**다. 같은 up/down 반복은 한 레코드를 유지, 반대 값은 변경, null은 취소한다. 글 행을 잠그고 집계 컬럼을 동기화한다. 본인 글 추천 금지 규칙은 없다.
- 신고 reason은 `spam/abuse/inappropriate/privacy/other`. `detail`은 필수 키지만 빈 문자열 허용, trim 후 최대 50자. 같은 글·같은 신고자의 재요청은 기존 신고 ID와 `created:false`를 반환하며 기존 사유/상세를 수정하지 않는다. 중복 요청도 먼저 입력 검증한다.
- 댓글/추천/신고에는 초안의 extra-forbid와 같은 엄격한 추가 키 거부가 없다. 신고 공개 읽기나 자동 숨김/제재는 구현되어 있지 않다.

## 초안·이미지 첨부·게시 트랜잭션

### 초안

- 목록/조회/수정/삭제는 본인 소유의 **미게시** 초안만 대상이다. 목록 정렬은 `-updated_at, -id`, 항상 페이지 객체, 기본 20·최대 100개.
- 초안 페이지는 게시글의 엄격한 Pydantic query 검증을 사용하지 않는다. DRF 기본 페이지 처리이며 `page_size` 초과는 100으로 제한하고 잘못된 page_size는 기본값으로 처리한다.
- 생성 `DraftInput`은 board 필수, teamCode/category/title/content는 기본 빈 문자열. teams이면 팀 코드 필수. category는 빈 값 또는 해당 게시판 허용값. 제목 200자·본문 20,000자이며 미완성/빈 값 허용, 본문의 공백·줄바꿈을 보존한다.
- Pydantic `extra="forbid"`로 미정의 키/owner 주입을 거부한다. teamCode/category는 trim(teamCode 대문자). `populate_by_name=True`로 `team_code` 같은 내부 필드명도 허용하지만 응답은 camelCase다.
- PATCH는 양의 **실제 정수** revision 필수. 문자열·boolean은 거부한다. 현재 revision과 다르면 `409`, 성공 시 실질 변경이 없더라도 revision을 1 올린다. 작성 필드를 명시적 null로 지우는 것은 합친 DraftInput 검증에서 거부된다.
- `imageIds`는 중복 없는 UUID 배열 최대 10개, 첨부 목록 전체 교체다. 생략/null은 첨부 변경 없음, 빈 배열은 연결 모두 해제. 자신 소유이며 post에 연결되지 않았고 현재 초안 또는 미연결 이미지여야 한다. 타인·다른 초안·게시 이미지·없는 ID는 `400`, 실패 시 초안/revision/첨부 모두 롤백된다.
- 초안 삭제는 이미지의 draft 연결만 해제하며 저장 객체를 지우지 않는다.

### 게시

`POST .../publish/` body는 `{revision}` 하나만 허용하고 별도 멱등 키 헤더가 필수다. 게시글의 완성된 본문/카테고리 검증을 다시 적용한다.

1. 본인 초안 행 잠금 → revision 검사 → 글 검증 → 사용자별 키 중복 검사 → 이미지 행 잠금/소유권·최대 10개 검증을 수행한다.
2. 한 DB 트랜잭션에서 글 생성, 이미지 draft→post 이동, 초안의 published_post 연결을 저장한다. 초안 원문과 revision은 그대로 남는다.
3. 실패한 초안을 삭제하거나 비우지 않는다. 성공 초안은 일반 목록·조회·수정·삭제에서 `404`로 숨긴다.
4. 동일 게시 초안에 같은 키·revision·현재 글과 일치하는 정규화 내용으로 재요청하면 기존 글 `200`. 키·revision·내용이 달라지면 `409`. 다른 게시 작업이 이미 사용한 키도 `409`.
5. 게시글 삭제 시 소비 초안도 삭제되므로 그 초안 URL로 재게시할 수 없다.

## 이미지 검증과 운영 제약

- private S3/MinIO bucket에 정규화 파일을 저장하고 Django가 읽어 전달한다. Compose의 MinIO를 사용할 때 실제 자격증명은 환경에서 안전하게 설정하고 문서/로그에 기록하지 않는다.
- 업로드 parser는 multipart 전용. 전체 body 제한은 **5MiB + 64KiB**로, Content-Length가 없거나 숫자로 해석되지 않으면 `411`, 초과하면 `413`. image 파일 하나 외 추가 필드/복수 파일은 `400`이다.
- 파일 입력과 재인코딩 결과 각각 **5MiB 이하**, 총 **2천만 픽셀 이하**. 실제 내용이 JPEG/PNG/WebP 정지 이미지이며 multipart MIME과 일치해야 한다. GIF/SVG/애니메이션/손상 파일/압축 폭탄은 거부한다.
- 서버가 EXIF 방향을 적용하고 메타데이터를 비운 뒤 재인코딩한다. 클라이언트 파일명을 저장 키로 사용하지 않는다. 업로드 후 DB 저장 실패 시 object 삭제로 보상하려고 시도하지만, 보상 실패까지 원자적으로 보장하는 것은 아니다.
- 미연결/draft 이미지는 소유자만 읽고 `Cache-Control: private, no-store`. 게시글 연결(또는 이미 게시된 draft 연결)은 공개 읽기 및 `public, max-age=60`. 응답에는 `Content-Length`, MIME, `X-Content-Type-Options: nosniff`를 붙인다.
- 삭제는 소유자만 가능, 게시 이미지는 `409`. draft에 연결된 미게시 이미지는 삭제 가능하며 별도 draft revision 증가 처리는 없다. 저장소 삭제 실패는 `503`이고 DB 행 삭제 트랜잭션은 롤백한다.
- 사용자 삭제 후 owner=null이 된 미게시 이미지는 공개되지 않는다. 게시 이미지와 메타데이터는 유지될 수 있다.
- **오래된 미연결 이미지 자동 정리 명령/스케줄은 현재 없다.** 보존 정책 확정 후 DB 행 잠금 아래 미연결·기한 초과 재검사를 수행하는 dry-run 기본 정리 절차가 필요하다. DB 삭제와 object storage 변경이 하나의 분산 트랜잭션은 아니라는 점도 운영에서 고려해야 한다.

## 예측 원천·마감·장애 동작

- date는 Python `date.fromisoformat`으로 해석한다(권장 `YYYY-MM-DD`). 없거나 빈 값이면 KST 오늘. team은 대문자 정규화, 빈 값은 필터 없음. 목록은 시작 시각·경기 ID 순서이며 페이지 처리하지 않는다.
- 오늘 조회는 원천 동기화를 시도하고 다른 날짜는 DB만 읽는다. 상세는 먼저 DB에 경기 존재 여부를 확인하므로 아직 없는 ID를 상세 호출만으로 생성하지 않는다.
- 원천 실패 시 목록은 DB 경기들이 있으면 `stale:true`로 반환, 해당 필터 결과가 없으면 `503`. 상세의 기존 경기는 `stale:true`로 반환한다. 예측 view가 생성하는 응답은 `Cache-Control: no-store`다.
- 투표는 `choice` 키만 있는 객체를 허용한다. home/away 설정과 변경, null 취소 모두 같은 경로다. 사용자·경기당 한 레코드이며 같은 선택 반복은 중복 표를 만들지 않는다.
- 투표는 항상 오늘 원천 동기화를 먼저 수행한다. 따라서 존재하지 않는 경기라도 원천 실패가 먼저 발생하면 `503`일 수 있다. 원천이 불명확하면 쓰기를 허용하지 않는다.
- 시작 시각 없음/현재 시각 이하, 또는 scheduled 외 모든 상태는 잠금 대상이다. 행 잠금 획득 후 서버 시간을 다시 확인한다. 기존 lock, void 또는 stale이면 취소 요청까지 `409`로 거부한다. 한 번 잠근 경기를 예정 상태로 되돌린다고 투표가 재개되지 않는다.
- stale: final/cancelled/postponed는 자체 판정상 false. unknown/시작 시각 없음은 true. live/suspended는 수집 후 7분 초과, 나머지는 75분 초과 또는 수집 시각이 미래 2분 초과면 true. 원천 실패 시 응답에는 강제로 true를 표시한다.
- cancelled/postponed는 void 처리. final은 점수로 home/away/draw 결과 결정. 기존 확정 결과/무효 상태를 이후 원천이 되돌리지 않도록 보존한다. void 이후에도 기존 예측 레코드를 삭제하거나 집계에서 제외하는 처리는 없다.

### 원천 입력 검증

기본은 HTTP self-call이 아니라 [tving service](../../backend/tving/service.py)의 `refresh_daily`를 직접 호출한다. `PREDICTION_SOURCE_URL`을 명시하면 HTTP/HTTPS JSON 원천을 사용한다. URL에 사용자정보/query/fragment는 허용하지 않는다.

- HTTP는 timeout 15초, 상태 200, redirect 없음, JSON Content-Type, 최대 2,000,000바이트를 요구한다.
- payload의 `data` 객체와 `error:null`, KST 오늘 날짜, 최대 20경기, timezone-aware `fetchedAt/nextCheckAt`, `stale:false`를 요구한다. 수집 75분/미래 2분 및 nextCheckAt+2분 유효성 검사.
- 경기 ID 중복·날짜 불일치·잘못된 상태/팀·같은 홈/원정·시작 날짜 불일치·범위 밖 점수(0~999, boolean 불가)를 거부한다. final은 양쪽 점수가 필수다.
- 기존 미종결 경기 누락, 같은 ID의 날짜/대진 변경은 전체 스냅샷을 거부한다. 동기화는 트랜잭션으로 적용한다.
- `PredictionSourceError`는 위 상태/오류 계약으로 처리되지만, 직접 호출한 하위 서비스에서 그 밖의 예외가 발생하는 모든 경우를 `503`으로 포괄 변환하는 것은 아니다.

## 회귀 근거와 검증 범위

| 테스트 파일 | 계약/경계 |
|---|---|
| [tests.py](../../backend/community/tests.py) | 글 필터·검증·소유권·멱등 생성·동시 요청·샘플 migration |
| [test_pagination.py](../../backend/community/test_pagination.py) | 전체 배열 호환성, page 경계·검색·`/api` 링크 |
| [test_drafts.py](../../backend/community/test_drafts.py) | 미완성 초안·extra 금지·타인 404·revision 경쟁 |
| [test_publishing.py](../../backend/community/test_publishing.py) | 이미지 소유권·원자적 이동/롤백·재시도·소비 초안 삭제 |
| [test_images.py](../../backend/community/test_images.py) | MIME/애니메이션/크기·메타데이터 제거·공개성·storage 보상/롤백 |
| [test_interactions.py](../../backend/community/test_interactions.py) | 댓글 소유권·순서·추천 최종 상태/동시성·신고 중복 |
| [test_predictions.py](../../backend/community/test_predictions.py) | 원천 fail-closed·결과 보존·선택 변경/취소·행 잠금 후 마감 재확인 |

프로젝트 루트에서 테스트 전용 PostgreSQL/pgvector DB 권한으로 실행한다. `test_<DB_NAME>` 생성/삭제가 수반되므로 운영 DB 계정을 사용하지 않는다. PostgreSQL sequence/행 잠금에 의존하는 테스트를 SQLite 통과로 대신하지 않는다.

```bash
python backend/manage.py test community --noinput
```

이 문서 작성에서는 소스 및 테스트 정의를 검토했으며 DB/API 회귀 테스트나 실제 S3·TVING 호출을 새로 실행하지 않았다. 기존 상위 README의 초안 게시 재시도·파일 보존·자동 정리 미구현 제약은 위에 보존했다.
