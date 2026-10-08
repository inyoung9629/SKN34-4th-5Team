from contextlib import ExitStack
from copy import deepcopy
from datetime import date, datetime
from unittest.mock import Mock, patch

import httpx
from django.test import SimpleTestCase

from llm.v1.rag.course import agent, availability as hours, editing, evidence_memory, grounding, timeline
from travel.public_page_reader import PublicReader, extract_dated_hours, extract_text
from .test_course_route_ranking import ANCHOR, ORIGIN, NEAR, FAR, CAFE, PARK, provider


DAY = "2026-10-06"  # Tuesday, Asia/Seoul
SHOP = {"placeId": "hours:shop", "name": "가상 식당", "address": "서울 송파구 올림픽로 10", "category": "FOOD"}


def document(text, **changes):
    return {"body_read": True, "status": "read", "title": "가상 식당 - 잠실 음식점", "url": "https://www.diningcode.com/profile.php?rid=hours",
            "body_text": "가상 식당\n서울특별시 송파구 올림픽로 10\n" + text, **changes}


class HoursRulesTests(SimpleTestCase):
    def test_date_tabs_are_not_promoted_to_recurring_weekly_hours(self):
        info = hours.parse("오늘(월)\n영업시간: 11:30 - 19:30\n10월 6일(화)\n영업시간: 11:30 - 19:30",
                           observed_on=date(2026, 10, 5))
        self.assertTrue(hours._verdict(info, date(2026, 10, 6), 1380, 50)[0])
        self.assertEqual(hours._verdict(info, date(2026, 10, 13), 1380, 50)[0], "")
        closed = hours.parse("영업시간\n10월 6일(화)\n휴무", observed_on=date(2026, 10, 5))
        self.assertEqual(hours._verdict(closed, date(2026, 10, 6), 1080, 50)[0], "휴무일")
        self.assertEqual(hours._verdict(closed, date(2026, 10, 13), 1080, 50)[0], "")
        special = hours.parse("영업시간\n매주 화요일 휴무\n10월 6일(화)\n11:00 - 22:00", observed_on=date(2026, 10, 5))
        self.assertEqual(hours._verdict(special, date(2026, 10, 6), 1080, 50)[0], "")
        self.assertEqual(hours._verdict(special, date(2026, 10, 6), None, 0)[0], "")
        self.assertEqual(hours._verdict(special, date(2026, 10, 13), 1080, 50)[0], "휴무일")

    def test_flattened_date_columns_do_not_apply_a_closure_or_hours_to_every_date(self):
        info = hours.parse("오늘(수)\n영업시간: 11:30 - 22:00\n"
                           "10월 8일(목) 10월 9일(금) 10월 10일(토) 10월 11일(일) 10월 12일(월) 10월 13일(화)\n"
                           "영업시간: 11:30 - 22:00\n브레이크타임: 14:00 - 16:00\n라스트오더: 21:00\n휴무일",
                           observed_on=date(2026, 10, 7))
        for day in range(8, 14):
            for start in (None, 600, 870, 1290):
                with self.subTest(day=day, start=start):
                    self.assertEqual(hours._verdict(info, date(2026, 10, day), start, 50), ("", False))
        self.assertIn("영업시간 밖", hours._verdict(info, date(2026, 10, 7), 600, 50)[0])

    def test_explicit_date_or_weekday_after_ambiguous_tabs_restores_scope(self):
        prefix = "영업시간\n10월 8일(목) 10월 9일(금)\n휴무일\n"
        for suffix in ("10월 12일(월)\n휴무일", "2026-10-12 휴무", "매주 월요일 휴무"):
            info = hours.parse(prefix + suffix, observed_on=date(2026, 10, 7))
            self.assertEqual(hours._verdict(info, date(2026, 10, 10), None, 0)[0], "")
            self.assertEqual(hours._verdict(info, date(2026, 10, 12), None, 0)[0], "휴무일")

    def test_single_date_resets_previous_weekday_and_full_date_scopes_following_line(self):
        for marker in ("10월 10일(토)", "2026-10-10", "2026-10-10 (토)"):
            info = hours.parse("영업시간\n매주 월요일 휴무\n" + marker + "\n휴무",
                               observed_on=date(2026, 10, 7))
            self.assertEqual(hours._verdict(info, date(2026, 10, 10), None, 0)[0], "휴무일")
            self.assertEqual(hours._verdict(info, date(2026, 10, 17), None, 0)[0], "")

    def verdict(self, text, at="18:00", stay=50, day=DAY, **page_changes):
        with hours.session():
            hours.observe(SHOP, document(text, **page_changes))
            return hours.check(SHOP, day, {"time": at, "stayMin": stay})

    def test_unknown_empty_ambiguous_and_unread_pages_keep_candidate(self):
        for text in ("메뉴정보\n자장면", "영업시간\n업체 문의", "영업시간\n시간 변동 가능", "영업시간\n11:00 ~ 마감 시", "영업시간\n매월 둘째 화요일 휴무"):
            self.assertEqual(self.verdict(text), "")
        self.assertEqual(self.verdict("폐업", body_read=False), "")

    def test_closed_before_open_after_close_and_exact_boundary(self):
        text = "영업시간\n매일 11:00 - 22:00"
        self.assertEqual(self.verdict(text, "11:00"), "")
        self.assertEqual(self.verdict(text, "21:10"), "")
        for at in ("10:59", "21:11", "22:00"):
            self.assertIn("영업시간 밖", self.verdict(text, at))

    def test_korean_clocks_and_am_pm(self):
        for text in ("매일 오전 11:00 - 오후 10:00", "매일 11시 ~ 22시"):
            self.assertEqual(self.verdict("영업시간\n" + text, "20:00"), "")
            self.assertIn("영업시간 밖", self.verdict("영업시간\n" + text, "22:00"))

    def test_weekday_weekend_and_weekday_range(self):
        text = "영업시간\n평일 11:00 - 18:00\n주말 11:00 - 23:00"
        self.assertIn("영업시간 밖", self.verdict(text))
        self.assertEqual(self.verdict(text, day="2026-10-10"), "")
        self.assertEqual(self.verdict("영업시간\n월~금 11:00 - 22:00"), "")

    def test_regular_day_off_and_separate_heading(self):
        for text in ("영업시간\n매일 11:00 - 22:00\n매주 화요일 정기휴무", "휴무일\n매주 화요일", "매주 화요일 휴무"):
            self.assertEqual(self.verdict(text), "휴무일")
            self.assertEqual(self.verdict(text, day="2026-10-07"), "")

    def test_dated_exception_and_special_open_day(self):
        self.assertEqual(self.verdict("휴무일\n2026-10-06 휴무"), "휴무일")
        self.assertEqual(self.verdict("휴무일\n2026-10-07 휴무"), "")
        self.assertEqual(self.verdict("영업시간\n매주 화요일 휴무\n2026-10-06 11:00 - 22:00"), "")

    def test_break_and_split_sessions_reject_overlap(self):
        text = "영업시간\n매일 11:00 - 22:00\n브레이크타임 15:00 - 17:00"
        self.assertEqual(self.verdict(text, "14:10"), "")
        self.assertEqual(self.verdict(text, "14:11"), "브레이크타임")
        self.assertEqual(self.verdict(text, "17:00"), "")
        self.assertIn("영업시간 밖", self.verdict("영업시간\n매일 11:00 - 15:00\n17:00 - 22:00", "14:30"))

    def test_last_order_inline_and_separate(self):
        for suffix in ("\n라스트오더 21:00", " (21:00 라스트오더)"):
            self.assertEqual(self.verdict("영업시간\n매일 11:00 - 22:00" + suffix, "21:00", 20), "주문 마감 이후")

    def test_overnight_and_previous_day_last_order(self):
        text = "영업시간\n매일 18:00 - 02:00\n라스트오더 01:30"
        self.assertEqual(self.verdict(text, "익일 00:30", 40), "")
        self.assertEqual(self.verdict(text, "익일 01:30", 20), "주문 마감 이후")
        self.assertIn("영업시간 밖", self.verdict(text, "익일 01:40", 30))

    def test_unknown_today_is_not_inferred_from_yesterdays_hours(self):
        self.assertEqual(self.verdict("영업시간\n월요일 18:00 - 02:00", "18:00"), "")
        self.assertEqual(self.verdict("영업시간\n월요일 18:00 - 02:00", "00:30"), "")

    def test_day_offset_uses_actual_calendar_day(self):
        with hours.session():
            hours.observe(SHOP, document("휴무일\n매주 수요일 휴무"))
            self.assertEqual(hours.check(SHOP, DAY, {"time": "익일 00:10", "stayMin": 30}), "휴무일")
            self.assertEqual(hours.check(SHOP, DAY, {"time": "18:00", "stayMin": 30}), "")

    def test_today_closed_badge_does_not_prove_future_closure(self):
        for text in ("영업시간\n현재 영업 종료", "영업시간\n오늘 휴무", "영업시간\n임시휴업"):
            self.assertEqual(self.verdict(text), "")

    def test_facility_hours_are_not_business_hours(self):
        for text in ("수영장 운영시간 06:00 - 10:00", "수영장\n운영시간\n06:00 - 10:00", "조식 이용시간\n07:00 - 10:00"):
            self.assertEqual(self.verdict(text), "")
        self.assertEqual(self.verdict("주차장\n운영시간\n08:00 - 10:00\n매장 영업시간\n매일 11:00 - 22:00"), "")

    def test_permanent_closure_and_marked_title(self):
        self.assertEqual(self.verdict("폐업"), "폐업")
        self.assertEqual(self.verdict("영업 상태: 영구 영업 종료"), "폐업")
        self.assertEqual(self.verdict("매장 정보", title="가상 식당 (폐업) - 잠실 음식점"), "폐업")

    def test_historical_review_other_place_and_negation_are_not_closure(self):
        for text in ("폐업이 아닙니다", "방문자 리뷰\n폐업\n영업시간\n매주 화요일 휴무", "예전에 옆 식당이 폐업했어요"):
            self.assertEqual(self.verdict(text), "")
        self.assertEqual(self.verdict("폐업", title="다른 식당 - 잠실"), "")
        self.assertEqual(self.verdict("폐업", body_text="서울 송파구 올림픽로 100\n폐업"), "")

    def test_disagreeing_sources_remain_uncertain(self):
        with hours.session():
            hours.observe(SHOP, document("폐업"))
            hours.observe(SHOP, document("영업시간\n매일 11:00 - 22:00", url="https://polle.com/place/fixture"))
            self.assertEqual(hours.check(SHOP, DAY, {"time": "18:00", "stayMin": 50}), "")

    def test_twenty_four_hours_and_missing_visit_date(self):
        self.assertEqual(self.verdict("24시간 영업", "23:30", 50), "")
        self.assertEqual(self.verdict("영업시간\n매일 11:00 - 12:00", day=None), "")
        self.assertEqual(self.verdict("폐업", day=None), "폐업")

    def test_request_scope_does_not_leak_to_next_course(self):
        self.assertEqual(self.verdict("폐업"), "폐업")
        self.assertEqual(hours.check(SHOP, DAY), "")


