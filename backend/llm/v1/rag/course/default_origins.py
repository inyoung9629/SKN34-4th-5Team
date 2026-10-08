"""출발지가 없는 새 코스의 구장별 대표 역. 2026-10-07 지도 좌표 대조.

사용자와 합의한 고정 후보이며 요청마다 역을 검색하거나 모델이 좌표를 만들지 않는다.
화서역은 반경 2.5km 안의 6번 출구를 사용한다. 광주역·마산역은 일반 철도역이다.
"""

STATIONS = {
    "JAMSIL": {"name": "종합운동장역", "lat": 37.5111446632705, "lng": 127.073849447402,
               "sourceUrl": "https://place.map.kakao.com/21160812"},
    "GOCHEOK": {"name": "구로역", "lat": 37.50334155631282, "lng": 126.88230806229326,
                "sourceUrl": "https://place.map.kakao.com/21160519"},
    "MUNHAK": {"name": "인천터미널역", "lat": 37.4419166733897, "lng": 126.699694889876,
               "sourceUrl": "https://place.map.kakao.com/21161098"},
    "SUWON": {"name": "화서역 6번 출구", "lat": 37.28507744546083, "lng": 126.98875710934392,
              "sourceUrl": "https://place.map.kakao.com/27525277"},
    "DAEJEON": {"name": "대전역", "lat": 36.33229415139486, "lng": 127.4346445159045,
                "sourceUrl": "https://place.map.kakao.com/7830504"},
    "DAEGU": {"name": "수성알파시티역", "lat": 35.84269870525034, "lng": 128.67998634310348,
              "sourceUrl": "https://place.map.kakao.com/21161015"},
    "GWANGJU": {"name": "광주역", "lat": 35.16530023524146, "lng": 126.90933350743458,
                "sourceUrl": "https://place.map.kakao.com/8235448"},
    "SAJIK": {"name": "사직역", "lat": 35.1986215514386, "lng": 129.065193277832,
              "sourceUrl": "https://place.map.kakao.com/21160507"},
    "CHANGWON": {"name": "마산역", "lat": 35.23614082851537, "lng": 128.57702912364888,
                 "sourceUrl": "https://place.map.kakao.com/7871141"},
}


def station_origin(code):
    station = STATIONS.get(code)
    return {key: station[key] for key in ("name", "lat", "lng")} if station else None
