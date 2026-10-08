# Google 숙박 코스 통합 (2026-09-29)

2026-09-30: 주 코스 작성 화면의 신규 숙박 검색은 사용자 요청에 따라 카카오 AD5로 전환했다. 아래 내용은 Google 파일럿과 기존 Google 참조 코스의 동작 기록이다. 기존 `google-ui-kit:` 코스의 재조회는 유지하지만 신규 주 화면 숙박 검색에는 사용하지 않는다. 현재 카카오 조회·ID 전용 저장·분류 및 직선 경로 제한은 [수집 장소 조회 문서](collected-places.md#2026-09-30-로컬-변경)를 참고한다.

## 사용 흐름

`/routes/new`의 숙박 버튼 또는 소분류 메뉴 → 호텔/모텔/여관 선택 → 검색 결과 카드 선택 → 지도 점 확인 → 코스에 담기.
숙박은 선택한 구장 반경 2.5km에서 분류별 가까운 최대 20곳을 수동 조회한다. 반환 결과는 전부 표시하되, 반경 내 모든 숙소를 보장하지 않는다. 주변 검색에는 다음 페이지가 없으며, 20개 도달 시 한도 안내를 표시한다. 0개도 실제 숙소 부재로 단정하지 않는다.
최초 진입·구장 전환·코스 열기·숙박 패널 재개방에는 Google 조회하지 않는다. 패널을 닫았다 다시 열면 기존 조회 결과를 유지한다.
공공데이터 식당/카페/산책 후보 및 기존 카카오 점 모양 핀은 유지한다.

Google 컴포넌트의 공식 CSS 설정으로 스타일을 맞추며 Google 출처, 링크, 안내를 숨기지 않는다. 검색 목록의 공식 `attribution-position="bottom"` 옵션과 출처의 회색 테마를 사용한다. 임의의 축소/덮어쓰기/Shadow DOM 조작은 하지 않는다. 자체 제목·카테고리 버튼의 중복 Google 배지는 제거하고, 지도 선택 카드 하단에는 12px 회색 `Google Maps` 출처를 표시한다.
주소·세부 업종은 위젯에서만 표시한다. 호텔(`hotel`)/모텔(`motel`)/여관(`inn`) 기본 업종으로 검색하며, 검색 필터를 개별 숙소의 확정 업종으로 저장하지 않는다.
일반 `숙박 업소` 표시는 세부 분류 미확인이며 `inn`은 국내 신고 업종인 여관·여인숙과의 정확한 대응을 보장하지 않는다. 게스트하우스 등 다른 숙박 유형은 이 세 필터에 포함하지 않는다.

## 저장 계약

- `placeId`: `google-ui-kit:<Google place ID>` (ID 부분 영숫자/밑줄/하이픈 1~220자).
- 자체 표시명 `선택한 숙소`, 자체 대분류 `숙박`, 방문 순서/visitId만 영구 저장한다.
- API `lat` / `lng`: Google 참조는 둘 다 `null`; 일반 장소는 유한 좌표 필수.
- Google 이름/주소/세부 분류/좌표를 코스 DB와 임시저장에 복제하지 않는다.
- 런타임의 미조회 좌표는 `NaN`으로 표현한다. JSON 경계에서는 `null`이며 지도에는 표시하지 않는다.
- 편집/상세 화면의 `코스 N번 숙소 확인`을 누르면 UI Kit로 재조회한다. 공개 ID/좌표만 메모리에 반영한다.
- 백엔드 serializer와 DB 제약으로 Google 참조에 좌표를 저장하는 요청을 차단한다.
- 마이그레이션: `travel.0011_google_lodging_references` 및 `0012_reference_coordinate_null_guard`. 기존 일반 장소 데이터는 변경하지 않는다.

## 경로 제한

기존 길찾기 서버는 요청 좌표를 지속 캐시한다. 따라서 Google 숙소가 포함된 코스는 이 API를 호출하지 않는다.
모든 지점이 조회된 경우 방문 순서의 직선만 연결하고 실제 이동 경로/시간이 아님을 명시한다.
미조회 지점을 건너뛰어 앞뒤 장소를 연결하지 않는다. Google 없는 코스는 기존 길찾기를 유지한다.
LLM 자동 숙박 분류/추천, Google 필드 추출, 웹검색 상세정보 기능은 이번 변경에 포함하지 않는다.

## 설정과 운영

브라우저 전용 `NEXT_PUBLIC_GOOGLE_MAPS_API_KEY`만 사용한다. 서버 키 fallback 없음.
Maps JavaScript API + Places UI Kit, 웹사이트 제한이 필요하다. 현재 발급 키는 로컬 전용이며 운영/개발서버 배포 전 별도 설정이 필요하다.
`/routes` 하위는 Google referrer 제한을 위해 origin 전달을 허용하고 Kakao SDK는 기존 no-referrer 동작을 유지한다.
수동 조회는 페이지 JS 수명당 최대 12회이며 새로고침하면 초기화된다. 결제계정 비용 상한이 아니다. 실패 자동 재시도 없음.

## 근거

- [서비스별 약관: Places UI Kit](https://cloud.google.com/maps-platform/terms/maps-service-terms)
- [공식 스타일 설정](https://developers.google.com/maps/documentation/javascript/places-ui-kit/custom-styling)
- [위젯 공개 필드](https://developers.google.com/maps/documentation/javascript/reference/places-widget)
- [주변 검색 결과 수 제한](https://developers.google.com/maps/documentation/places/web-service/nearby-search#maxresultcount)
- [숙박 분류표](https://developers.google.com/maps/documentation/places/web-service/place-types#lodging)
