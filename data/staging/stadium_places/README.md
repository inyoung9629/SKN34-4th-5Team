# 구장 주변 장소 별도 적재본

서비스 테이블에 넣기 전 검토·복원하기 위한 수집 스냅샷입니다. 실제 데이터도 Git에 포함합니다.
현재 스냅샷: [`20260927T092810Z/manifest.json`](20260927T092810Z/manifest.json).

2026-09-29: 코스 작성 화면과 활성 챗봇 장소 검색은 이 수집본의 공공장소 JSONL을
읽기 전용 API로 조회하도록 연결했습니다. DB 스테이징 적재와는 독립적이며 원본 파일은 변경하지 않습니다.
숙박 상세는 미연결입니다. [서비스 연결 명세](../../../docs/api/collected-places.md)를 참고하세요.

## 저장 내용

등록된 9개 구장의 중심으로부터 직선 2,500m 이내 후보입니다.

| 파일 (각 구장 폴더) | 전체 건수 | 내용 |
|---|---:|---|
| `public_places.jsonl` | 37,027 | 소상공인 음식점·술집·카페·편의점·놀이시설, 공원·산책 후보 |
| `convenience_review.jsonl` | 387 | 미식별·과거 브랜드명 등으로 선정하지 않은 편의점 기록 |
| `google_lodging_ids.jsonl` | 3,209 | Google 숙박 후보 Place ID만 저장 |

- 27개 JSONL 파일 약 48 MiB. 한 줄이 한 기록이며 UTF-8입니다.
- 같은 구장의 선정된 편의점 1,726건은 `public_places.jsonl`에 한 번만 보관합니다.
- 공공데이터의 원본 필드는 `source_fields`에 보존합니다. 출처·상가업소번호·주소·좌표·원본 분류·선정 근거도 유지합니다.
- `manifest.json`에 구장 좌표, 출처별 수집 시각·기준월·검색 한계, 파일별 건수·SHA-256을 보관합니다.
- 소상공인 원천 기준월은 202606이며 조회일과 다릅니다. 기록 수는 현재 영업하는 실제 매장 수의 인증이 아닙니다.
- 공원과 Tour 산책 후보는 대표 지점이며 경로 선형·출입구·보행 접근성은 미확인입니다. 출처 간 중복은 미병합 상태입니다.
- Google 상세 상호·주소·좌표·유형은 저장하지 않습니다. ID로 요청 시 상세 API를 호출하는 구조입니다.
  잠실·사직 각각 검색 제한 잔여 영역 2개가 있어 전수 수집을 보장하지 않습니다.
- API 키, 이동용 `.env`, 전국 공원 원본, 중복 소스별 수집본, 과거 비교 실험 파일은 포함하지 않습니다.

분류·출처 제한은 [수집 안내](../../../backend/crawling/STADIUM_COLLECTION.md)를 참고하세요.

## 파일 검증과 새 수집본 만들기

저장소 루트에서 실행합니다. 파일 검증·변환은 Python 표준 라이브러리만 쓰며 API를 재호출하지 않습니다.

```powershell
python backend/crawling/stadium_snapshot.py data/staging/stadium_places/20260927T092810Z

# 새 수집이 완료된 뒤 새 스냅샷 디렉터리로 변환 (기존 경로 덮어쓰기 거부)
python backend/crawling/stadium_snapshot.py data/staging/stadium_places/<새시각> --from-collection backend/artifacts/stadium_collection/<수집폴더>
```

`.gitattributes`가 줄바꿈을 LF로 고정하므로 Windows/Linux에서 Clone 후에도 체크섬이 일치합니다.
변환은 검증이 끝난 뒤 디렉터리를 공개하며 실패한 일부 파일을 완성본으로 남기지 않습니다.

## 기존 PostgreSQL 안에 별도 적재

새 DB 서비스를 추가할 필요는 없습니다. 프로젝트 PostgreSQL 안에 `place_staging` 스키마를 만듭니다.
Django `Place`, 관광지·코스·RAG 테이블에는 쓰지 않습니다. 적재 스키마는 아래 SQL 파일로 관리하며
현재 Django ORM 모델이나 `manage.py migrate`에는 연결하지 않았습니다.

