"""Opt-in bounded live evaluation; no production changes, DB, routing or saved pages.

--extract: 22 synthetic requests; --food: 8 public candidates (2 tool calls each);
--reviews: 3 candidates x 2 traits (3 tool calls each). Two concurrent jobs max.
Each case is independent, uses current application policies, and is never retried.
Evidence pass is NOT an independently verified accuracy label. Print safe JSONL
diagnostics only; do not persist web text, reviewer identities or account details.
"""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import sys
import time


# Public IDs already observed in the checksum-validated JAMSIL collection.
FOOD_CASES = [
    ("korean", "한식", "대복삼계탕", "MA0101202512A0019883", "삼계탕", "삼계탕"),
    ("chinese", "중식", "희래등", "MA010120220812491899", "짜장면", "자장면"),
    ("japanese", "일식", "스시메시", "MA010120220814092673", "초밥", "초밥"),
    ("western", "양식", "아메리칸테이블", "MA010120220809674694", "파스타", "파스타"),
    ("snack", "분식", "진김밥", "MA0101202411A0053780", "김밥", "김밥"),
    ("chicken", "치킨", "페리카나치킨", "MA010120220804237305", "후라이드 치킨", "후라이드 치킨"),
    ("vietnamese", "베트남", "에머이 삼성점", "MA010120220808627837", "쌀국수", "쌀국수"),
]
NEGATIVE_CASE = ("coarse-category-control", "치킨", "크라이치즈버거", "MA010120220810326883", "후라이드 치킨", "후라이드 치킨")
# Deliberate representative examples, NOT a search-volume popularity ranking.
# mode is the current supported boundary, not a promise that every feature works.
KEYWORD_CASES = [
    ("quiet", "반드시 조용한", "review", "quietness", "required"),
    ("clean", "반드시 매장과 식기가 깨끗한", "review", "cleanliness", "required"),
    ("conversation", "가능하면 소음이 적어서 대화하기 좋은", "review", "quietness", "preferred"),
    ("tidy-interior", "반드시 인테리어가 깔끔한", "unverified", "인테리어", "required"),
    ("date", "반드시 데이트하기 좋은 분위기의", "unverified", "데이트", "required"),
    ("aesthetic", "반드시 감성적인 분위기의", "unverified", "감성", "required"),
    ("view", "반드시 뷰가 좋은", "unverified", "뷰|전망|경치", "required"),
    ("cozy", "반드시 아늑한 분위기의", "unverified", "아늑", "required"),
    ("solo", "반드시 혼밥하기 좋은", "unverified", "혼밥|혼자", "required"),
    ("family", "반드시 가족 모임하기 좋은", "unverified", "가족", "required"),
    ("value", "가능하면 가성비 좋은", "unverified", "가성비", "preferred"),
    ("friendly", "반드시 직원이 친절한", "unverified", "친절", "required"),
    ("parking", "반드시 주차 가능한", "unverified", "주차", "required"),
    ("price", "반드시 메뉴 가격이 1인당 1만원 이하인", "unverified", "만원|10000|10,000", "required"),
    ("open", "반드시 방문 예정 시간에 영업 중인", "unverified", "영업", "required"),
]


def emit(value):
    print(json.dumps(value, ensure_ascii=False, default=str), flush=True)


def error_metadata(exc):
    """Keep schema diagnostics without raw response, input values or credentials."""
    from pydantic import ValidationError
    result = {"error_type": type(exc).__name__}
    if isinstance(exc, ValidationError):
        result["validation_errors"] = [{"location": list(error["loc"]), "type": error["type"]}
            for error in exc.errors(include_url=False, include_context=False, include_input=False)]
    return result


def setup():
    from dotenv import load_dotenv
    backend = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(backend))
    load_dotenv(backend / ".env", override=False)
    load_dotenv(backend.parent / ".env", override=False)
    os.environ["DJANGO_SETTINGS_MODULE"] = "llm.tests.course_settings"
    import django
    django.setup()


def catalogue_cases():
    from travel.collected_places import planning_catalogue
    data = planning_catalogue("JAMSIL")
    places = {p["placeId"]: p for p in data["places"]}
    selected = {}
    for case in [*FOOD_CASES, NEGATIVE_CASE]:
        label, _, name, identity, _, _ = case
        candidate = places["collected:SBIZ:" + identity]
        if candidate["name"] != name or candidate["kind"] != "food":
            raise ValueError("Fixture identity changed; inspect the catalogue before spending")
        selected[label] = candidate
    return selected, data["snapshotId"]


