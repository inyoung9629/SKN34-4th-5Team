# 잠실 공공 장소 데이터 파일럿

> 이 문서는 과거 잠실 비교 파일럿입니다. 2026-09-27 이후 구장별 수집은
> [STADIUM_COLLECTION.md](STADIUM_COLLECTION.md)를 따릅니다. 새 수집의 숙박 출처는
> 사용자 결정에 따라 Google Places API이며, 아래 행안부·관광공사 숙박 자료는 합치지 않습니다.

Python 표준 라이브러리만 사용합니다. 루트 `.env`의 `SBIZ_API_KEY`, `PARK_API_KEY`, `TOUR_API_KEY`를 읽습니다. 비교에는 `KAKAO_REST_API_KEY`가 필요합니다. URL 인코딩된 공공데이터 키도 한 번 디코딩한 뒤 요청 파라미터로 인코딩합니다.

```powershell
python backend/crawling/collect_stadium_pilot.py --compare-kakao
python -m unittest discover -s backend/crawling -p test_stadium_pilot.py
```

현재 대상은 프로젝트에 등록된 잠실 중심 좌표와 직선 반경 2,500m입니다. 다른 구장을 자동 실행하지 않습니다. 결과는 `data/preprocessed/stadium_pilot/JAMSIL_<UTC시각>/`에 생성합니다.

- 소상공인: 전체 업종을 끝 페이지까지 수집한 뒤 좌표로 반경을 재검증합니다. 전체 업종은 이름·분류가 다른 장소를 비교하기 위한 자료입니다.
- 공원: 전국 목록을 끝 페이지까지 읽고 반경으로 추립니다. 실제 API에서는 페이지 0과 1이 같은 첫 페이지를 반환하므로 1부터 시작합니다.
- TourAPI: 위치 기반 목록에서 식당·카페·숙박·관광·산책 후보를 추리고 공통정보와 소개정보를 보완합니다. 상세정보 실패는 각 장소의 `detail_errors`에 기록합니다.
- 비교: 카카오 식당·카페를 거리순/정확도순 각 최대 45건씩 조회합니다. 검색 표본일 뿐 전수조사가 아닙니다. 응답은 메모리에서만 비교하고 ID·URL·매칭 결과만 저장합니다.
- 동일 주소는 수동 검토 후보입니다. 대응 후보 없음도 전국 DB의 부재를 확정하지 않습니다. 표본은 구장 내부 매점에 치우칠 수 있습니다.
- `places.json`에는 식당, 카페, 산책·공원, 볼거리, 숙박, 놀이시설, 편의점의 일곱 종류를 담고, 소상공인 기숙사/고시원을 제외합니다. 출처 간 중복은 아직 병합하지 않습니다. 음식 업종에는 빵집·떡집·구내식당이 포함되며 숙박 업종은 실제 여행자 예약 가능 여부 확인이 필요합니다.
- 보드게임 카페와 키즈카페·실내놀이터는 `kind=play_facility`(놀이시설)로 분류하고 `play_type=board_game_cafe` 또는 `kids_cafe`로 구분합니다. 명칭·업종이 명시적인 경우에만 적용하며, 키즈 의류·학원이나 보드람치킨 등은 포함하지 않습니다. 원본 업종은 보존합니다.
- 웹 검증 후보의 놀이시설 분류는 점포 연결 상태와 독립적으로 기록합니다. 분류만 바뀌었다고 미확정 점포가 확인된 것으로 처리하거나 다른 소상공인 점포에 연결하지 않습니다. 룸카페·만화카페는 보드게임/키즈 시설 근거가 있는 곳에만 적용합니다.
- 원본·결과 파일만 생성하며 서비스 DB, 임베딩, 주기 작업은 실행하지 않습니다.

실패한 출처만 보완하거나 비교만 다시 실행할 수 있습니다. 동시에 같은 디렉터리에 실행하지 마세요.

```powershell
python backend/crawling/collect_stadium_pilot.py --sources PARK --output-dir data/preprocessed/stadium_pilot/JAMSIL_<시각>
python backend/crawling/collect_stadium_pilot.py --comparison-only --compare-kakao --output-dir data/preprocessed/stadium_pilot/JAMSIL_<시각>
python backend/crawling/collect_stadium_pilot.py --rebuild-report --output-dir data/preprocessed/stadium_pilot/JAMSIL_<시각>
```

실패는 `summary.json`에서 출처별로 확인합니다. 빈 결과와 인증·네트워크 실패를 구분합니다. 키는 소스·로그·보고서에 남기지 않습니다. `--probe`는 API 응답 형식을 점검하는 소량 조회입니다.

`--rebuild-report`는 API 호출 없이 저장된 원본으로 `places.json`, `convenience_stores.json`, `REPORT.md`를 다시 만듭니다. `summary.json`의 `sources`는 최초 수집 당시 분류이며, `places`는 현재 프로젝트 분류입니다.

## 편의점 분류 기준 (2026-09-27 사용자 결정, 파일럿 적용)

