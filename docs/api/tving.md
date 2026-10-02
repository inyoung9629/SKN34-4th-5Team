# tving — TVING KBO 수집·관계형 조회 API

TVING KBO 일정·순위·구단·선수 정보를 요청 시 수집하고 검증하여 공유 야구 DB에 저장하는 앱이다. 단순 HTTP 프록시가 아니며 **공개 GET도 데이터 갱신·DB 쓰기를 일으킬 수 있다**. 일반 야구 데이터의 저장분 조회·19개 리소스 관리자 CRUD는 [baseball](baseball.md)에 있다.

## 구현 위치와 모델 소유권

[루트 URL](../../backend/config/urls.py) → [앱 URL](../../backend/tving/urls.py) → [뷰](../../backend/tving/views.py) → [서비스](../../backend/tving/service.py) → [원천 파서](../../backend/tving/parsers.py) / [관계형 저장·재구성](../../backend/tving/relational.py).
[serializer](../../backend/tving/serializers.py)는 요청 검증과 응답 스키마를 정의한다. 응답은 뷰에서 dict로 직접 생성하므로 응답 serializer가 실행 시 모든 원천 필드를 재검증하는 것은 아니다.

현재 `tving/models.py`는 없으며 실행 모델은 모두 [baseball/models.py](../../backend/baseball/models.py)에 있다. 예전 `Tving*` 이름이 [마이그레이션](../../backend/tving/migrations/0005_delete_tvingscheduleday_and_more.py)에 남아 있다고 현행 API 모델로 해석하지 않는다.

| 공유 모델 | 역할·주요 식별자 |
| --- | --- |
| `Team` | TVING 2자리 코드와 내부 구단 코드 연결, 기존 구단 필요 |
| `Game` | 경기. 외부 `source_external_code` 유일, 기존 CSV 경기와 매칭 가능 |
| `StandingHistory` | `(team, snapshot_date)`별 순위. 현재 원천 순위를 요청 날짜에 저장 |
| `ScheduleDay` | 날짜 유일. 상태·경기 수·활성 외부 경기 코드 목록으로 완전성 확인 |
| `TeamProfile`, `TeamSeasonRecord` | 구단별 프로필, `(team, season, category, title)`별 기록 |
| `Player` | `external_code` 문자열 PK, 소속 구단, 프로필/신원/상세별 조회·동기화 시각 |
| `PlayerSeasonRecord`, `PlayerCareerRecord` | 시즌별 종류/키 기록, 선수별 통산 행 위치와 JSON 지표 |
| `TeamRoster`, `TeamTopPlayer` | 구단 선수단 및 투수/타자 부문 상위 기록 |
| `ProviderSnapshot` | `(resource_kind, resource_key)` 유일한 레거시 정규화 JSON. 실시간 갱신의 주 저장소가 아님 |

`Team` 등 상위 구단 참조는 `PROTECT`; 선수 삭제 시 선수 시즌/통산 기록·선수단·상위 기록은 `CASCADE`다. 대부분의 동기화 모델에는 `source_fetched_at, last_synced_at, updated_at`이 있다.

## 데이터 흐름·외부 서비스

```text
공개 갱신 GET → 관계형 데이터 재구성 + 완전성/동기화 시각 확인
  ├─ 유효 기간 내 → DB 표현 반환
  └─ 만료/없음 → TVING HTTPS → 파서 검증 → 트랜잭션 upsert → 반환
                    └─ 처리 가능한 원천/검증 오류 + 기존 표현 있음
                         → 기존 표현 반환(stale=true, warning)
스냅샷 CRUD → ProviderSnapshot만 변경
레거시 backfill 명령 → 스냅샷을 읽어 관계형 모델에 반영(원본 보존)
```

