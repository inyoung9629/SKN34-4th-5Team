"""2026-10-07 사용자가 지정한 구장별 기본 메뉴. 판매 근거는 매장별 수집 메뉴뿐이다.

브랜드/대표성의 편집 기준과 실제 매장 정보를 분리한다. 특히 대구의 한만두와
북촌손만두는 동일 상호로 취급하지 않는다. 같은 시그니처 메뉴를 판독한 매장만 연결한다.
"""
import re

from .stadium_food import matching_menu_items, menu_text


SIGNATURES = {
    "JAMSIL": {"store": "통빱", "stores": ("통빱", "통밥"),
               "menus": (("삼겹살정식",), ("김치말이국수",))},
    "GOCHEOK": {"store": "쉬림프셰프", "stores": ("쉬림프셰프", "쉬림프쉐프"),
                "menus": (("크림새우",),)},
    "MUNHAK": {"store": "스테이션", "stores": ("스테이션",), "menus": (("크림새우",),)},
    "SUWON": {"store": "보영만두", "stores": ("보영만두",), "menus": (("군만두",), ("쫄면",))},
    "DAEJEON": {"store": "농심가락", "stores": ("농심가락",), "menus": (("떡볶이",),)},
    "GWANGJU": {"store": "STATION", "stores": ("STATION", "스테이션"), "menus": (("크림새우",),),
                "research": {"checkedAt": "2026-10-07", "referenceUrl": "https://notes0236.tistory.com/154",
                             "sourceUrl": "https://myseatcheck.com/광주-챔피언스필드-먹거리-1루-3층-station/",
                             "note": "사용자 요청으로 야구공빵을 제외하고 요아정은 후순위. 두 출처에서 확인한 크림새우를 기본 추천하며 위치는 자리어때의 1루 3층 106블럭을 사용"}},
    "DAEGU": {"store": "한만두", "stores": ("한만두",), "menu_stores": ("북촌손만두",),
              "menus": (("짬뽕만두",),),
              "research": {
                  "checkedAt": "2026-10-07",
                  "referenceUrl": "https://blog.nasmedia.co.kr/entry/2603-lifestyletrend-eat",
                  "sourceUrl": "https://myseatcheck.com/대구-라이온즈파크-먹거리-3층-3루-북촌손만두/",
                  "note": "한만두 소개와 별개로, 자리어때 북촌손만두의 매장별 메뉴판에서 짬뽕만두 확인",
              }},
    "CHANGWON": {"store": "코아양과", "stores": ("코아양과",),
                 "menus": (("밀크셰이크", "밀크쉐이크"),)},
    "SAJIK": {"store": "다리집", "stores": ("다리집",), "menus": (("떡볶이",), ("오징어튀김",))},
}


# Reference articles nominate dishes; only the exact branch's collected menu
# can make one available. Article locations never replace MySeatCheck locations.
REFERENCE_MENUS = {
    "GWANGJU": {"url": "https://notes0236.tistory.com/154", "checkedAt": "2026-10-07", "items": (
        {"store": "스테이션", "stores": ("스테이션", "STATION"), "menus": (("크림새우",),),
         "locationNote": "수집 매장은 1루 3층 106블럭. 글의 3루 3층 위치로 대체하지 않음"},
        {"store": "광주원샷", "stores": ("광주원샷",), "menus": (("원샷 핫 로제 눈꽃",),),
         "articleMenu": "핫로제치킨 원샷", "locationNote": "원샷 메뉴판을 확인한 수집 지점은 1루 4층·3루 4층. 글의 3루 3층 위치로 대체하지 않음"},
        {"store": "XOXO핫도그", "stores": ("XOXO핫도그",), "menus": (("칠리치즈 핫도그",),)},
        {"store": "스트릿츄러스", "stores": ("스트릿츄러스",), "menus": (("아츄",),)},
        {"store": "요아정", "stores": ("요아정",), "menus": (("홈런의 정석",),)},
        {"store": "마왕족발", "stores": ("마왕족발",), "menus": (("족발 볶음밥",), ("비빔국수",))},
        {"store": "마성떡볶이", "stores": ("마성떡볶이",), "menus": (("4번 타자 세트",),)},
        {"store": "프랭크버거", "stores": ("프랭크버거",), "menus": (("자이언츠 팩",),)},
        {"store": "파파존스", "stores": ("파파존스",), "menus": (("아이리쉬 포테이토",),)},
        {"store": "BHC치킨", "stores": ("BHC치킨",), "menus": (("해바라기 후라이드",),)},
    )},
}


def menu_label(signature):
    return " + ".join(terms[0] for terms in signature["menus"])


def _matches(place, signature):
    if not signature or place.get("source") != "MYSEATCHECK":
        return False
    names = {menu_text(name) for name in re.split(r"[,·]", place["name"])}
    allowed = signature["stores"] + signature.get("menu_stores", ())
    if not names.intersection(menu_text(name) for name in allowed):
        return False
    groups = [[item for term in terms for item in matching_menu_items(place, term)] for terms in signature["menus"]]
    # A combined recommendation needs evidence for every dish at this exact branch.
    return all(groups)


def reference_details(place):
    """Verified reference dishes at this source branch; missing dishes stay out."""
    reference = REFERENCE_MENUS.get(place.get("stadium"))
    if not reference:
        return []
    return [{"menu": menu_label(item), "referenceUrl": reference["url"], "sourceUrl": place["placeUrl"],
             "location": place["address"], "locationNote": item.get("locationNote", ""),
             "items": [m for terms in item["menus"] for term in terms for m in matching_menu_items(place, term)]}
            for item in reference["items"] if _matches(place, item)]


def details(place):
    """Call with canonical MySeatCheck rows, never trust caller-supplied menu facts."""
    signature = SIGNATURES.get(place.get("stadium"))
    if not _matches(place, signature):
        return None
    label = menu_label(signature)
    return {"_signature_menu": label,
            "_signature_reason": f"구장 대표 메뉴는 {label}입니다. {place['name']}의 자리어때 메뉴판 사진에서 확인했어요."}