- 소상공인 `소매 > 종합 소매 > 편의점`을 `kind=convenience_store`로 독립 분류합니다. 다른 업종에 편의점 브랜드명이 들어 있다는 이유만으로 편의점으로 바꾸지 않습니다.
- 1차 서비스 범위는 CU·GS25·세븐일레븐·이마트24입니다. `places.json`에는 브랜드명이 명시된 기록(`brand_status=name_identified`)만 포함합니다. 이는 브랜드 표기 식별이며 영업 여부 검증이 아닙니다.
- 공백·전각 문자를 정규화하고 명확한 한글·영문 브랜드 표기를 통일합니다. 법인명·축약명·오타는 `brand_status=needs_review`, `brand_candidates`로 기록하고 브랜드 확정을 보류합니다. 서로 다른 브랜드명이 섞여 있어도 검토 대상으로 둡니다.
- 미니스톱·위드미 등 과거 브랜드 표기는 `legacy_name`, 브랜드를 식별하지 못한 경우는 `unidentified`로 보존합니다. 현재 브랜드로 자동 전환하거나 소규모 브랜드라고 단정하지 않습니다.
- `convenience_stores.json`에는 편의점 전체 분류 결과와 검토 후보를 함께 저장합니다. 주요 4개 외 브랜드는 1차 서비스 대상에서 제외하되 원본을 삭제하지 않습니다.
- 원본 상호·상가업소번호·업종은 보존합니다. 같은 브랜드·같은 건물이라는 이유로 기록을 합치지 않으며, 기록 수를 실제 영업 점포 수로 해석하지 않습니다.

## 카페·디저트 분류 기준 (2026-09-27 사용자 결정)

서비스 분류를 위한 기준입니다. 현재 수집기의 `kind`와 기존 결과 파일에는 아직 적용하지 않았습니다.

- 커피·음료와 디저트를 함께 취급하는 매장, 디저트 전문점은 `카페·디저트`로 묶습니다.
- 디저트 없이 커피·음료만 판매하는 것으로 확인된 매장은 위 묶음에서 분리해 `커피·음료`로 관리합니다. 전체 장소 목록에서 삭제한다는 의미는 아닙니다.
- 소상공인 소분류 `카페`만으로 디저트 판매 여부를 판단하지 않습니다. 확인되지 않은 매장은 `세부 유형 미확인`으로 보존하며, 음료 전용점으로 단정하거나 임의로 제외하지 않습니다.
- `빵/도넛`, `아이스크림/빙수`, `떡/한과` 등의 업종은 디저트 후보로 사용할 수 있지만, 해당 업종만으로 커피 판매나 취식 공간이 있다고 판단하지 않습니다.
- 메뉴 자료가 없다는 사실은 디저트를 판매하지 않는다는 근거가 아닙니다. 판매 여부를 보완할 때는 이용 가능한 근거와 확인일을 함께 관리합니다.
- 보드게임 카페·키즈카페는 기존 놀이시설 분류를 우선 적용합니다. 소상공인 원본 대·중·소분류는 별도로 보존합니다.

## 행정안전부 숙박업 별도 파일럿 (2026-09-27)

`collect_mois_lodging_pilot.py`는 기존 결과와 별도 디렉터리에 수집합니다. 기존 식당·편의점 등의 집계에는 병합하지 않습니다.
이 수집기에만 좌표 변환용 `pyproj`가 필요합니다. 설치된 Python을 사용하거나, 설치 디렉터리를 `--dependency-dir`로 지정할 수 있습니다.

```powershell
python backend/crawling/collect_mois_lodging_pilot.py
python backend/crawling/collect_mois_lodging_pilot.py --rebuild --output-dir data/preprocessed/stadium_pilot/JAMSIL_MOIS_<UTC시각>
```

- 루트 `.env`의 `MOIS_LODGING_API_KEY`를 우선 사용하고, 없으면 사용자가 같은 포털 키임을 확인한 `SBIZ_API_KEY`를 사용합니다. 숙박업 서비스 활용 승인은 별도로 필요합니다. 키를 출력·저장하지 않습니다.
- `https://apis.data.go.kr/1741000/lodgings/info`에서 성동·광진·강남·송파구를 모든 영업 상태로 수집합니다. 페이지당 최대 100건, 총건수·관리번호 중복·자치단체 필터 적용 여부를 검증합니다.
- 프로젝트 잠실 좌표에서 직선 2,500m 이내를 추립니다. 행안부 EPSG:5174 좌표를 EPSG:4326으로 변환하고 좌표 누락·이상은 `unlocated.json`으로 보존합니다.
- `raw_<자치단체코드>.json`은 조회 지역 원본, `lodgings_all_statuses.json`은 반경 내 전체 상태, `lodgings_active.json`은 그중 행정상 영업/정상(`01`)인 기록입니다.
- `summary.json`과 `REPORT.md`에 상태·업태 분포와 필드별 값 유무를 기록합니다. 객실 수 0을 실제 객실 없음으로 해석하지 않습니다.
- 원본 업태를 그대로 유지하며 관광호텔·일반호텔·여관업 등을 상호만 보고 변경하지 않습니다. 별도 관광숙박업·도시민박·농어촌민박 자료는 이번 범위에 포함하지 않습니다.
- 2026-09-27 수집: 4개 구 1,085건을 13페이지로 수신, 원본 좌표 기준 반경 내 166건(영업/정상 69, 폐업 97). 조회 지역 전체의 좌표 누락 124건 중 영업/정상 3건은 별도 확인 대상입니다.
- 이번 `location_followup.json`은 추가 소상공인 숙박업 반경 5km 조회 및 기존 파일럿으로 3건의 동일 건물 주소 좌표를 확인한 보완 기록입니다. 행안부 원본 좌표와 구별하며 69건 집계에는 더하지 않습니다. 이 추가 주소 조사는 수집기를 실행한다고 자동 재실행되지는 않습니다.
- 서비스 DB, RAG 임베딩, 주기 작업은 실행하지 않습니다.
