# 구장별 장소 수집

2026-09-27 사용자 결정에 따라 **숙박 출처는 Google Places API (New)** 입니다.
행안부·관광공사·소상공인 숙박을 이번 결과에 포함하거나 Google 실패 시 대체하지 않습니다.
기존 잠실 파일럿은 과거 비교 자료로 보존합니다.

`collect_stadium_places.py`는 `data/preprocessed/stadium_coordinates.csv`에 등록된 모든 구장의
중심 좌표로부터 직선 2,500m 이내를 대상으로 합니다. Python 표준 라이브러리만 사용합니다.
현재 등록 구장은 9개입니다. 같은 구장을 공유하는 구단을 중복 수집하지 않습니다.

```powershell
python backend/crawling/collect_stadium_places.py
python -m unittest discover -s backend/crawling -p 'test_stadium*.py'
```

루트 `.env`에서 `SBIZ_API_KEY`, `PARK_API_KEY`, `TOUR_API_KEY`, `GOOGLE_PLACES_API_KEY`를 읽습니다.
키는 로그·결과 파일에 기록하지 않습니다. Google 키는 서버에서만 사용합니다.
결과는 Git에서 이미 제외된 `backend/artifacts/stadium_collection/<UTC>/`에 생성합니다.

2026-09-27 수집본은 검증 후 `data/staging/stadium_places/20260927T092810Z/`에
중복 없는 복원용 JSONL로 따로 보관합니다. **공공장소 실제 기록과 Google Place ID도 Git에 포함**합니다.
검증·PostgreSQL 별도 스키마 적재 방법은 [적재본 안내](../../data/staging/stadium_places/README.md)를 참고하세요.

## 출처와 분류

| 대상 | 출처 | 처리 |
|---|---|---|
| 음식점·술집 | 소상공인 상가정보 | 원본 대·중·소분류 및 상가업소번호 보존 |
| 카페·디저트 후보 | 소상공인 상가정보 | 카페, 빵/도넛, 아이스크림/빙수, 떡/한과. 메뉴·좌석은 추정하지 않음 |
| 편의점 | 소상공인 상가정보 | 명칭으로 확인되는 CU·GS25·세븐일레븐·이마트24. 다른 기록은 검토 파일에 보존 |
| 놀이시설 | 소상공인 상가정보 | 명시적 키즈카페·보드게임카페를 우선 분류 |
| 공원 | 전국도시공원 표준데이터 | 전국 페이지를 한 번 수집하고 각 구장 반경으로 필터 |
| 산책 후보 | 관광공사 TourAPI | 관광지(12)·레포츠(28) 중 명칭으로 추린 공원·산책·둘레길·숲길 등 |
| 숙박 | Google Places API (New) | 호텔·모텔·호스텔·게스트하우스 등 Lodging 유형 검색, ID 중복 제거 |

카페의 디저트 판매가 미확인이라면 `cafe_type=unverified`이며 음료 전용으로 간주하지 않습니다.
디저트 업종은 `dessert_candidate`로 표시합니다. 공공데이터에는 전체 메뉴나 실시간 영업 확인이 없습니다.

공원·산책 후보 좌표는 대표 지점입니다. 실제 산책로 선형, 입구, 접근 가능 여부, 보행 거리를 뜻하지 않습니다.
두 출처 간 동일 공원의 중복은 아직 합치지 않았습니다. 제목 기반 후보 추출로 모든 산책로를 보장하지 않습니다.

## 숙박 조회와 저장

- `google_lodging.py`가 Google만 조회합니다. 상호·주소·좌표·대표 유형·유형 목록·영업 상태와 출처 표기를 요청합니다.
- Nearby Search는 한 번에 최대 20건입니다. 20건을 채운 영역을 사각 격자로 나누고 각 격자를 덮는 원을 검색합니다.
  다시 원래 구장의 2.5km 반경으로 걸러냅니다. Place ID를 기준으로 중복을 제거합니다.
