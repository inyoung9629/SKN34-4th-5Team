"""Fast ordinary recommendations; explicit evidence requests keep full research."""
import re
from contextlib import contextmanager
from contextvars import ContextVar
from django.conf import settings

_BALANCED = ContextVar("course_balanced_recommendation", default=False)
EXPLICIT_RESEARCH = re.compile(r"평점|후기|리뷰|실내|위생|청결|깔끔|조용|알레르기|비건|휠체어|주차|실제\s*메뉴|메뉴.{0,12}(?:확인|근거)|근거.{0,12}확인")
NOTICE = "거리와 업종을 기준으로 구성했어요. 일부 매장의 평점·실내 상태는 아직 확인되지 않았으며, 영업시간은 방문 전에 확인해 주세요."


def enabled():
    return getattr(settings, "COURSE_RECOMMENDATION_MODE", "strict") == "balanced"


def balanced():
    return _BALANCED.get()


def explain(result):
    from . import place_quality
    from travel.stadium_food import resolve_food_place
    if balanced() and place_quality.active() and any(
            p.get("category") in {"FOOD", "FOOD_OUT", "CAFE"} and not resolve_food_place(p)
            and not place_quality.resolve(p) for p in result.get("places", [])):
        if NOTICE not in result.get("answer", ""):
            result["answer"] = result.get("answer", "").rstrip() + "\n\n" + NOTICE
    return result


@contextmanager
def request(question):
    token = _BALANCED.set(enabled() and not EXPLICIT_RESEARCH.search(question))
    try:
        yield
    finally:
        _BALANCED.reset(token)
