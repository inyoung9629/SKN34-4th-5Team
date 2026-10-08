# 구장 주변 장소 수집본

현재 스냅샷: [`20260927T092810Z-no-sbiz/manifest.json`](20260927T092810Z-no-sbiz/manifest.json).

2026-10-04 소상공인(SBIZ) 장소 36,500건과 편의점 검토 자료 387건을 제거했습니다.
공원·관광공사 기록과 Google 숙박 ID는 원래 내용 그대로 유지합니다. 수집 시각은
2026-09-27이며, 이번 정리는 재수집이나 영업 상태 갱신이 아닙니다.
기존 스냅샷과 혼동하지 않도록 ID를 변경하고 스키마 버전을 2로 올렸습니다.

## 남은 데이터

등록된 9개 구장의 원래 중심 좌표에서 직선 2,500m 이내 후보입니다.

| 파일 (각 구장 폴더) | 전체 건수 | 내용 |
|---|---:|---|
| `public_places.jsonl` | 527 | 전국도시공원 493건, 관광공사 산책 후보 34건 |
| `google_lodging_ids.jsonl` | 3,209 | Google 숙박 후보 Place ID |

- 18개 JSONL 파일, UTF-8/LF. `convenience_review.jsonl`은 제거했습니다.
- 공공데이터 원본 필드와 출처·주소·좌표는 `source_fields`와 각 기록에 남아 있습니다.
- `manifest.json`에 수집 시각, 원래 구장 좌표, 출처별 건수, 파일별 SHA-256을 저장합니다.
- 공원·산책 좌표는 대표점입니다. 입구·보행 경로·접근성이나 현재 영업을 확정하지 않습니다.
- Google 상호·주소·좌표 등 상세정보는 저장하지 않습니다. 숙박 ID는 실시간 상세 조회용입니다.
- 지도 목록의 카카오 실시간 조회 및 별도 구장 내부 시설 데이터는 이 수집본과 독립적입니다.

## 검증과 생성

저장소 루트에서 실행합니다. 파일 검증과 변환은 API를 호출하지 않습니다.

```powershell
python backend/crawling/stadium_snapshot.py data/staging/stadium_places/20260927T092810Z-no-sbiz
python -m unittest discover -s backend/crawling -p 'test_stadium*.py'

# 완료된 스키마 2 수집 결과를 새 디렉터리에 내보내기
python backend/crawling/stadium_snapshot.py data/staging/stadium_places/<새시각> --from-collection backend/artifacts/stadium_collection/<수집폴더>
```

현재 수집기는 PARK, TOUR_WALK, GOOGLE만 지원합니다. 소상공인 API 수집은 중단했습니다.
검증기는 이전 스키마 1과 SBIZ 레코드를 거부하므로 과거 파일을 다시 적재할 수 없습니다.
기존 경로는 덮어쓰지 않으며 검증을 통과한 완성본만 공개합니다.

## PostgreSQL 별도 적재

`place_staging` 스키마만 사용하며 Django 서비스 테이블에는 쓰지 않습니다.

| 테이블 | 내용 |
|---|---|
| `snapshots` | 스냅샷 ID, manifest, SHA-256 |
| `source_runs` | 9개 구장 × 3개 출처 = 27건 |
| `public_places` | 선정된 공원·관광 후보 527건 |
| `google_lodging_ids` | 숙박 ID 3,209건 |

```powershell
# 파일 검증만 수행
python backend/crawling/load_stadium_snapshot.py data/staging/stadium_places/20260927T092810Z-no-sbiz

# 명시적으로 요청한 DB에 적재
python backend/crawling/load_stadium_snapshot.py data/staging/stadium_places/20260927T092810Z-no-sbiz --apply --host localhost
```

같은 ID의 다른 내용은 거부하며 전체 적재를 하나의 트랜잭션으로 처리합니다.
실제 PostgreSQL 회귀 검증은 별도의 테스트 DB에 `STADIUM_STAGING_TEST_DSN`을 설정해 실행합니다.
DB가 없으면 해당 테스트 한 개는 건너뜁니다.

파일 정리는 기존 외부 DB 적재본이나 별도로 만든 RAG 인덱스를 자동 삭제하지 않습니다.
이 PC에서 확인된 로컬 수집 원본·파일럿 SBIZ 자료·복원 테스트 사본은 함께 정리했으며,
실행 중인 프로젝트 DB와 장소 RAG 인덱스는 없었습니다. 운영 DB에는 접속하지 않았습니다.
외부 환경에 적용할 때는 이전 스냅샷 적재본을 확인하고 장소 RAG를 남은 자료로 다시 빌드해야 합니다.

스키마: [stadium_staging.sql](../../../backend/crawling/stadium_staging.sql)
 / 수집 안내: [STADIUM_COLLECTION.md](../../../backend/crawling/STADIUM_COLLECTION.md)
 / 조회 API: [collected-places.md](../../../docs/api/collected-places.md)
