"""A generic food/snack request is not a specific menu constraint."""
import re

_TERMS = re.compile(r"(?:간단한|가벼운|맛있는|대표|시그니처|시그니쳐|기본)?(?:간식|먹거리|음식|식사|메뉴)")
_WORDS = (
    "야구장", "경기장", "구장", "내부", "안쪽", "내", "안", "에서", "에있는", "에", "의",
    "경기전", "경기후", "입장후", "전에", "후에", "간식", "먹거리", "식사", "음식", "끼니", "메뉴", "밥",
    "시그니처", "시그니쳐", "대표", "기본", "간단한", "가벼운", "맛있는", "간단히", "가볍게", "좀",
    "뭐", "뭘", "아무거나", "하나", "한곳", "을", "를", "은", "는", "만",
    "먹고싶어요", "먹고싶어", "먹고싶은데", "먹을래", "먹을까", "먹자", "먹은다음", "먹고", "먹기",
    "추천해주세요", "추천해줘", "추천해", "추천", "추가해줘", "넣어줘", "먹을거", "먹을것", "골라줘", "다음",
)
_GENERIC = re.compile("(?:" + "|".join(map(re.escape, sorted(_WORDS, key=len, reverse=True))) + ")+")


def compact(text):
    return re.sub(r"\s+", "", str(text or "")).strip(".!?,")


def generic_term(text):
    return bool(_TERMS.fullmatch(compact(text)))


def use_default(expression, kind, query="", conditions=(), marked=False):
    if kind not in ("FOOD", "CAFE"):
        return False
    if query and not generic_term(query):
        return False
    if any(c != expression and not generic_term(c) for c in conditions or ()):
        return False
    text = compact(expression)
    broad = bool(re.search(r"구장|경기장|야구장", text) and re.search(r"먹|식사|간식|음식|메뉴|끼니|밥", text)
                 and _GENERIC.fullmatch(text))
    return broad or (marked is True and kind == "FOOD")
