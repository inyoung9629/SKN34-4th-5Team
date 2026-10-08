# TVING KBO 데이터

브라우저는 Nginx의 `/api/v1/` 프록시를 통해 Django만 호출합니다. Nginx가 `/api/v1/` 접두어를 제거하므로 실제 경로는 다음과 같습니다.

| 화면 요청 | Django URL | 내용 |
| --- | --- | --- |
| `GET /api/v1/tving/daily/` | `/tving/daily/` | 한국시간 당일 일정, 10팀 순위, 투수·타자 개인 순위 |
| `GET /api/v1/tving/daily/?date=YYYY-MM-DD` | `/tving/daily/` | 지정일 일정과 해당 시즌 순위 |
| `GET /api/v1/tving/schedule/?month=YYYY-MM` | `/tving/schedule/` | 월 달력과 날짜별 일정 |
| `GET /api/v1/tving/details/teams/{code}/` | `/tving/details/teams/{code}/` | 팀 기록, 일정, TOP5, 포지션별 선수단 |
| `GET /api/v1/tving/details/athletes/{code}/` | `/tving/details/athletes/{code}/` | 선수 프로필, 시즌·통산 기록 |
| `GET /api/v1/tving/details/status/` | `/tving/details/status/` | 실제 DB 보유 건수와 `on-demand` 전략 |

각 명시적 조회는 TVING 공개 응답을 새로 가져와 완전히 검증한 뒤 그 응답을 반환합니다. `EXTERNAL_DATA_SYNC_INTERVAL_SECONDS`(기본 600초)는 외부 호출 간격이 아니라 DB 쓰기 억제 간격입니다. 같은 리소스의 `lastSyncedAt`이 600초 이내이거나 정확히 경계이거나 미래이면 UPDATE하지 않습니다. 따라서 `providerFetchedAt`/`fetchedAt`은 이번 원천 조회 시각이고 `lastSyncedAt`/`updatedAt`은 마지막 DB 반영 시각으로 서로 다를 수 있습니다.

원천 조회나 검증이 실패하면 완전한 관계형 ORM projection이 있을 때 `stale: true`, `warning`과 함께 반환합니다. 저장본이 없으면 고정된 오류 코드의 503 JSON을 반환하고 원문 응답·URL 쿼리·DB 오류는 노출하지 않습니다. 외부 목록에 없다는 이유로 canonical 행을 삭제하지 않으며 날짜별 `game_codes`만 현재 화면 projection을 구분합니다.

## 데이터 안전 조건

- URL은 서버 코드의 TVING HTTPS 호스트와 경로 allowlist로만 조합합니다. 사용자 제공 URL은 받지 않습니다.
- 요청 제한 15초, JSON Content-Type, redirect 불허, 최대 4MB를 검사합니다.
- 일정은 날짜와 `focusDate`, 월 달력, 상태, 팀, 경기 ID를 확인합니다. 무경기일은 달력 근거가 있어야 하며 더블헤더 ID와 `SUSPENDED`를 보존합니다.
- 순위는 정규리그 10팀과 승·무·패 합계를 검증합니다. 개인 순위의 팀·선수·모든 통계 필드도 검증합니다.
- 팀 상세는 투수·타자 TOP5와 투수·내야수·외야수·포수 선수단을 모두 검증한 뒤 한 트랜잭션으로 저장합니다.
- 이미지 URL은 `https://image.tving.com`만 허용합니다. 선택적인 선발 투수 이름은 잘못되면 `null`이며 값을 만들지 않습니다.
- Next `instrumentation.ts`는 더 이상 수집 타이머를 시작하지 않고 `.cache/kbo` 파일을 쓰지 않습니다. 기존 TypeScript parser만 회귀 테스트 참고용으로 남고 사용자 화면의 네트워크 소유자가 아닙니다.

## TVING 공개 원천

다음은 TVING 웹 화면 내부 응답이며 안정적인 공개 개발자 API가 아닙니다. 회원 쿠키, 재생 API, API 키는 사용하지 않습니다.

- `/bff/sports/v2/kbo/schedule?date=YYYYMMDD`
- `/bff/sports/v2/kbo/schedule/day?date=YYYYMM`
- `/bff/sports/v2/kbo/history/team?yearSeason=YYYY&gameSeason=0`
- `/bff/sports/v2/kbo/history/athlete/ranking?...`
- `/bff/sports/v2/team?code=TEAM&sportsType=kbo`
- `/bff/sports/v2/kbo/history/athlete/top5?...`
- `/bff/sports/v2/roaster/item?...`
- `/bff/sports/v2/athlete?code=PLAYER&sportsType=kbo`

## 확인

```bash
docker compose -p tving-backend-test -f docker/tving-backend-test.compose.yml exec -T backend \
  python manage.py test tving.tests community.test_predictions
cd frontend && npm test && npm run build
```

실제 원천 smoke는 테스트 fixture와 구분해 `/tving/daily/`, `/tving/schedule/`, 팀, 선수 HTTP 경로를 호출하고 응답의 `stale`, 건수와 실제 DB 행을 함께 확인합니다.
