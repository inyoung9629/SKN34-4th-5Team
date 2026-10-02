# data/raw

팀이 준 원본 파일들. 어떤 스크립트도 자동으로 덮어쓰지 않음.

- 구장먹거리,컨텐츠.xlsx
- 구장정보.xlsx
- 시트가 뭘 의미하는지.xlsx
- 재입장 규정.비공식.txt
- backlog 보충.xlsx
- kbo_schedule.csv (팀원 원본 크롤링, 9월 한 달치 — 확장판은 data/preprocessed/kbo_schedule_full.csv)
- kbo_ticket_policy.csv (yagu.today 크롤링 원본, 자연어 문장 형태 — 구조화본은 data/preprocessed/kbo_ticket_policy_structured.csv)
- team_stadium_code_map.csv (팀·구장 표준 코드 매핑표. 구장정보.xlsx 기준으로 만든 기준표라 raw로 분류 — 스크립트가 자동 생성하지 않음)
- KBO_잔여정보_보완자료_좌석도_주차_버스_20260907.xlsx (팀원 보완 조사, 2026-09-08 추가. 광주 KIA 좌석도/좌석수, 대전·대구 주차 수용면·요금, 대전 버스정류장명. 시트 3개: 최종_보완데이터/입력용_요약/검증_메모. status·evidence_type·source_url까지 컬럼으로 갖춰져 있어 별도 구조화 없이 바로 참고 가능)
- KBO_9개구장_편의시설_통합_20260907.xlsx (팀원 보완 조사, 2026-09-08 추가. 9개 구장 화장실·수유실·흡연구역·장애인화장실·임산부휴게실 205건 통합. 시트 4개: 현행_시설/보류_이력/구장별_요약/기준_설명. 기존 구장정보.xlsx의 Facilities(49건)보다 훨씬 촘촘함 — 편의시설 정보 부족 문제가 사실상 해결됨)

※ kbo_standing.csv는 여기 없음 — 크롤러가 실행할 때마다 덮어쓰는 생성물이라 data/preprocessed/에 있음.

## rules/ — KBO 규정집 원본 (4차 #14, 2026-09-23 추가)

KBO 공식 홈페이지에서 수동 다운로드한 2026년판 PDF 2종 (**git 미포함** — `.gitignore`. 청킹을 재실행하려면 아래 파일명 그대로 이 폴더에 넣을 것). koreabaseball.com은 robots.txt로 봇 수집을 막고 있어 크롤링하지 않고 원본을 그대로 보존한다. 판권은 KBO·KBSA에 있으므로 서비스에서는 조항 인용(근거 표시)만 하고 전문 재배포는 하지 않는다.

| 파일 | 쪽 | 비고 |
|---|---|---|
| `2026_KBO야구규칙.pdf` | 220 | 공식야구규칙 1.00~9.23 + 용어의 정의 82항목 + 야구 도량형 + 2026 변경 요약 |
| `2026_KBO리그규정.pdf` | 106 | 리그규정 제1~78조 + 부록 9종(ABS·스피드업·피치클락·벌칙내규·표창규정 등). |

야구규약(268p)은 정관·구단 가입·선수계약·연봉중재·FA 등 구단/선수 대상 내용이라 수집 대상에서 제외(2026-09-23 결정). 필요해지면 KBO 홈페이지에서 받아 `chunk_rulebooks.py`의 `SOURCES`에 추가.

청킹: `backend/preprocessing/chunk_rulebooks.py` → `data/preprocessed/kbo_rulebook_chunks.csv` (387청크, 조 단위 + 1,400자 초과 조만 항 경계 분할. 두 문서 공통 설정이며 근거는 `data/preprocessed/README.md` 참고)