- 구장당 최대 512회 검색, 최대 깊이 6입니다. 한도에서 미처리 영역이 남으면 `coverage=partial_capped`와
  `unresolved_search_cells`로 표시합니다. 모든 영역이 한도 아래여도 Google 검색의 실제 전수성을 보증하지 않습니다.
  `unqueried_cells_at_request_limit`는 호출 한도에서 미조회인 영역이며, `capped_cells_at_depth_limit`는
  최소 크기로 나누어도 20건 제한을 채운 영역입니다. 실행 당시 한도는 요약에 보존됩니다.
- API 장애를 0건으로 취급하지 않습니다. 최대 3번 전송 재시도 후 실패를 기록하며, 호출 수 집계는 논리 검색 수입니다.
- Google `businessStatus`를 확인하되 조회되지 않은 상태는 `UNKNOWN`입니다. 반환 기록에 휴·폐업 상태가 있다면
  ID를 지우지 않고 집계에 구분합니다. `OPERATIONAL`도 당일 이용·예약 가능 여부의 보증은 아닙니다.
- `primaryType`이 없으면 미확정으로 남깁니다. `inn`을 한국 행정상 여인숙으로 단정하거나 유형에서 품질을 추론하지 않습니다.
- `lodging`만 있는 후보는 호텔·모텔로 세분류하지 않습니다. 다른 대표 유형에 숙박 태그가 함께 붙은 곳도
  후보에 남기며 `other_or_missing_primary_type`으로 집계합니다. 이 후보 수는 검증된 예약 가능 숙박업소 수가 아닙니다.
- **파일에는 `place_id`만 보존합니다.** 상호·주소·좌표·유형·상태의 개별 응답은 메모리에서 처리한 뒤 버립니다.
  진단 보고서에는 요청 수와 항목별 집계만 기록합니다. Google 원문이나 그 임베딩을 영구 RAG에 넣지 않습니다.
- 상세정보는 `google_lodging.fetch_detail(key, place_id)`로 요청 시 조회합니다. 반환값은 저장하지 말고 필요한 출처와 함께 표시합니다.
  현재 서비스 API/프론트엔드에는 아직 연결하지 않았습니다.
- 일반 Places 결과를 카카오 지도 마커로 결합하지 않습니다. Google 지도와 함께 표시하거나,
  지도 없는 상세 목록에 Google Maps 출처를 표시하는 방식으로 서비스 화면을 별도로 설계해야 합니다.

근거: [Places 저장·표시 정책](https://developers.google.com/maps/documentation/places/web-service/policies),
[Nearby Search](https://developers.google.com/maps/documentation/places/web-service/nearby-search),
[유형 목록](https://developers.google.com/maps/documentation/places/web-service/place-types),
[서비스별 약관의 Places API 조항](https://cloud.google.com/maps-platform/terms/maps-service-terms).

## 결과와 재실행

- `summary.json`, `REPORT.md`: 구장별 출처·기록 수·완료 시각·오류·검색 한도 상태.
- `<구장>/public_places.json`: 저장 가능한 공공데이터 통합 목록. Google 및 숙박은 포함하지 않습니다.
- `<구장>/sbiz_places.json`: 음식점·카페·술집·편의점·놀이시설과 원본 필드.
- `<구장>/convenience_review.json`: 선정 여부를 포함한 편의점 전체 기록. 영업 여부 확정 아님.
- `<구장>/park_candidates.json`, `tour_walk_candidates.json`: 공원·산책 후보와 원본 필드.
- `<구장>/google_lodging_ids.json`: 숙박 Place ID 목록. 구장 연결·수집 시각은 상위 경로와 요약에서 관리.
- `parks_national.json`: 이번 실행에서 공유하는 전국 공원 스냅샷.

```powershell
# 한 구장/출처 검증
python backend/crawling/collect_stadium_places.py --stadiums JAMSIL --sources GOOGLE

# 이전 실행 보완. 같은 구장 좌표에서 완료한 출처는 재호출하지 않습니다.
python backend/crawling/collect_stadium_places.py --output-dir backend/artifacts/stadium_collection/<기존시각>
```

새로 갱신할 때는 새 출력 디렉터리를 사용합니다. 같은 디렉터리에 동시에 실행하지 않습니다.
같은 실행을 검증하면서 일부 출처만 재조회하려면 `--sources GOOGLE --refresh`처럼 명시합니다.
서비스 DB 적재·임베딩·주기 작업은 이 수집기로 실행하지 않습니다.
