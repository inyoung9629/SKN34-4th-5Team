"""Opt-in paid probe: six synthetic requests and/or one public restaurant search.

No app DB, route API, shared memory or saved pages. Run --extract and/or --web.
Unknown review evidence is a valid result, not a successful trait confirmation.
"""
import argparse
import os
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--extract", action="store_true")
    parser.add_argument("--web", action="store_true")
    args = parser.parse_args()
    if not args.extract and not args.web:
        parser.error("Specify --extract or --web to opt into paid calls")
    from dotenv import load_dotenv
    backend = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(backend))
    load_dotenv(backend / ".env", override=False)
    load_dotenv(backend.parent / ".env", override=False)
    os.environ["DJANGO_SETTINGS_MODULE"] = "llm.tests.course_settings"
    import django
    django.setup()
    from datetime import datetime, timedelta
    from llm.v2.agent.common import llm
    from llm.v2.course.itinerary_request import KST, extract_itinerary
    from llm.v2.course.review_requirements import ReviewRequirements
    from llm.v2.course.review_verification import ReviewVerifier, evaluate_reviews, search_reviews
    now = datetime.now(KST)
    anchor = {"stadium_code": "JAMSIL", "stadium_name": "잠실야구장", "starts_at": "2026-10-01T18:30:00+09:00"}
    print("Configured model:", llm().model_name, flush=True)
    passed = True
    if args.extract:
        def conditions(r):
            return {c.aspect: c.priority for s in r.stops if s.reviews for c in s.reviews.all_of}
        previous = {"stops": [{"kind": "food", "reviews": {"all_of": [{"aspect": "quietness", "priority": "required"}]}}]}
        cases = [
            ("required-and-menu", "경기 전 외부 식당 한 곳만. 돈카츠를 팔고 조용하고 매장도 깨끗한 곳으로.", None,
             lambda r: conditions(r) == {"quietness": "required", "cleanliness": "required"} and bool(r.stops[0].food)
             and not r.unverified_requirements and not r.stops[0].unverified_requirements),
            ("preferences", "외부 식당 한 곳만. 가능하면 조용하고 가능하면 매장도 깔끔하면 좋겠어.", None,
             lambda r: conditions(r) == {"quietness": "preferred", "cleanliness": "preferred"}),
            ("taste-not-cleanliness", "외부 식당 한 곳만. 반드시 국물 맛이 깔끔한 집. 매장 청결도 조건을 말한 게 아니야.", None,
             lambda r: not conditions(r) and bool(r.unverified_requirements or r.stops[0].unverified_requirements)),
            ("cancel-memory", "이전 조용함 조건은 취소할게. 시끄러워도 괜찮으니 외부 식당 한 곳만.", previous,
             lambda r: not conditions(r)),
            ("migrate-memory", "같은 조건으로 다시 짜줘. 외부 식당 한 곳만.",
             {"stops": [{"kind": "food", "unverified_requirements": ["조용한 매장", "주차 가능"]}]},
             lambda r: conditions(r) == {"quietness": "required"} and
             "주차" in " ".join(r.unverified_requirements + r.stops[0].unverified_requirements)),
            ("internal-retained", "구장 내부 매점 중 반드시 조용하고 깨끗한 곳 한 군데 들를래.", None,
             lambda r: r.stops[0].phase == "inside" and bool(conditions(r))),
        ]
        for label, question, previous_request, expected in cases:
            try:
                result = extract_itinerary(question, anchor, {}, previous_request, now=now)
                ok = expected(result)
                print(("PASS " if ok else "FAIL ") + label, flush=True)
                if not ok:
                    print(result.model_dump_json(exclude_none=True), flush=True)
                passed &= ok
            except Exception as exc:
                print("ERROR", label, type(exc).__name__, flush=True)
                passed = False
    if args.web:
        from travel.collected_places import planning_catalogue
        restaurant = next(p for p in planning_catalogue("MUNHAK")["places"] if p["name"] == "백소정구월 로데오점")
        need = ReviewRequirements(all_of=[{"aspect": aspect, "priority": "required"}
                                          for aspect in ("quietness", "cleanliness")])
        observed = {}
        def lookup(*values, **options):
            try:
                response = search_reviews(*values, **options)
                observed["completed"] = True
                return response
            except Exception as exc:
                print("WEB_ERROR", type(exc).__name__, getattr(exc, "status_code", None), flush=True)
                raise
        verifier = ReviewVerifier(lookup=lookup)
        report = evaluate_reviews(verifier.verify(restaurant, need), need, arrival=now,
                                  departure=now + timedelta(hours=1))
        print("WEB", report["status"], report["reason"], verifier.audit(), flush=True)
        print("CONDITIONS", report["conditions"], flush=True)
        print("SOURCES", [s["url"] for s in report["sources"]], flush=True)
        passed &= bool(observed.get("completed")) and verifier.tool_calls > 0
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
