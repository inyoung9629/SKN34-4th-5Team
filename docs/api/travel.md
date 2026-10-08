# travel — 코스·장소·날씨·길찾기·관광 API

현재 [URL 등록](../../backend/travel/urls.py), [프로젝트 URL](../../backend/config/urls.py), 뷰·serializer·서비스·테스트를 기준으로 정리한 구현 계약이다. 아래 URL은 **Django 직접 경로**다. [Nginx](../../nginx/nginx.conf)는 `/api/`를 제거해서 전달하므로 브라우저에서는 `/api/courses/`, `/api/travel/directions/`처럼 호출한다. 끝의 `/`를 포함한다.

## 공통 규칙

2026-10-02 구장 영역 필터: 지도와 동일한 초록 경계 안의 먹거리·시설은 내부로 분류하며, 빨간 단지 중 초록 밖의 장소는 카카오 검색 응답과 챗봇·RAG 후보에서 제외합니다. 검색 결과에 선택적인 `stadiumArea: {scope: "internal" | "external", stadium: string | null}`가 포함됩니다. 필터로 현재 페이지가 비어도 공급자가 다음 페이지를 제공하면 `hasNextPage`는 유지됩니다. 저장 데이터는 삭제하지 않습니다. [분류 우선순위와 적용 경로](course-policy-v1.md#2026-10-02-구장-단지-검색-필터)를 참고하세요.

2026-10-02부터 코스 작성 지도의 주변 장소 목록은 `POST /api/v1/places/live/`로 카카오를 실시간 조회합니다. 조회 결과를 Place DB에 자동 저장하지 않으며, 주변 핀은 선택 전에는 표시하지 않습니다. 숙박은 기존 `/api/v1/places/lodging/`에서 필요할 때 조회하고 구장 시설은 별도 목록을 유지합니다. 챗봇의 자동 코스 후보는 이번 변경 대상이 아니므로 [수집 장소/RAG](collected-places.md)를 계속 사용합니다. 길찾기·날씨 제공자는 유지합니다.

- 기본 인증은 [설정](../../backend/config/settings.py)의 JWT(`Authorization: Bearer <access-token>`). `AllowAny`는 토큰 없이 호출 가능하다는 뜻이지 잘못된 JWT를 무시한다는 뜻은 아니다. `/weather/`만 인증 클래스 자체를 비활성화한다.
- 아래 표는 업무 메서드다. 미지원 메서드는 405이며 DRF의 OPTIONS 등은 별도다. 페이지네이션은 명시된 API에만 있다.
- 코스·장소 쓰기와 길찾기는 JSON 요청이다. 오류 형식은 API별로 다르므로 모든 실패를 `{error: ...}`로 처리하면 안 된다.
- 외부 서비스 응답과 DB 동기화 시점은 다르다. `EXTERNAL_DATA_SYNC_INTERVAL_SECONDS`(기본 600초)는 **저장된 행의 갱신 간격**이며 외부 API 호출 캐시 TTL이 아니다. 장소·길찾기·관광은 요청마다 공급자를 조회하고, 새 행/동기화 시각 없음/간격을 **엄격히 초과**한 행을 갱신한다. 정확한 경계나 미래 시각의 행은 재갱신하지 않는다.

## 등록된 API 전체

| Django URL | 메서드 | 권한 | 정상 응답 |
|---|---|---|---|
| `/courses/` | GET | 공개 | 200 코스 배열, 최신 생성순 |
| `/courses/` | POST | 공개 | 201 코스 + 최초 1회 `editToken` |
| `/courses/<uuid:pk>/` | GET | 공개 | 200 코스 |
| `/courses/<uuid:pk>/` | PATCH / DELETE | 공개 + 편집 토큰 | 200 코스 / 204 본문 없음 |
| `/courses/<uuid:pk>/reaction/` | GET / POST | 로그인 | 200 `{liked, likes}` |
| `/courses/<uuid:pk>/view/` | POST | 공개 + 조회 토큰 | 200 `{views}` |
| `/places/search/` | POST | 공개 | 200 `{places, hasNextPage, syncedAt}` |
| `/places/live/` | POST | 공개 | 200 같은 검색 응답, DB 읽기·쓰기 없음, `Cache-Control: no-store` |
| `/places/collected/` | GET | 공개 | 200 수집 장소 목록·출처·스냅샷 정보; [상세 계약](collected-places.md) |
| `/places/` | GET | 공개 | 200 `{count, page, page_size, results}` |
| `/places/` | POST | 활성 staff | 201 장소 |
| `/places/<int:pk>/` | GET | 공개 | 200 장소 |
| `/places/<int:pk>/` | PATCH / DELETE | 활성 staff | 200 장소 / 204 본문 없음 |
| `/weather/` | GET | 인증 처리 없음 | 200 `{weather: object 또는 null}` |
| `/travel/directions/` | POST | 공개 | 200 `{mode, legs, distance, seconds}` |
| `/tourism/` | GET | 공개 | 200 관광 검색 결과 |

`directions_service`의 목록·수정·삭제 및 `tourism_tools`의 엔터티 CRUD는 **Python 호출용 함수이며 등록된 HTTP API가 아니다**.

## 코스

근거: [views.py](../../backend/travel/views.py), [serializers.py](../../backend/travel/serializers.py), [models.py](../../backend/travel/models.py), [회귀 테스트](../../backend/travel/tests.py).

### 생성·수정 입력

실제 검증은 `CourseSerializer`가 수행한다. OpenAPI 전용 `CourseCreateRequestSerializer`와 달리 런타임 `tags`는 생략 가능하다(모델 기본값 `[]`).

| 필드 | 조건 |
|---|---|
| `title` | 생성 필수, 공백 제거 후 비어 있지 않은 문자열, 최대 80자 |
| `stadium` | 생성 필수, 최대 120자. 날씨의 구장 코드 enum과 달리 자유 문자열 |
| `duration` | 생성 필수, 최대 80자 |
| `content` | 선택, 빈 문자열 허용, 최대 12,000자 |
| `contentFormat` | 선택, `""` 또는 `"html"` |
| `tags` | 선택, 문자열 배열 |
| `startLat`, `startLng` | 선택/null 가능. 둘 다 값이 있거나 둘 다 null이어야 함 |
| `stops` | 생성 필수, 1~12개. PATCH에서 주면 **기존 전체 목록 교체** |

좌표는 유한수이며 위도 -90~90, 경도 -180~180이다. `stops` 각 항목의 필수값은 `position`, `name`(255자), `lat`, `lng`, `category`(120자). `position`은 중복 없이 0부터 연속이어야 한다. 선택값은 `placeId`, `visitId`, `address`, `tourContentId`, `isMapPoint`, `isDrawnPoint`이며 null/빈 문자열 허용 여부는 serializer를 따른다. PATCH도 전달한 각 stop은 필수 필드를 모두 갖춰야 한다.

`id`, `sampleId`, `routeNumber`, `description`, `cover`, `author`, `likes`, `views`, `isSample`, `createdAt`, `updatedAt`은 읽기 전용이다. 이 serializer는 장소/관광처럼 알 수 없는 입력 키를 일괄 거부하는 strict serializer가 아니다. HTML 형식 표시는 서버 HTML 정화 보장을 뜻하지 않는다.

```json
{
  "title": "잠실 산책", "stadium": "잠실야구장", "duration": "2시간",
  "tags": ["산책"],
  "stops": [{"position": 0, "name": "잠실야구장", "lat": 37.5162, "lng": 127.0759, "category": "야구장"}]
}
```

### 응답·편집·반응

코스 응답은 `id, routeNumber, title, stadium, description, content, duration, cover, tags, author, likes, views, isSample, createdAt, updatedAt, stops`와 조건부 `sampleId, contentFormat, startLat, startLng`이다. `sampleId=null`, 빈 `contentFormat`, 없는 출발 좌표는 키가 생략된다. stop의 null 필드도 생략된다. 목록에는 별도 검색 필터/페이지네이션이 없다.

- 생성 시 암호학적 난수 `editToken`을 한 번 반환하고 DB에는 해시만 저장한다. PATCH/DELETE는 `X-Course-Edit-Token` 필요. 로그인이나 staff가 편집 토큰을 대체하지 않는다. 조회에서는 토큰을 재발급/공개하지 않는다. 시드 샘플은 클라이언트가 사용할 편집 토큰이 없어 일반 편집할 수 없으며 복제 생성은 가능하다.
- 생성/stop 교체는 트랜잭션이다. 검증·저장 실패 시 코스와 stop 변경을 함께 롤백한다.
- `GET reaction`은 현재 회원의 좋아요 여부, `POST reaction`은 `{ "liked": true 또는 false }`를 받는다. 토글이 아니라 **원하는 상태의 멱등 적용**이며 `(course,user)` 유일성과 행 잠금으로 카운터를 보호한다.
- `POST view`는 본문 없이 UUID `X-Course-View-Token`을 보낸다. 정규화한 UUID의 SHA-256과 코스 조합으로 한 번만 증가한다. 사람/회원 식별이 아니며 새 토큰은 새 조회로 집계된다. GET 상세 조회가 자동 집계하지 않는다.
- POST/PATCH/DELETE는 `Sec-Fetch-Site: cross-site` 또는 요청 scheme/host와 다른 `Origin`을 403으로 거부한다. Origin 부재는 허용한다. JSON 본문은 64,000바이트 이하이며 잘못된 Content-Length도 413이다.
- 쓰기 throttle은 IP 기준 `course_write=30/hour`(조회 집계 POST 포함). DRF `NUM_PROXIES=1` 전제이므로 신뢰하는 프록시가 전달 헤더를 덮어써야 한다.

오류: 검증/조회 토큰 400, 로그인 필요 401, 교차 출처·편집 토큰 403, 코스 없음 404, 크기 413, 비JSON 415, throttle 429. 보통 `{detail: ...}` 또는 필드별 오류이며 저장소 예외를 모두 사용자 정의 오류로 변환하지는 않는다.

## 장소

근거: [뷰](../../backend/travel/place_views.py), [입출력](../../backend/travel/place_serializers.py), [서비스](../../backend/travel/place_service.py), [권한](../../backend/baseball/permissions.py), [테스트](../../backend/travel/test_places.py).

### 저장 장소 CRUD

- GET `/places/`: `name` 부분 일치, `category`는 그룹 코드 일치 **또는** category_name 부분 일치, `kakao_place_id` 정확 일치. 필터 문자열은 공백 제거 후 1~100자. `page` 기본 1/범위 1~10000, `page_size` 기본 20/범위 1~100. 알 수 없는 쿼리는 400. ID 오름차순이며 결과 밖 페이지는 빈 배열이다.
- 생성 필수: `name`(255자), `lat`, `lng`. 선택: `kakao_place_id`(null 가능, 100자), `address`, `road_address`(각 500자), `category_group_code`(20자), `category_group_name`(100자), `category_name`(255자), `phone`(50자), `url`(500자 URL). 좌표는 문자열/bool이 아닌 유한 JSON 숫자다.
- PATCH는 위 필드 중 `kakao_place_id`를 제외한 비어 있지 않은 부분 객체. ID·동기화 시각 등 알 수 없거나 불변인 필드는 거부한다. 생성·수정·삭제는 인증된 `is_active && is_staff`만 가능하며 서비스에서도 재검증한다.
- 응답: `id, kakao_place_id, name, address, road_address, category_group_code, category_group_name, category_name, phone, lat, lng, url, created_at, updated_at, last_synced_at`. 수동 변경은 공급자 동기화 시각을 갱신하지 않는다.

### 지도용 카카오 실시간 조회 (2026-10-02)

- 브라우저 경로 `POST /api/v1/places/live/`. 입력·응답 형식은 아래 기존 검색 API와 같지만 Place 테이블을 읽거나 갱신하지 않는다. 기존 `/places/search/`의 저장 동작은 호환용으로 유지한다.
- 서버의 카카오 REST 키와 기존 타임아웃·동시성·호출 제한을 사용한다. 브라우저로 REST 키를 보내지 않으며 검색 실패 시 소상공인 수집본·유료 웹검색으로 자동 대체하지 않는다.
- 지도 목록은 검토된 구장 좌표 기준 2.5km, 검색식 17개(숙박 제외), 검색식별 최대 3페이지×15건, 동시 요청 2개다. 페이지 결과를 Kakao ID로 중복 제거하고 즉시 목록에 반영한다. 실제 호출 수는 다음 페이지 유무에 따라 줄어들며 전체 상점 목록을 보장하지 않는다.
- 지도 생성은 장소 검색을 기다리지 않는다. 검색어·분류 필터 변경은 이미 조회한 결과만 필터링하고 구장 변경·화면 이탈은 이전 요청을 취소한다. 원본 응답은 화면 메모리에서만 사용한다. 이 조회의 무저장 정책과 사용자가 명시적으로 저장한 기존 코스 데이터는 별개다.
- 구장 시설 이름·주소 및 검토된 전체 구장 테두리 안의 결과는 외부 주변 목록에서 분리한다. 내부 시설은 기존 전용 목록을 사용한다. 핀은 선택한 장소의 작은 점과 코스 순서 핀만 표시한다. 새 프론트 선택 박스는 추가하지 않는다.

### 카카오 실시간 검색과 저장 (기존 호환 API)

POST `/places/search/` 입력은 `method, lat, lng, page, size, sort` 필수, `keyword, category, radius` 선택이다.

- `method`: `keyword` 또는 `category`; keyword 방식에는 비어 있지 않은 `keyword`(최대 100자), category 방식에는 지원 `category` 필수.
- category: `FD6, CE7, AT4, CT1, CS2, AD5`만 가능. keyword 방식의 선택 category도 동일 제한.
- `page=1..3`, `size=1..15`, `sort=accuracy|distance`, 선택 `radius=1..20000`미터. 숫자는 실제 JSON 숫자/정수여야 하며 알 수 없는 필드는 거부한다.
- 고정 Kakao Local keyword/category URL, `KAKAO_REST_API_KEY`, 8초 타임아웃, 응답 262,144바이트 제한, 리다이렉트 금지. 현재 페이지 전체 정규화·중복 ID 검증 후 트랜잭션 upsert한다. 중간 실패는 부분 저장 성공으로 보고하지 않는다.
- 응답 `places`는 DB serializer가 아닌 카카오 형식: `id, place_name, road_address_name, address_name, category_group_name, category_group_code, category_name, phone, x, y`. `x/y`는 문자열 경도/위도다. `hasNextPage = !meta.is_end`. `syncedAt`은 **이번 외부 조회 시각**으로 모든 행의 DB 갱신 시각을 의미하지 않는다.
- 이미 신선한 행은 DB 갱신을 건너뛰어도 응답에는 최신 공급자 페이지를 반환한다. 빈 페이지가 기존 장소를 삭제하지 않는다.
- DRF IP throttle `240/minute`와 별개로 프로세스 내 동시 8건/분당 시작 240건 제한. POST/PATCH 12,000바이트, JSON만 허용한다.

장소 API 실패는 `{ "error": "안전한 한국어 메시지" }`로 통일: 400 입력, 401 로그인, 403 관리자, 404 없음, 409 ID 충돌, 413 크기, 415 형식, 429 제한, 502 공급자, 503 설정/저장소. 일반적인 신규 중복 `kakao_place_id`는 ModelSerializer 유일성 검증에서 **400**이 될 수 있으며 DB IntegrityError 경로가 409로 변환된다. 공급자 HTTP 오류를 세분화하지 않아 Kakao HTTP 429 자체도 502 경로다.

## 날씨

근거: [뷰](../../backend/travel/weather_views.py), [응답 스키마](../../backend/travel/weather_serializers.py), [서비스](../../backend/travel/weather_service.py), [테스트](../../backend/travel/test_weather.py).

GET `/weather/?stadium=JAMSIL&date=YYYY-MM-DD&time=HH:MM`.

- 세 값 필수. 구장 코드는 `JAMSIL, GOCHEOK, MUNHAK, SUWON, DAEJEON, DAEGU, GWANGJU, SAJIK, CHANGWON`.
- 날짜/시간은 실제 달력의 KST 시각이며 **현재 기준 과거 24시간~미래 120시간** 범위. 날짜 단위가 아닌 이동하는 시간 구간이다. 뷰는 이 세 쿼리만 읽고 기타 키를 strict 거부하지 않는다.
- 구장 고정 좌표를 기상청 격자로 변환. `min(현재, 목표)-1시간` 이전의 02/05/08/11/14/17/20/23시 발표를 선택하고 목표 시각은 가장 가까운 정시(30분은 다음 정시)로 반올림한다.
- 기상청 `getVilageFcst`, `KMA_SERVICE_KEY` 우선/`KMA_API_KEY` 대체, 12초 타임아웃, JSON 1MiB 제한, 리다이렉트 금지, 프로세스 내 동시 4건. DB 저장/캐시/자동 재시도 없음. 모든 응답 `Cache-Control: no-store`.
- 성공 `weather`: `label, temperature, forecastAt, issuedAt, fetchedAt, source`(`기상청 단기예보`). TMP/SKY/PTY가 완전하고 유한해야 하며 강수 라벨을 하늘 상태보다 우선한다. 해당 예보 시각이 없거나 공급자 정상 무자료이면 **200 `{weather:null}`**.
- 실패 `{weather:null,error:{code}}`: 400 `invalid_request`, 429 `weather_busy`, 503 `weather_not_configured` 또는 `provider_unavailable`, 502 `provider_response_invalid`. 키·공급자 원문을 반환하지 않는다.

## 길찾기

근거: [뷰](../../backend/travel/views.py), [입출력](../../backend/travel/serializers.py), [공급자](../../backend/travel/directions_provider.py), [저장 서비스](../../backend/travel/directions_service.py), [모델](../../backend/travel/directions_models.py), [테스트](../../backend/travel/test_external.py).

POST `/travel/directions/`:

```json
{"mode":"walk","points":[{"lat":37.5162,"lng":127.0759},{"lat":37.51,"lng":127.08}]}
```

`mode=walk|car|transit`, `points` 2~13개. 선택 `action`은 `directions`만 가능하며 실행 전에 제거된다. 객체의 다른 키는 거부한다. 좌표는 유한 JSON 숫자/bool 금지, 위·경도 범위 검증. JSON 본문 12,000바이트 제한.

인접 점 쌍별로 공급자를 조회한다. car는 Kakao Mobility `/v1/directions`, walk/transit은 코드에 지정된 Kakao `/v2/routing/walk`, `/v2/routing/publictraffic`을 사용한다. `KAKAO_REST_API_KEY`가 필요하다. 이 경로의 실제 외부 제공/계정 사용 가능 여부는 별도 운영 확인 대상이다. 10초 타임아웃, 최대 2,000,000바이트, 동시 3건, 리다이렉트 금지. transit은 공급자가 준 최소 totalTime 경로를 선택하며 임의 거리·시간 추정으로 성공 처리하지 않는다.

- 응답 leg 성공: `{status:"ok",distance,seconds,paths,instructions}`. distance 미터, seconds 초, paths는 `{lat,lng}` 배열들의 배열이다.
- 같은 점 쌍은 외부 호출 없이 거리/시간 0. **모든 점이 같으면 키 없이도 성공**한다.
- 성공한 구간은 `DirectionsRoute`에 저장. identity는 mode와 시작/종료 좌표 소수 6자리이며 거리·시간은 저장 시 반올림한다.
- 공급자 실패는 구간별로 잡는다. 저장된 경로가 있으면 그 값에 `status:"error",stale:true,warning,error`를 붙이고, 없으면 `status:"error",paths:[],instructions:[],error`를 반환한다(이 경우 leg `distance/seconds` 키 자체가 없을 수 있다).
- 하나라도 실패한 leg가 있으면 전체 `distance/seconds=null`이지만 **HTTP는 200**이다. 저장 fallback은 정상 최신 결과가 아니다. 공급자 429도 보통 이 구간 실패 경로로 처리된다.
- 입력 검증은 400 필드 오류, 크기 413/비JSON 415는 DRF 오류. 키 없음은 503 `{error:"지도 데이터 연결 설정이 필요해요."}`. 뷰까지 전파된 `DirectionsError`는 해당 상태와 안전한 `error`를 반환하지만 DB/일반 예외까지 변환하지 않는다. 스키마의 429/502만 보고 모든 공급자 실패가 HTTP 오류라고 해석하지 않는다.

## 관광

근거: [뷰](../../backend/travel/tourism_views.py), [serializer](../../backend/travel/tourism_serializers.py), [공급자](../../backend/travel/tourism_provider.py), [동기화](../../backend/travel/tourism_service.py), [Python 도구](../../backend/travel/tourism_tools.py), [테스트](../../backend/travel/test_tourism.py).

GET `/tourism/?stadium=JAMSIL&lat=37.5162&lng=127.0759`. 세 값 필수이며 다른 쿼리는 거부한다. 구장 코드는 날씨와 동일한 9개, 유한 좌표 범위와 해당 구장으로부터 1km 이내 조건을 검증한다. 임의 반경·페이지·종류 필터를 받지 않는다.

- `TOUR_API_KEY` 미설정은 오류 HTTP가 아니라 **200** `{status:"unconfigured",places:[],truncated:false,stale:false,fetchedAt:null,lastSyncedAt:null}`.
- TourAPI `KorService2/locationBasedList2`, 반경 2,500m, content type `12/14/28`, 종류별 페이지당 100개·최대 3페이지. 8초 타임아웃/2MiB/리다이렉트 거부. 페이지 상한 도달은 `truncated:true`; 일부 호출·레코드 실패와 성공이 섞이면 `status:"partial"`. 실패 페이지 자체를 자동 재시도하지 않고 해당 종류를 중단한 뒤 다음 종류를 처리한다.
- 구장 내부/너무 가까운 지점·종료성 행사 제목·부적합 레포츠 등을 제거하고 `walk/sight/indoor`로 분류한다. canonical 구장 기준 거리/ID 순 정렬, placeId 중복 제거.
- 결과 `{status,places,truncated,stale,fetchedAt,lastSyncedAt}`. 정상 조회의 `stale=false`, `fetchedAt`은 이번 조회, `lastSyncedAt`은 반환 엔터티 중 최대 저장 동기화 시각(없으면 null). 실패 시 저장된 관광 목록으로 fallback하지 않는다.
- 각 장소: `placeId:"tour:<id>",tourContentId,contentTypeId,name,lat,lng,category,kind,cuisine,address,phone,detail,distance`, 선택 `subcategory,imageUrl`; serializer는 `sourceUrl`도 허용하나 현재 공급자 정규화는 이를 생성하지 않는다. category는 산책/관광 명소/실내 놀거리, cuisine은 기타, distance는 미터.
- 정상/partial 결과를 검증 후 `Place` 공통 행 + `TourismPlace` 일대일 행으로 저장한다. content_id 유일성, 정렬된 PostgreSQL advisory lock, 트랜잭션으로 중복·고아 행을 방지한다. 카카오 연결 Place는 관광 갱신이 덮어쓰지 않는다. 누락된 기존 행을 삭제하거나 카카오 숫자 ID와 Tour ID를 임의 병합하지 않는다.
- IP throttle `20/minute`, 프로세스 동시 3건. 입력 400은 DRF 필드 오류, DRF throttle 429는 `detail` 형식. 동시 제한 429/공급자 오류 429·502·503/일반 예외 502는 `{status:"error",places:[],truncated:false}`. 모든 오류가 TourismResponse 전체 필드를 갖지는 않는다.

## 주요 저장 모델과 호출 경계

| 모델 | 역할 |
|---|---|
| `Course` / `CourseStop` | UUID 코스/순서 있는 방문 장소. 6자리 routeNumber는 PostgreSQL sequence에서 발급 |
| `CourseReaction` / `CourseView` | 회원별 좋아요/익명 조회 capability 해시의 코스별 유일성 |
| `Place` | 카카오·수동·관광 공통 장소, nullable unique kakao_place_id |
| `DirectionsRoute` | 이동수단+정규화 좌표 쌍별 마지막 유효 경로 |
| `TourismPlace` | Place 일대일 관광 확장, 고유 content_id와 공급자 시각 |
| `ExternalProviderSnapshot` | 과거 외부 payload 모델/전환 지원. 현재 길찾기·관광 검색의 주 저장소로 오인하지 말 것 |

[외부 snapshot 서비스](../../backend/travel/external_snapshot_service.py)와 [마이그레이션](../../backend/travel/migrations)은 레거시 전환 근거다. [LLM 도메인 도구](../../backend/llm/tools/domain.py)는 공개 조회 서비스를 재사용하며, [LLM README](llm.md)와 기존 [RAG 문서](../../backend/llm/rag/README.md)를 참고한다. 모델/서비스에 함수가 있다고 URL이 자동 생성되지는 않는다.

## 검증 범위

위 링크의 테스트에서 토큰·출처·본문 제한, 소유자/관리자 경계, 롤백, 동시 upsert, 갱신 간격 경계, 공급자 오류/부분 성공, 날씨 시간 계산을 확인할 수 있다. 이 README 작성 시에는 **소스와 테스트를 정적 대조하고 로컬 링크를 검사**했으며 Django 테스트·실제 DB·외부 유료 API는 실행하지 않았다. 키 실제값은 읽지 않았고 공급자 가용성을 검증했다는 의미도 아니다.