- 원천 base URL은 `https://gw.tving.com/bff/sports/v2`, 표시 출처는 `https://www.tving.com/sports/kbo`다. 사용자 지정 URL을 받지 않는다.
- 허용 원천 경로: `/kbo/schedule`, `/kbo/schedule/day`, `/kbo/history/team`, `/kbo/history/athlete/ranking`, `/team`, `/kbo/history/athlete/top5`, `/roaster/item`, `/athlete`.
- 요청마다 timeout 15초, JSON Content-Type·HTTP 200·응답 URL 일치 확인, redirect 차단, 응답 최대 4,000,000 bytes. 코드상 TVING 로그인/별도 API 키를 전송하지 않는다. Origin/Referer/Accept 헤더를 보낸다.
- 일간은 일정·팀순위·투수/타자 순위를 병렬 조회하며 필요한 경우 월 달력을 추가 확인한다. 월간은 달력 후 경기일별 조회(최대 5 worker), 팀은 프로필·2종 상위 기록·4개 포지션 선수단(최대 7 worker), 선수는 상세를 가져온다. 15초는 각 HTTP 요청 제한이지 전체 API 처리 시간 제한이 아니다.
- [설정](../../backend/config/settings.py)의 `EXTERNAL_DATA_SYNC_INTERVAL_SECONDS` 기본값은 600초다. 관계형 자료가 완전하고 동기화 시각이 충분히 최근이면 원천 호출을 생략한다. 요청 기반이며 예약 작업/백그라운드 전체 수집기를 의미하지 않는다.
- 저장은 트랜잭션과 행 잠금을 사용하고 행별 갱신 주기도 확인한다. 갱신 시 선택적 null/빈 문자열은 기존 값을 지우지 않으며 `0`, `false`, 빈 컬렉션은 명시값이다. 원천에서 사라진 모든 행을 물리 삭제하는 방식은 아니다. 활성 선수단/통산 키 목록 등으로 응답을 재구성한다.
- CSV와 TVING 경기는 외부 ID, 날짜·시간·상대팀 등으로 매칭하며 모호한 후보는 실패한다. 신규 경기는 `stadium=None`, `game_type="UNKNOWN"`일 수 있다. 원천 구장명이 있어도 자동으로 `Stadium` FK를 확정하지 않는다.
- 파서는 원천 구조·개수·날짜·팀 코드·숫자와 문자열을 검증한다. 이미지 URL은 HTTPS `image.tving.com`만 허용한다. 일간/월간의 경기 상태는 `scheduled/live/final/cancelled/suspended/unknown` 등 정규화 값이며 팀 일정 표현은 원천식 상태 코드로 재구성될 수 있다.

### 신선도와 역사 데이터의 한계

갱신 응답에는 `fetchedAt`, `providerFetchedAt`, `updatedAt`, `lastSyncedAt`, `source:{name,url}`, `stale`, `warning`이 들어간다. DB 재사용 경로에서는 fetched 계열도 저장 동기화 시각으로 표시하므로 **이번 요청이 실제 TVING에 접속했다는 증거가 아니다**. 성공은 HTTP 200이어도 `stale=true`일 수 있다. DB 장애 등 모든 예외가 fallback되는 것은 아니며 바깥 뷰에서 503으로 처리될 수 있다.

TVING 팀순위/개인순위는 시즌 조회이며 과거 날짜별 순위 API가 아니다. `/daily/?date=과거날짜`가 당시 순위를 복원한다고 보장할 수 없다. `get_standings_freshness()`는 과거/미래 순위를 저장분으로 제한하지만, 공개 `daily` 경로는 `refresh_daily()`를 직접 호출하므로 그 보호 분기와 다르다. 개인 시즌 순위도 날짜별 스냅샷으로 보존되지 않는다. `get_game_range_freshness()`의 미래 연도 제한 역시 내부 호출용이며 공개 월간 endpoint의 입력 제한으로 가정하지 않는다.

## URL·인증·공통 응답

아래는 **Django 직접 경로**다. [Nginx](../../nginx/nginx.conf)를 통하면 `/api/tving/...`이며 `/api/`를 제거하고 backend로 전달한다. [OpenAPI 설정](../../backend/config/settings.py)의 `/api` 접두어와 Django URL을 혼동하지 않는다.

기본 인증은 JWT `Authorization: Bearer <access-token>`. 공개 GET은 `AllowAny`, 수정은 `IsAdminUser`이며 서비스도 인증된 `is_staff` 사용자를 검사한다. `baseball.ActiveStaffOnly`와 동일 클래스는 아니며 서비스에서 별도로 `is_active`를 검사하지 않는다(기본 JWT 인증의 비활성 사용자 검사는 별개). 공개 경로도 잘못된 토큰을 보내면 인증 실패할 수 있다.

