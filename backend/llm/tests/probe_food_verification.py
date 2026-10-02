"""Opt-in paid smoke checks: synthetic intent + one public catalogue restaurant.

Run from the repository root with --extract and/or --web. No DB, route API, or
persisted web pages. At most six extraction responses and one two-tool web response.
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
        parser.error("Specify --extract or --web to opt into paid API calls")
    from dotenv import load_dotenv
    backend = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(backend))
    load_dotenv(backend / ".env", override=False)
    load_dotenv(backend.parent / ".env", override=False)
    os.environ["DJANGO_SETTINGS_MODULE"] = "llm.tests.course_settings"
    import django
    django.setup()
    from datetime import datetime
    from llm.v2.agent.common import llm
    from llm.v2.course.itinerary_request import KST, extract_itinerary
    from llm.v2.course.food_requirements import FoodRequirements
    from llm.v2.course.food_verification import FoodVerifier, search_food
    now = datetime.now(KST)
    anchor = {"stadium_code": "JAMSIL", "stadium_name": "잠실야구장", "starts_at": "2026-10-01T18:30:00+09:00"}
    print("Configured model:", llm().model_name, flush=True)
    passed = True
    if args.extract:
        def terms(result):
            return list(result.stops[0].food.conditions().values()) if result.stops[0].food else []

        cases = [
            ("aliases", "돈가스나 돈카츠 먹고 경기장 갈래. 카페는 필요 없어.",
             lambda r: bool(terms(r)) and all(t.name == "돈까스" for t in terms(r))),
            ("and", "돈까스와 우동을 둘 다 파는 외부 식당 한 곳만 들를래.",
             lambda r: len(r.stops) == 1 and len(r.stops[0].food.all_of) == 2),
            ("or", "짜장면이나 짬뽕 중 하나 먹을래. 식당 한 곳만.",
             lambda r: len(r.stops[0].food.all_of) == 1 and len(terms(r)) == 2),
            ("dish-qualifier", "외부에서 치즈 없는 안심 돈카츠 먹고 경기장 갈래. 치즈 메뉴도 파는 식당이어도 괜찮아.",
             lambda r: any("안심" in " ".join(t.qualifiers) and "치즈" in " ".join(t.qualifiers) and not t.exclude for t in terms(r))
             and not r.stops[0].excluded_keywords),
            ("outside-scope", "외부에서 돈까스 먹을래. 반드시 만 원 이하고 땅콩 알레르기에도 안전해야 해.",
             lambda r: bool(terms(r)) and bool(r.unverified_requirements or r.stops[0].unverified_requirements)),
            ("inside-retained", "구장 내부에서 치즈돈까스 먹고 경기 볼래.",
             lambda r: r.stops[0].phase == "inside" and bool(terms(r))),
        ]
        for label, question, expected in cases:
            try:
                result = extract_itinerary(question, anchor, {}, now=now)
                ok = expected(result)
                print(("PASS " if ok else "FAIL ") + label, flush=True)
                if not ok:
                    print(result.model_dump_json(exclude_none=True), flush=True)
                passed &= ok
            except Exception as exc:
                print("ERROR", label, type(exc).__name__, flush=True)
                passed = False
    if args.web:
        # Use the checked snapshot adapter. Send only this business's public name/address.
        from travel.collected_places import planning_catalogue
        candidates = planning_catalogue("MUNHAK")["places"]
        restaurant = next(p for p in candidates if p["name"] == "백소정구월 로데오점")
        need = FoodRequirements.model_validate({"all_of": [{"any_of": [{"kind": "menu", "name": "돈까스"}]}]})
        observed = {}

        def lookup(*values, **options):
            try:
                result = search_food(*values, **options)
                observed["completed"] = True
                return result
            except Exception as exc:
                # Safe diagnostics only: never print exception body or account/key data.
                print("WEB_ERROR", type(exc).__name__, getattr(exc, "status_code", None), flush=True)
                raise

        verifier = FoodVerifier(lookup=lookup)
        report = verifier.verify(restaurant, need)
        print("WEB", report["status"], report["reason"], verifier.audit(), flush=True)
        print("SOURCES", [source["url"] for source in report["sources"]], flush=True)
        # Unknown is a valid evidence outcome, not a successful menu verification.
        passed &= bool(observed.get("completed")) and verifier.tool_calls > 0
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