| 테이블 | 역할 |
|---|---|
| `place_staging.snapshots` | 수집본 ID, manifest, 체크섬, DB 적재 시각 |
| `place_staging.source_runs` | 구장별 출처·수집 시각·기준월·진단 메타데이터 (36건) |
| `place_staging.public_places` | 기본 필드와 원본 payload, `selected`/`needs_review`, 구장·종류 검색 인덱스 |
| `place_staging.google_lodging_ids` | 구장과 숙박 Place ID 연결; 상세정보 칼럼 없음 |

프로젝트 Python 환경의 `psycopg`와 `python-dotenv`가 필요합니다 (`backend/requirements.txt`에 포함).
접속값은 Django와 같이 프로세스 환경변수 → `backend/.env` → 루트 `.env` 순으로 읽습니다.
DB 계정에는 접속 대상 DB의 스키마 생성 권한이 필요합니다.

```powershell
# 파일만 검증. DB 접속·쓰기는 하지 않음
python backend/crawling/load_stadium_snapshot.py data/staging/stadium_places/20260927T092810Z

# 설정한 PostgreSQL에 실제 적재
python backend/crawling/load_stadium_snapshot.py data/staging/stadium_places/20260927T092810Z --apply

# DB_HOST=db인 Docker DB에 호스트 PC에서 접근할 때 (기존 env 값은 바꾸지 않음)
python backend/crawling/load_stadium_snapshot.py data/staging/stadium_places/20260927T092810Z --apply --host localhost

# 실행 중인 backend 컨테이너에서 실행할 경우
docker compose exec backend python crawling/load_stadium_snapshot.py /data/staging/stadium_places/20260927T092810Z --apply
```

입력 전체를 검증한 뒤 한 트랜잭션으로 적재합니다. 실패하면 스키마 생성·적재를 함께 롤백합니다.
같은 스냅샷과 체크섬으로 재실행하면 건수를 확인하고 `already_loaded`로 종료합니다.
같은 ID의 다른 파일이나 불완전한 기존 적재는 오류로 중단하며 자동 덮어쓰기·삭제하지 않습니다.
새 수집본은 새 ID로 적재해 과거 수집 이력을 유지합니다. 동시 적재는 트랜잭션 잠금으로 직렬화합니다.

```sql
-- 검토 대상을 빼고 잠실 카페 후보 검색
SELECT name, address, category_small, lat, lng, distance_m
FROM place_staging.public_places
WHERE snapshot_id = '20260927T092810Z'
  AND stadium_code = 'JAMSIL'
  AND selection_status = 'selected'
  AND kind = 'cafe'
ORDER BY distance_m;
```

후보의 `verification_status`는 모두 `unverified`입니다. 실제 영업 확인·출처 간 동일 장소 연결·
서비스 승인·RAG 임베딩·챗봇 연결·주기 갱신은 이후 작업입니다. JSONL을 Git에 올리는 것만으로
운영 DB나 챗봇에 자동 반영되지는 않습니다.

## 2026-09-27 검증 상태

- 수집·스냅샷·적재 관련 테스트 21개 통과.
- 임시 PGlite PostgreSQL 엔진에서 전체 37,414개 공공 기록 + 3,209개 ID + 36개 출처 기록 적재·재실행 검증.
- 동일 ID의 다른 내용 거부, Google 상세정보 유입 거부, 실패 롤백, 불완전 적재 감지 확인.
- 테스트 엔진의 확장 프로토콜 제약 때문에 통합 테스트는 `psycopg.ClientCursor`의 단순 프로토콜 사용.
  프로젝트 DB 대상 CLI는 일반 PostgreSQL 연결을 사용합니다.
- 이 PC에는 실행 중인 프로젝트 PostgreSQL/Docker가 없어 **프로젝트 DB의 실제 적재는 미실행**입니다.
  임시 테스트 엔진은 서비스 DB가 아니며 종료 시 테스트 데이터가 사라집니다. 위 파일이 복원 원본입니다.

일반 테스트는 DB 없이 실행되며 DB 테스트 1개는 건너뜁니다. 별도의 테스트용 PostgreSQL을 준비했다면
`STADIUM_STAGING_TEST_DSN` 환경변수를 설정하고 실행합니다. 테스트 데이터는 외부 트랜잭션으로 롤백합니다.

```powershell
python -m unittest discover -s backend/crawling -p 'test_stadium*.py'
```

스키마: [stadium_staging.sql](../../../backend/crawling/stadium_staging.sql)
 / 적재 명령: [load_stadium_snapshot.py](../../../backend/crawling/load_stadium_snapshot.py)