- 일반 성공: `{ "data": ..., "error": null }`.
- 목록: `data={count, page, pageSize, results}`. `page` 기본 1, 범위 1..10000; `page_size` 기본 50, 범위 1..100. 최대 초과를 조용히 자르는 대신 400이다. 유효 범위 내 빈 페이지는 빈 results다.
- 갱신·상태 응답과 서비스 오류는 `Cache-Control: no-store`. entity/snapshot의 모든 성공 응답에 동일 헤더가 붙는 것은 아니다.
- 이 앱은 **DefaultRouter를 사용하지 않는다**. 아래 명시적 URL/메서드가 전체 업무 API이며 자동 범용 CRUD나 PUT은 없다. APIView의 HEAD/OPTIONS 같은 프레임워크 처리와 업무 메서드는 구분한다.

## 공개 조회 API

| 메서드·Django 경로 | 요청 필드·필터 | `data` 주요 응답 |
| --- | --- | --- |
| `GET /tving/daily/` | `date=YYYY-MM-DD`, 생략 시 한국 오늘 | `date, games[], standings[], individualRankings:{pitchers,hitters}, sourceUpdatedAt, nextCheckAt, mode="fixed-interval"` + 신선도 |
| `GET /tving/schedule/` | `month=YYYY-MM`, 생략 시 한국 현재가 2026년이면 현재 월, 아니면 **2026-12** | `year, month, today, games[], days:[{date,status,gameCount}], loading=false` + 신선도 |
| `GET /tving/details/teams/{value}/` | TVING 구단 코드; 대문자로 정규화 | `code, teamName, shortName, teamImageUrl, backgroundImage, seasonTitle, mainRecords[], boxRecords[], schedule[], rankings, rosters, shortcuts[], collecting=false, progress` + 신선도 |
| `GET /tving/details/athletes/{value}/` | 숫자 문자열 선수 코드, 길이 4..12 | `profile, seasonTitle, seasonRecords[], careerTitle, careerColumns[], careerRows[], collecting=false, progress` + 신선도 |
| `GET /tving/details/status/` | 없음 | 아래 수집 상태 |
| `GET /tving/entities/` | 필수 `kind`; 선택 `team, player, date, month, page, page_size` | 관계형 entity 페이지; 종류별 아래 표 |
| `GET /tving/entities/players/{code}/` | 실제 Player PK 문자열 | 선수 entity; 없으면 404 |
| `GET /tving/snapshots/` | `kind, key, team, player, date, month, page, page_size` | snapshot 페이지 |
| `GET /tving/snapshots/{pk}/` | 정수 PK | snapshot; 없으면 404 |

`/entities/players/` 자체에는 GET 목록이 없다. 선수 목록은 `/entities/?kind=player`다. `details`와 `daily/schedule`은 갱신 가능 경로, `entities/snapshots`는 저장분 조회 경로다.

TVING 팀 코드는 `SS, KT, LG, HT, OB, NC, HH, LT, SK, WO`이며 [TEAM_MAP](../../backend/tving/relational.py)의 내부 코드는 각각 `SAMSUNG, KT, LG, KIA, DOOSAN, NC, HANWHA, LOTTE, SSG, KIWOOM`이다. 내부 Team 선행 적재가 빠지면 서비스가 실패한다. 경기 원천에는 올스타 `WE/EA`도 가능하지만 일반 팀 필터에는 허용되지 않는다.

상태 `progress`는 `state=idle|partial`, `generation/startedAt/completedAt=null`, `teamTotal=10`, `teamDone=TeamProfile 수`, `athleteTotal=athleteDone=프로필 동기화 선수 수`, `failures=[]`, `strategy="on-demand"`다. 전체 선수 총원 대비 진행률이나 실제 실행 중인 작업 추적기가 아니다.

### 관계형 entity 종류별 계약