class DatedHoursPageTests(SimpleTestCase):
    def grid(self, dates, times, *, times_first=False, today=False):
        date_column = '<div class="hour-dates-column">' + ''.join(
            f'<span class="hour_date">{day}</span>' for day in dates) + '</div>'
        time_column = '<div class="hour-times-column">' + ''.join(
            f'<div class="hour_time_item">{text}</div>' for text in times) + '</div>'
        header = ('<span class="open-desc">오늘(수)</span><span class="today-main-hours">영업시간: 11:30 - 22:00</span>'
                  if today else '')
        return ('<div class="hour-main-grid">' + header
                + (time_column + date_column if times_first else date_column + time_column) + '</div>')

    def test_reader_and_candidate_filter_keep_saturday_open_and_monday_closed(self):
        # Reduced structure of the live page: dates in one column, one row of
        # hours/break/last-order (or closure) per date in the other column.
        dates = ["10월 8일(목)", "10월 9일(금)", "10월 10일(토)", "10월 11일(일)", "10월 12일(월)", "10월 13일(화)"]
        open_row = ('<span class="hour_time">영업시간: 11:30 - 22:00</span><br>'
                    '<span class="hour_time">브레이크타임: 14:00 - 16:00</span><br>'
                    '<span class="hour_time">라스트오더: 21:00</span>')
        html = ('<title>가상 식당 - 잠실 음식점</title><p>서울 송파구 올림픽로 10</p>'
                + self.grid(dates, [open_row] * 4 + ['<span class="hour_time closed">휴무일</span>', open_row], today=True)
                + '<h2>메뉴정보</h2><p>초밥</p>')
        def handle(request):
            return (httpx.Response(404) if request.url.path == "/robots.txt" else
                    httpx.Response(200, text=html, headers={"content-type": "text/html"}))
        with httpx.Client(transport=httpx.MockTransport(handle)) as client:
            page = PublicReader(client=client, dns_check=lambda _: True, gap=0).read(document("")["url"], ["초밥"], complete_text=True)
        self.assertTrue(page["body_read"])
        self.assertEqual(len(page["dated_hours"]), 7)
        self.assertIn("초밥", page["body_text"])
        with hours.session(), patch.object(hours, "datetime", wraps=datetime) as clock:
            clock.now.return_value = datetime(2026, 10, 7, 9, tzinfo=hours.KST)
            hours.observe(SHOP, page)
            self.assertEqual(hours.check(SHOP, "2026-10-07"), "")
            self.assertEqual(hours.check(SHOP, "2026-10-10"), "")
            self.assertEqual(hours.check(SHOP, "2026-10-10", {"time": "12:00", "stayMin": 50}), "")
            self.assertEqual(hours.check(SHOP, "2026-10-10", {"time": "14:30", "stayMin": 50}), "브레이크타임")
            self.assertEqual(hours.check(SHOP, "2026-10-10", {"time": "21:00", "stayMin": 20}), "주문 마감 이후")
            self.assertEqual(hours.check(SHOP, "2026-10-12"), "휴무일")
            self.assertEqual(hours.check(SHOP, "2026-10-13", {"time": "12:00", "stayMin": 50}), "")
            self.assertEqual(hours.check(SHOP, "2026-10-19"), "")  # No invented weekly closure.

    def test_each_date_keeps_its_own_hours_in_either_column_order(self):
        for times_first in (False, True):
            rows = extract_dated_hours(self.grid(["10월 10일(토)", "10월 11일(일)"],
                ["영업시간: 11:00 - 18:00", "영업시간: 17:00 - 23:00"], times_first=times_first))
            info = {"closed": False, "rules": [rule for row in rows for rule in hours.parse(row, date(2026, 10, 7))["rules"]]}
            self.assertEqual(hours._verdict(info, date(2026, 10, 10), 720, 50)[0], "")
            self.assertIn("영업시간 밖", hours._verdict(info, date(2026, 10, 11), 720, 50)[0])

    def test_incomplete_or_mismatched_columns_do_not_guess_a_date(self):
        for html in (self.grid(["10월 10일(토)", "10월 12일(월)"], ["휴무일"]),
                     self.grid(["10월 10일(토)"], ["영업시간: 11:00 - 22:00", "휴무일"]),
                     self.grid(["날짜 확인 필요"], ["휴무일"]),
                     self.grid(["10월 10일(토)", "10월 10일(토)"], ["휴무일", "휴무일"]),
                     self.grid(["10월 10일(토)"], ["휴무일"])[:-6]):
            self.assertEqual(extract_dated_hours(html), [])
            body = extract_text('<p>영업시간</p>' + html, skip_dated_grids=True)[0]
            self.assertNotIn("휴무", body)
            self.assertEqual(hours.parse(body)["rules"], [])

    def test_separate_grids_and_hidden_scripts_are_not_combined(self):
        html = self.grid(["10월 10일(토)"], []) + self.grid([], ["휴무일"])
        self.assertEqual(extract_dated_hours(html), [])
        for tag in ("script", "template", "noscript"):
            self.assertEqual(extract_dated_hours(f'<{tag}>' + self.grid(["10월 10일(토)"], ["휴무일"]) + f'</{tag}>'), [])
        rows = extract_dated_hours(self.grid(["10월 10일(토)"], [
            '<span>영업시간: 11:00 - 22:00</span><script>휴무일</script>']))
        self.assertEqual(len(rows), 1)
        self.assertNotIn("휴무", rows[0])

    def test_today_closure_is_limited_to_today(self):
        html = self.grid(["10월 8일(목)"], ["영업시간: 11:00 - 22:00"], today=True)
        html = html.replace('class="today-main-hours">영업시간: 11:30 - 22:00', 'class="today-main-hours">휴무일')
        info = {"closed": False, "rules": [rule for row in extract_dated_hours(html)
                                         for rule in hours.parse(row, date(2026, 10, 7))["rules"]]}
        self.assertEqual(hours._verdict(info, date(2026, 10, 7), None, 0)[0], "휴무일")
        self.assertEqual(hours._verdict(info, date(2026, 10, 8), None, 0)[0], "")
        self.assertEqual(hours._verdict(info, date(2026, 10, 14), None, 0)[0], "")

    def test_paired_dates_cross_the_year_without_becoming_weekly_rules(self):
        rows = extract_dated_hours(self.grid(["12월 31일(목)", "1월 1일(금)"],
                                  ["영업시간: 11:00 - 22:00", "휴무일"]))
        info = {"closed": False, "rules": [rule for row in rows for rule in hours.parse(row, date(2026, 12, 30))["rules"]]}
        self.assertEqual(hours._verdict(info, date(2026, 12, 31), None, 0)[0], "")
        self.assertEqual(hours._verdict(info, date(2027, 1, 1), None, 0)[0], "휴무일")
        self.assertEqual(hours._verdict(info, date(2027, 1, 8), None, 0)[0], "")


