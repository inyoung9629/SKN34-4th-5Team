r"""옵트인 유료 모델 스모크 검사. 가상 발화만 전송하며 실제 DB/일정 API는 사용하지 않는다.

저장소 루트: .\.venv\Scripts\python.exe backend/llm/tests/probe_course_extraction.py
API 키/오류 원문은 출력하지 않는다. 자동 test discovery 대상이 아니다.
"""
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path


def main():
    from dotenv import load_dotenv
    backend = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(backend))
    load_dotenv(backend / ".env", override=False)
    load_dotenv(backend.parent / ".env", override=False)
    # 프로덕션 settings/.env의 DATABASES는 사용하지 않는다.
    os.environ["DJANGO_SETTINGS_MODULE"] = "llm.tests.course_settings"
    import django
    django.setup()
    from llm.v2.course.conditions import ConditionPatch, extract_conditions
    from llm.v2.course.games import KST
    from llm.v2.course.state import empty_state, merge_patch

    now = datetime(2026, 9, 28, 12, tzinfo=KST)
    catalog = [{"stadium_code": "JAMSIL", "stadium_name_ko": "잠실야구장"},
               {"stadium_code": "GOCHEOK", "stadium_name_ko": "고척스카이돔"},
               {"stadium_code": "DAEGU", "stadium_name_ko": "대구 삼성 라이온즈 파크"}]
    old, _ = merge_patch(None, ConditionPatch(team_code="LG", stadium_code="JAMSIL", date_from=now.date()))
    candidates = empty_state()
    candidates.update(pending="choice", candidates=[
        {"id": 41, "side": "home", "date": "2026-09-29", "time": "18:30:00", "stadium_code": "JAMSIL"},
        {"id": 42, "side": "away", "date": "2026-09-30", "time": "18:30:00", "stadium_code": "DAEGU"},
    ])
    cases = [
        ("missing-target", "일식 먹고 카페 가는 직관 코스 짜줘", empty_state(),
         lambda p: not p.team_code and not p.stadium_code and not p.game_id and bool(p.preferences.get("food"))),
        ("target-vs-fandom", "두산 팬이지만 내일은 삼성 원정 경기를 보러 갈래. 오후 3시에 출발해서 카페도 갈 거야.", empty_state(),
         lambda p: p.team_code == "SS" and p.home_away == "away" and str(p.date_from) == "2026-09-29"
         and not p.game_time_min and not p.game_time_max and bool(p.preferences.get("start"))),
        ("ambiguous-city", "서울에서 야구 보고 식사하는 코스 짜줘", empty_state(),
         lambda p: not p.stadium_code and not p.team_code),
        ("profile-with-date", "내 응원팀 기준으로 이번 토요일 직관 코스 짜줘", empty_state(),
         lambda p: p.profile == "allow" and not p.team_code and str(p.date_from) == "2026-10-03"),
        ("clear-vs-keep", "구장은 어디든 괜찮아. 기존 날짜는 유지하고 삼성으로 바꿔줘", old,
         lambda p: p.team_code == "SS" and "stadium_code" in p.clear_fields
         and "date_from" not in p.clear_fields and "date_to" not in p.clear_fields),
        ("candidate-choice", "2안으로 해줘", candidates,
         lambda p: p.choice == "second" or p.game_id == 42),
    ]

    def check(case):
        label, question, state, expectation = case
        try:
            result = extract_conditions(question, state, catalog, now)
        except Exception as exc:
            print(f"ERROR {label}: {type(exc).__name__}", flush=True)
            return False
        passed = expectation(result)
        print(f"{'PASS' if passed else 'FAIL'} {label}", flush=True)
        if not passed:
            print(result.model_dump_json(exclude_none=True), flush=True)
        return passed

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(check, cases))
    print(f"{sum(results)}/{len(results)} extraction probes passed", flush=True)
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