`team`은 TVING 코드를 대문자로 정규화한다. `player`는 비어 있지 않은 최대 40자이며 이름 부분 검색이 아니라 해당 종류의 외부 코드/FK 정확 일치다. `date/month`는 형식을 검증하더라도 해당 종류가 아래 필터를 쓰지 않으면 결과 필터링에 적용되지 않는다.

| `kind` | 적용 필터 | 결과 필드 |
| --- | --- | --- |
| `game` | `team, date, month`; source=tving만 | `id, gameCode, tvingCode, date, time, homeTeam, awayTeam, status, lastSyncedAt` |
| `standing` | `team, date, month`; source=tving만 | `id, teamCode, date, rank, wins, draws, losses, lastSyncedAt` |
| `team` | `team` | `id, teamCode, name, seasonTitle, lastSyncedAt` |
| `player` | `team, player` | `externalCode, teamCode, name, imageUrl, positions, backNumber, profileLastSyncedAt` |
| `roster` | `team, player` | `id, teamCode, playerCode, position, lastSyncedAt` |
| `player-season` | `team, player` | `id, playerCode, season, kind, rank, metrics, lastSyncedAt` |
| `team-top` | `team, player` | `id, teamCode, playerCode, athleteType, category, rank, lastSyncedAt` |

종류별 코드 표현도 같지 않다. `team/player`의 `teamCode`는 TVING 코드, `standing/roster/team-top`은 내부 구단 코드다. `game`은 FK가 있으면 내부 코드, 없으면 원천 코드를 반환한다. 통산 선수 기록 전용 entity kind나 경기 entity 상세 수정 API는 구현되어 있지 않다.

### 스냅샷 검색과 표현

`kind=daily|month|team|athlete`, `key`는 1..16자 정확 일치다. `team`은 유효 TVING 코드, `player`는 1..80자이고 제어문자를 허용하지 않는다. `date/month`는 ISO 형식을 검사한다. **team/player/date/month 검색은 payload의 `icontains` 텍스트 검색**이며 정규화된 FK 조인이나 의미론적 날짜 필터가 아니다. 결과는 kind/key 순이다.

스냅샷 객체 필드: `id, resourceKind, resourceKey, payload, sourceFetchedAt, lastSyncedAt, createdAt, updatedAt`. 공개 GET에 payload가 포함되므로 관리자가 여기에 비밀값을 저장해서는 안 된다.

## 관리자 명시적 CRUD

| 메서드·Django 경로 | JSON 요청 | 성공 |
| --- | --- | --- |
| `POST /tving/entities/players/` | 필수 `externalCode`(숫자 4..12자), `teamCode`(위 대문자 코드), `name`(1..80자) | 201 선수 entity |
| `PATCH /tving/entities/players/{code}/` | **teamCode와 name 모두 필수**. externalCode를 보내도 PK 변경에 사용하지 않음 | 200 선수 entity |
| `DELETE /tving/entities/players/{code}/` | 없음 | 204; 없어도 동일 |
| `POST /tving/snapshots/` | `resourceKind, resourceKey, payload, sourceFetchedAt` | 201 snapshot |
| `PATCH /tving/snapshots/{pk}/` | **payload와 sourceFetchedAt 모두 필수** | 200 snapshot |
| `DELETE /tving/snapshots/{pk}/` | 없음 | 204; 없어도 동일 |