class RepairTests(SimpleTestCase):
    def test_closed_then_bad_hours_then_unknown_candidate_and_recalculate(self):
        original = {**SHOP, "key": "food", "phase": "BEFORE"}
        short = {**SHOP, "placeId": "short", "name": "짧은영업식당"}
        unknown = {**SHOP, "placeId": "unknown", "name": "정보없는식당"}
        stadium = {"key": "stadium", "placeId": "stadium", "category": "STADIUM", "name": "구장", "phase": "GAME"}
        calculate = Mock(side_effect=lambda rows: {"tl": {"rows": [{"time": "18:00", "stayMin": 50} for p in rows]}})
        def candidates(target, i, rows, rejected):
            return [short, unknown]
        with hours.session():
            hours.observe(SHOP, document("폐업"))
            hours.observe(short, document("영업시간\n매일 11:00 - 18:10", title="짧은영업식당"))
            result, _, changes = hours.repair([original, stadium], calculate, candidates, DAY)
        self.assertEqual([p["name"] for p in result], [unknown["name"], "구장"])
        self.assertEqual(calculate.call_count, 3)
        self.assertEqual(len(changes), 2)
        self.assertEqual(original["name"], SHOP["name"])

    def test_no_alternative_omits_closed_visit_and_keeps_other_stops(self):
        with hours.session():
            hours.observe(SHOP, document("폐업"))
            kept = {**SHOP, "name": "다른카페", "placeId": "other", "category": "CAFE"}
            result, _, changes = hours.repair([SHOP, kept], lambda rows: {"tl": {"rows": [{} for p in rows]}}, lambda *a: [], DAY)
        self.assertEqual(result, [kept])
        self.assertIn("대체 후보를 찾지 못해", changes[0])

    def test_unknown_does_not_search_or_remove_and_completed_is_preserved(self):
        search = Mock()
        calculate = lambda rows: {"tl": {"rows": [{} for p in rows]}}
        with hours.session():
            self.assertEqual(hours.repair([SHOP], calculate, search, DAY)[0], [SHOP])
            hours.observe(SHOP, document("폐업"))
            done = {**SHOP, "completed": True}
            self.assertEqual(hours.repair([done], calculate, search, DAY)[0], [done])
        search.assert_not_called()

    def test_grounding_collects_hours_even_when_requested_menu_is_not_found(self):
        from .test_course_grounding import claim
        f = claim(place_id=SHOP["placeId"])
        with evidence_memory.request_budget():
            budget = evidence_memory._BUDGET.get()
            budget["deadline"] = grounding.time.monotonic() + 30
            with patch.object(grounding, "PublicReader") as reader:
                reader.return_value.read.return_value = document("폐업\n메뉴정보\n우동")
                self.assertEqual(grounding.verify([f], {f.url}, budget), [])
            self.assertEqual(hours.check(SHOP, DAY), "폐업")

    def test_lodging_reference_page_is_checked_without_extra_page_or_model_calls(self):
        from llm.v1.rag.nearby import lodging
        hotel = {"id": "hotel", "name": "가상호텔", "address": SHOP["address"]}
        url = "https://nol.yanolja.com/stay/domestic/fixture"
        page = document("폐업", title="가상호텔 호텔/리조트 예약 | NOL", url=url)
        model = Mock()
        model.with_structured_output.return_value.invoke.return_value = {"requirements": [], "properties": []}
        with hours.session(), patch("travel.public_page_reader.PublicReader") as reader, patch.object(agent, "llm", return_value=model):
            reader.return_value.read.return_value = page
            lodging._read_details([hotel], [url], [], {"deadline": grounding.time.monotonic() + 90})
            self.assertEqual(hours.check({**hotel, "placeId": "hotel"}, DAY), "폐업")
        reader.return_value.read.assert_called_once()
        model.with_structured_output.return_value.invoke.assert_called_once()

    def test_menu_evidence_on_closed_first_batch_continues_to_next_candidate(self):
        req = evidence_memory.Requirement(term="돈까스", attribute="menu", intent="required", group="food")
        candidates = [{**SHOP, "placeId": str(i), "name": f"가상식당{i}"} for i in range(5)]
        def search(active, requested):
            for p in active:
                if p["placeId"] != "4":
                    hours.observe(p, document("폐업", title=p["name"]))
            return [], set(), 1
        rows = [{"attribute": "menu", "term": "돈까스", "polarity": "positive", "source": {"url": "https://example.com/menu"}}]
        with self.settings(COURSE_WEB_VERIFICATION_ENABLED=True), evidence_memory.request_budget(), \
                patch.object(evidence_memory, "requirements", return_value=[req]), \
                patch.object(evidence_memory, "read", return_value=[]), \
                patch.object(evidence_memory, "ensure_place", return_value=object()), \
                patch.object(evidence_memory, "cooling_terms", return_value=set()), \
                patch.object(evidence_memory, "store", return_value=0), \
                patch.object(evidence_memory, "checked_rows", return_value=rows), \
                patch.object(evidence_memory.PlaceEnrichmentAttempt.objects, "create"), \
                patch.object(evidence_memory, "search", side_effect=search) as lookup:
            result = evidence_memory.enrich(candidates, ["돈까스"])
        self.assertEqual([p["placeId"] for p in result], ["4"])
        self.assertEqual(lookup.call_count, 2)


