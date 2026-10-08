# 10월 16일 발표용 가상 경기

2026년 10월 16일, 한국 시간 기준이다. 공식 경기 일정이 아니라 사용자가 요청한 시연용 데이터다.

| 구장 | 시작 | 홈 | 원정 |
| --- | --- | --- | --- |
| 광주-KIA 챔피언스 필드 | 14:00 | KIA 타이거즈 | NC 다이노스 |
| 잠실야구장 | 17:00 | 두산 베어스 | 롯데 자이언츠 |

프로젝트 루트에서 실행한다. 실행 중인 로컬 백엔드의 DB에 바로 반영되어 재시작이 필요 없다.

```powershell
# 등록 (여러 번 실행해도 중복 생성되지 않음)
docker compose exec backend python manage.py demo_games apply

# 등록 상태 확인 (인자 생략 시에도 조회만 함)
docker compose exec backend python manage.py demo_games status

# 위 두 가상 경기만 제거
docker compose exec backend python manage.py demo_games remove
```

`GAME` 테이블에 `source=demo`, `game_type=DEMO`, `source_status_label=가상 경기`로 구분한다.
`game_code`와 `source_external_code`는 `demo:20261016:` 식별자를 사용하므로 TVING 동기화가 공식 경기로 가져가지 않는다.
공식 일정 수집 파일·월별 수집 완료 표시는 수정하지 않으며, 서버 시작이나 마이그레이션으로 자동 등록하지 않는다.
대상 구장 또는 팀의 기존 일정과 충돌하면 등록 전체를 취소한다. 삭제도 위 두 경기의 식별자와 가상 표식이 모두 일치하는 행만 대상으로 한다.

챗봇의 `get_games` 및 코스 생성·수정의 일정 조회는 같은 `GAME` 테이블을 읽는다.
예: `10월 16일 광주 KIA 구장 코스 짜줘`, `10월 16일 잠실 두산 경기 코스 짜줘`.
삭제하면 이후 일정 조회에서 바로 사라진다. 이미 작성된 대화나 저장한 코스는 별도 사용자 데이터라 유지된다.