def extract_case(case, *, food=False):
    from llm.v2.course.itinerary_request import KST, extract_itinerary
    now = datetime.now(KST)
    game = (now + timedelta(days=1)).replace(hour=18, minute=30, second=0, microsecond=0)
    anchor = {"stadium_code": "JAMSIL", "stadium_name": "잠실야구장", "starts_at": game.isoformat()}
    if food:
        label, cuisine, _, _, menu, wording = case
        question = f"경기 전에 외부 {cuisine} 식당 한 곳만 들를래. {wording}을 파는 곳으로 해줘."
    else:
        label, phrase, mode, expected, priority = case
        question = f"경기 전에 외부 식당 한 곳만 들를래. {phrase} 식당으로 해줘."
    result = extract_itinerary(question, anchor, {}, now=now)
    conditions = [term for stop in result.stops if stop.food for term in stop.food.conditions().values()]
    reviews = [c for stop in result.stops if stop.reviews for c in stop.reviews.all_of]
    hard = result.unverified_requirements + [v for stop in result.stops for v in stop.unverified_requirements]
    soft = result.soft_notes
    name_filters = [v for stop in result.stops for v in stop.required_keywords + stop.excluded_keywords]
    if food:
        # Keep exact conditions for manual interpretation of any unexpected label.
        compact_menu = menu.replace(" ", "")
        menu_ok = any(t.kind == "menu" and (t.name.replace(" ", "") == compact_menu or
                      (menu == "후라이드 치킨" and t.name.replace(" ", "") in ("프라이드치킨", "후라이드", "치킨"))) and not t.exclude for t in conditions)
        cuisine_ok = any(t.kind == "cuisine" and cuisine in t.name and not t.exclude for t in conditions)
        ok = menu_ok and cuisine_ok and not hard and not name_filters and not reviews
    else:
        if mode == "review":
            ok = len(reviews) == 1 and reviews[0].aspect == expected and reviews[0].priority == priority and not hard
        else:
            notes = hard if priority == "required" else soft
            ok = any(token in " ".join(notes) for token in expected.split("|")) and not reviews and not conditions
            if result.clarification and priority == "required":
                ok = True  # Explicit clarification is a safe alternative to guessing.
        ok &= not name_filters
    ok &= len(result.stops) == 1 and result.stops[0].kind == "food" and result.stops[0].phase == "before"
    return {"stage": "extract", "case": label, "question": question, "expectation_met": bool(ok),
            "food": [t.model_dump() for t in conditions], "reviews": [c.model_dump() for c in reviews],
            "unverified": hard, "soft_notes": soft, "name_filters": name_filters, "clarification": result.clarification}


def food_case(case, candidate):
    from llm.v2.course.food_requirements import FoodRequirements
    from llm.v2.course.food_verification import FoodVerifier, search_food
    label, cuisine, _, _, menu, _ = case
    need = FoodRequirements(all_of=[{"any_of": [{"kind": kind, "name": name}]}
                                    for kind, name in (("cuisine", cuisine), ("menu", menu))])
    observed = {}
    def lookup(*args, **kwargs):
        try:
            answer, urls, calls = search_food(*args, **kwargs)
            observed.update(completed=True, identity=answer.identity, scope=answer.scope, tool_calls=calls,
                            observed_url_count=len(urls), source_kinds=[e.kind for e in answer.evidence])
            return answer, urls, calls
        except Exception as exc:
            observed.update(error_metadata(exc))
            raise
    verifier = FoodVerifier(lookup=lookup)
    report = verifier.verify(candidate, need)
    return {"stage": "food", "case": label, "name": candidate["name"], "address": candidate["address"],
            "catalogue_cuisine": candidate["cuisine"], "status": report["status"], "reason": report["reason"],
            "conditions": report["conditions"], "sources": [s["url"] for s in report["sources"]],
            "audit": verifier.audit(), "observed": observed}