class AvailabilityPipelineTests(SimpleTestCase):
    def test_requested_edit_keeps_its_visit_when_closed_first_choice_is_replaced(self):
        self.check_requested_replacement(has_alternative=True)

    def test_failed_requested_replacement_never_turns_into_unrequested_deletion(self):
        self.check_requested_replacement(has_alternative=False)

    def check_requested_replacement(self, has_alternative):
        from llm.v1.rag.course.progress import ProgressError
        near, far, cafe, park, stadium, game = self.fixture()
        places = [{**p, 'category': 'FOOD' if p['category'] == 'FOOD_OUT' else p['category'], 'visitId': str(i)}
                  for i, p in enumerate((near, cafe, stadium, park))]
        current = {'places': deepcopy(places), 'travelMode': 'walk', 'legModes': {}, 'game': game, 'stadiumCode': 'JAMSIL'}
        selected_plan = {'query':'초밥', 'conditions':['초밥'], 'closer_to_stadium':True, '_distance_limit':2000}
        with hours.session(), patch.object(agent, 'load_schedule', return_value=({},1)), \
                patch.object(agent, 'find_game', return_value=(game,False,[game])), \
                patch.object(agent, 'stadium_anchor', return_value=stadium), \
                patch.object(agent, 'invoke_domain_tool', provider()), \
                patch.object(editing, 'candidates', return_value=[{**far,'category':'FOOD'}] if has_alternative else []) as search, \
                patch.object(editing, 'choose', side_effect=lambda options,*a:options):
            hours.observe(near, document('영업시간\n매일 11:00 - 12:00', title=near['name']))
            if not has_alternative:
                with self.assertRaisesRegex(ProgressError, '요청한 방문을 삭제하지 않고'):
                    editing.rebuild(deepcopy(places), current, 'JAMSIL', ORIGIN, '더 가까운 곳', [], availability_plans={'0': selected_plan})
            else:
                result = editing.rebuild(deepcopy(places), current, 'JAMSIL', ORIGIN, '더 가까운 곳', [], availability_plans={'0': selected_plan})
                self.assertEqual(result['places'][0]['name'], far['name'])
                self.assertEqual([p['visitId'] for p in result['places']], ['0','1','2','3'])
                self.assertEqual(search.call_args.args[2]['_distance_limit'], 2000)
        self.assertEqual(current['places'], places)

    def fixture(self):
        def complete(p):
            return {"placeUrl": "https://example.com/place", "address": SHOP["address"], "distance": 500,
                    "doc_id": "", **p}
        near, far, cafe, park = [complete(p) for p in (NEAR, FAR, CAFE, PARK)]
        stadium = complete({**ANCHOR, "name": "잠실야구장", "key": "STADIUM", "placeId": None, "category": "STADIUM"})
        game = {"date": DAY, "time": "18:30", "home": "LG", "away": "삼성", "status": "scheduled"}
        return near, far, cafe, park, stadium, game

    def test_new_course_replaces_closed_meal_preserves_cafe_park_origin_and_payload(self):
        near, far, cafe, park, stadium, game = self.fixture()
        invoke = provider()
        build = agent.build_origin_course
        def planned(*a, **kw):
            result = build(*a, **kw)
            # Simulate an observed closure after initial selection, before the
            # final timetable repair. Description generation is no longer a step.
            hours.observe(near, document("폐업", title=near["name"]))
            return result
        mocks = {"load_schedule": ({}, 1), "find_game": (game, False, [game]), "embed_many": ([.1], [.2]),
                 "stadium_anchor": stadium, "_live_candidates": ([near, far, cafe, park], {}), "search_places": []}
        with ExitStack() as stack:
            # These fictional shops exercise closure recovery. Review eligibility
            # is covered separately using the real reviewed catalogue.
            stack.enter_context(patch.object(agent.place_quality, "filter_place", side_effect=lambda place, category: place))
            for name, value in mocks.items():
                stack.enter_context(patch.object(agent, name, return_value=value))
            stack.enter_context(patch.object(agent, "build_origin_course", side_effect=planned))
            describe = stack.enter_context(patch.object(agent, "call_llm"))
            stack.enter_context(patch.object(agent, "invoke_domain_tool", invoke))
            stack.enter_context(patch.object(agent.kakao, "nearby", return_value=[]))
            stack.enter_context(patch.object(agent, "_kakao_step", side_effect=lambda kind, *a, **kw: {
                "FOOD": [near, far], "CAFE": [cafe], "WALK": [park]}[kind]))
            result = agent.answer("경기 전 식사하고 카페 갔다가 경기 후 산책만", hint_stadium="JAMSIL", origin=ORIGIN)
        describe.assert_not_called()
        names = [far["name"], cafe["name"], stadium["name"], park["name"]]
        self.assertEqual([p["name"] for p in result["places"]], names)
        self.assertEqual([p["name"] for p in result["coursePayload"]["stops"]], names)
        self.assertEqual(result["origin"]["lat"], ORIGIN["lat"])
        self.assertIn("폐업", result["answer"])
        self.assertNotIn(near["name"], names)
        self.assertTrue(all(p.get("time") for p in result["places"]))

    def test_edit_rebuild_rechecks_closing_time_and_preserves_other_visit_ids(self):
        near, far, cafe, park, stadium, game = self.fixture()
        places = [{**p, "category": "FOOD" if p["category"] == "FOOD_OUT" else p["category"], "visitId": str(i)}
                  for i, p in enumerate((near, cafe, stadium, park))]
        current = {"places": deepcopy(places), "travelMode": "walk", "legModes": {}, "game": game, "stadiumCode": "JAMSIL"}
        with hours.session(), patch.object(agent, "load_schedule", return_value=({}, 1)), \
                patch.object(agent, "find_game", return_value=(game, False, [game])), \
                patch.object(agent, "stadium_anchor", return_value=stadium), \
                patch.object(agent, "invoke_domain_tool", provider()), \
                patch.object(editing, "candidates", return_value=[{**far, "category": "FOOD"}]) as search, \
                patch.object(editing, "choose", side_effect=lambda options, *a: options):
            hours.observe(near, document("영업시간\n매일 11:00 - 12:00", title=near["name"]))
            result = editing.rebuild(places, current, "JAMSIL", ORIGIN, "순서 바꿔줘", [])
        self.assertEqual([p["name"] for p in result["places"]], [far["name"], cafe["name"], stadium["name"], park["name"]])
        self.assertEqual([p["visitId"] for p in result["places"][1:]], ["1", "2", "3"])
        self.assertEqual(result["origin"], ORIGIN)
        search.assert_called_once()
        self.assertIn("영업시간 밖", result["answer"])