- PATCH에도 serializer를 `partial=True`로 사용하지 않는다. 스냅샷 kind/key는 serializer에서 optional이지만 POST 서비스의 identity 검증을 통과하려면 필요하다. PATCH의 kind/key는 변경되지 않는다.
- `sourceFetchedAt`은 DRF DateTimeField로 파싱한 후 서비스에서 timezone-aware datetime인지 검사한다. 클라이언트는 명시적 timezone offset을 보내는 것이 안전하다.
- 키 규칙: daily `YYYY-MM-DD`, month `YYYY-MM`, team 허용 코드, athlete 숫자 4..12자. payload는 임의 원천 JSON이 아닌 해당 kind의 **정규화된 객체**다.
- payload 최대 4,000,000 bytes, NaN/Infinity 불허, 문자열 최대 4000자, 배열 최대 2000항목, 객체 최대 200키/키 최대 120자 등 안전 검증이 있다. daily는 날짜 일치·10개 순위·비어 있지 않은 투타 순위·최대 20경기, month는 월·일자 상태/경기, team은 코드·필수 문자열·4개 포지션/2개 투타 종류, athlete는 프로필 코드·팀·시즌/통산 필드를 검사한다. 정확한 중첩 계약은 [서비스 `_validate_normalized`](../../backend/tving/service.py)와 [serializer](../../backend/tving/serializers.py)를 따른다.
- 스냅샷 생성/수정은 `lastSyncedAt=null`로 둔다. **수정했다고 관계형 모델이나 공개 daily/team 응답이 즉시 바뀌지 않는다.** 별도 [backfill 명령](../../backend/tving/management/commands/backfill_tving_snapshots.py)은 스냅샷을 보존하면서 관계형 persister를 호출하고 `migrated/skipped/preserved`를 보고한다. 이 명령도 행별 갱신 정책을 따르므로 강제 덮어쓰기/HTTP 동기화 API가 아니다.
- TVING 관리자 API에는 baseball CRUD의 ETag/If-Match 조건부 수정이 없다. 행 잠금이 있어도 오래된 클라이언트의 덮어쓰기를 412로 막는 계약은 아니다. 선수 삭제는 관련 기록까지 연쇄 삭제한다.

## 오류 계약

| 상태 | 동작 |
| --- | --- |
| 400 `invalid_input` | 날짜/코드/페이지/payload/요청 필드 실패, 중복 선수 코드. 메시지는 `요청 값을 확인해 주세요.`로 축약 |
| 403 `forbidden` | 서비스의 관리자 검사 실패 |
| 503 `upstream_unavailable` 또는 `tving_error` | 서비스에서 분류한 원천 실패, 기존 완전한 데이터로 fallback할 수 없음 |
| 503 `service_unavailable` | 뷰가 잡은 기타 예외(일부 DB 오류 포함) |
| 404 | 선수/스냅샷 상세 GET 또는 PATCH 대상 없음. `{data:null,error:...}`이며 code는 없음 |
| 401 / 403 | DRF/JWT 인증·권한 단계 실패. 뷰 서비스 오류 envelope와 다른 기본 DRF 응답일 수 있음 |
| 405 | 미구현 메서드. 기본 DRF 오류 |

서비스 오류 envelope는 `{data:null,error:"...",code:"..."}`다. 필드별 serializer 오류를 그대로 반환하지 않는다. 스냅샷 중복 생성의 IntegrityError는 선수와 달리 400/409로 변환하지 않고 일반 503이 된다. 잘못된 JSON 등 뷰 진입 전 DRF 오류도 모든 경우 같은 envelope라고 보장하지 않는다.

## SQL 및 운영 범위

이 앱에는 SQL 실행 endpoint가 없다. [baseball SQL 서비스](../../backend/baseball/query_service.py)는 `baseball_readonly` 계정으로 공유 모델을 조회하는 내부 경로이며 모델 화이트리스트·단일 SELECT·함수 제한·시간/행/응답 크기 제한을 적용한다. 모델을 baseball로 옮긴 결과 TVING 관계형 데이터와 ProviderSnapshot도 현재 허용 대상에 포함된다. **SQL은 TVING을 갱신하지 않고**, SQL용 읽기 전용 계정이 있다고 이 앱의 공개 GET upsert나 관리자 ORM 쓰기까지 읽기 전용이 되는 것은 아니다. 상세 보안/설정 한계는 [baseball 문서](baseball.md)를 본다.

현재 앱/전역 설정에 TVING 전용 DRF throttle은 지정되어 있지 않다. 기본 동기화 간격이 곧 요청량 제한이나 원천 동시 호출 합치기를 의미하지 않는다. 원천 스키마 변경·차단·부분 누락, 기준 구단 미적재, 관계형 데이터 불완전성으로 503 또는 stale 응답이 날 수 있다.

[서비스/API 테스트](../../backend/tving/tests/test_tving.py), [모델 이전 테스트](../../backend/tving/tests/test_migrations.py)가 관련 검증 위치다. 이 README는 소스 및 로컬 링크만 확인했으며 실제 원천 연결·DB 쓰기·backfill·API 테스트를 실행하지 않았다.