def review_case(label, candidate):
    from llm.v2.course.itinerary_request import KST
    from llm.v2.course.review_requirements import ReviewRequirements
    from llm.v2.course.review_verification import ReviewVerifier, evaluate_reviews, search_reviews
    need = ReviewRequirements(all_of=[{"aspect": aspect, "priority": "required"}
                                      for aspect in ("quietness", "cleanliness")])
    observed = {}
    def lookup(*args, **kwargs):
        try:
            answer, urls, calls = search_reviews(*args, **kwargs)
            # Metadata only; no reviewer identities, prose, raw API response or pages.
            observed.update(completed=True, identity=answer.identity, scope=answer.scope, tool_calls=calls,
                observed_url_count=len(urls), raw_observation_count=len(answer.observations),
                observation_flags=[{k: o.model_dump(mode="json")[k] for k in (
                    "aspect", "polarity", "same_branch", "body_read", "kind", "promotion", "published_on", "visited_on", "context")}
                                   for o in answer.observations])
            return answer, urls, calls
        except Exception as exc:
            observed.update(error_metadata(exc))
            raise
    verifier = ReviewVerifier(lookup=lookup)
    now = datetime.now(KST)
    arrival = (now + timedelta(days=1)).replace(hour=16, minute=0, second=0, microsecond=0)
    report = evaluate_reviews(verifier.verify(candidate, need), need, arrival=arrival, departure=arrival + timedelta(hours=1))
    return {"stage": "reviews", "case": label, "name": candidate["name"], "address": candidate["address"],
            "status": report["status"], "eligible": report["eligible"], "reason": report["reason"],
            "accepted_observation_count": len(report["observations"]), "conditions": report["conditions"],
            "sources": [s["url"] for s in report["sources"]], "audit": verifier.audit(), "observed": observed}


def main():
    parser = argparse.ArgumentParser()
    for flag in ("extract", "food", "reviews", "list"):
        parser.add_argument("--" + flag, action="store_true")
    parser.add_argument("--case", help="Run just one named case, still requiring an explicit stage")
    args = parser.parse_args()
    if not any((args.extract, args.food, args.reviews, args.list)):
        parser.error("Select --list (offline) or explicitly opt into --extract/--food/--reviews paid calls")
    setup()
    selected, snapshot = catalogue_cases()
    if args.list:
        emit({"snapshot": snapshot, "candidates": {key: {k: p[k] for k in ("placeId", "name", "address", "cuisine")}
                                                   for key, p in selected.items()}, "keywords": KEYWORD_CASES})
        return 0
    from llm.v2.agent.common import llm
    emit({"model": llm().model_name, "snapshot": snapshot, "max_workers": 2, "no_retries": True})
    jobs = []
    if args.extract:
        jobs += [(case[0], lambda c=case: extract_case(c, food=True)) for case in FOOD_CASES]
        jobs += [(case[0], lambda c=case: extract_case(c)) for case in KEYWORD_CASES]
    if args.food:
        jobs += [(case[0], lambda c=case: food_case(c, selected[c[0]])) for case in [*FOOD_CASES, NEGATIVE_CASE]]
    if args.reviews:
        jobs += [(label, lambda label=label: review_case(label, selected[label])) for label in ("korean", "japanese", "western")]
    if args.case:
        jobs = [(label, fn) for label, fn in jobs if label == args.case]
        if not jobs:
            parser.error("No matching case in the selected stages")
    emit({"planned_responses": len(jobs)})
    results = []
    def execute(label, fn):
        started = time.monotonic()
        try:
            return {**fn(), "elapsed_seconds": round(time.monotonic() - started, 2)}
        except Exception as exc:
            return {"case": label, **error_metadata(exc), "expectation_met": False,
                    "elapsed_seconds": round(time.monotonic() - started, 2)}
    with ThreadPoolExecutor(max_workers=2) as executor:
        pending = [executor.submit(execute, label, fn) for label, fn in jobs]
        for future in as_completed(pending):
            result = future.result()
            results.append(result)
            emit(result)
    emit({"summary": {"cases": len(results),
                      "extraction_checks_passed": sum(r.get("stage") == "extract" and r.get("expectation_met") is True for r in results),
                      "extraction_checks_failed": sum(r.get("stage") == "extract" and r.get("expectation_met") is False for r in results),
                      "error_cases": sum("error_type" in r or "error_type" in r.get("observed", {}) for r in results),
                      "food_outcomes": dict(Counter(r["status"] for r in results if r.get("stage") == "food")),
                      "review_outcomes": dict(Counter(r["status"] for r in results if r.get("stage") == "reviews"))}})
    # A web unknown is not an infrastructure failure and must not be counted as a trait pass.
    return 1 if any(r.get("expectation_met") is False or "error_type" in r.get("observed", {}) for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
