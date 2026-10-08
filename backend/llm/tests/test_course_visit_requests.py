import json
from contextlib import ExitStack
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from llm.tests.test_course_editing import plan
from llm.v1.rag.course import agent, editing, evidence_memory, memory, slots, venue_policy, visit_requests
from travel.stadium_food import food_candidates, matching_menu_items


QUESTION = "국밥 먹고 구장 내부에서 츄러스 먹은 다음에 경기 끝나고 술집에서 술 마시고 싶어. 코스 짜줘"


def visit(kind, expression, query="", phase="BEFORE"):
    return {"kind": kind, "phase": phase, "expression": expression, "query": query, "conditions": []}


def requirements(conditions):
    text = " ".join(conditions)
    return [evidence_memory.Requirement(term=term, attribute="menu", intent="required", group=term)
            for term in ("국밥", "츄러스", "치킨", "망고빙수", "아츄") if term in text]


class VisitConditionsTests(SimpleTestCase):
    def setUp(self):
        self.sources = food_candidates("GWANGJU")
        self.churros = next(p for p in self.sources if p["name"] == "스트릿츄러스")
        self.chicken = next(p for p in self.sources if p["name"] == "BHC치킨")
        self.outside = {"name": "밖국밥집", "category": "FOOD_OUT", "detail": "음식점 > 한식 > 국밥",
                        "lat": 35.166, "lng": 126.902, "placeId": "soup", "placeUrl": "", "address": "광주",
                        "dist": .1, "distance": 1100, "doc_id": "soup"}

    def test_dessert_classification_and_internal_permission_belong_to_one_visit(self):
        visits = [visit("FOOD", "국밥 먹고", "국밥"), visit("FOOD", "구장 내부에서 츄러스 먹은", "츄러스"),
                  visit("BAR", "경기 끝나고 술집에서 술 마시고 싶어", "술집", "AFTER")]
        normalized = visit_requests.normalize(QUESTION, visits)
        self.assertEqual([v["kind"] for v in normalized], ["FOOD", "CAFE", "BAR"])
        self.assertEqual([bool(v["internal"]) for v in normalized], [False, True, False])
        with venue_policy.request_policy(QUESTION, "GWANGJU", visits=visits):
            self.assertEqual(len(venue_policy.filter_candidates([self.churros, self.outside])), 2)
            selector = visit_requests.Selector(normalized, memory.empty(), "GWANGJU")
            with selector.policy(normalized[0]):
                self.assertEqual(venue_policy.filter_candidates([self.churros, self.outside]), [self.outside])
            with selector.policy(normalized[1]):
                invoke = Mock()
                rows = venue_policy.search_candidates(invoke, {}, "CAFE")
                self.assertTrue(rows)
                self.assertTrue(all(p["source"] == "MYSEATCHECK" for p in rows))
                invoke.assert_not_called()
        self.assertEqual(venue_policy.filter_candidates([self.churros]), [])

    def test_same_food_category_can_have_outside_soup_and_inside_chicken(self):
        question = "국밥 먹고 구장 안에서 치킨 먹자"
        visits = [visit("FOOD", "국밥 먹고", "국밥"), visit("FOOD", "구장 안에서 치킨 먹자", "치킨")]
        normalized = visit_requests.normalize(question, visits)
        selector = visit_requests.Selector(normalized, memory.empty(), "GWANGJU")
        inside = {**self.chicken, "category": "FOOD_OUT"}
        with venue_policy.request_policy(question, "GWANGJU", visits=visits), \
                patch.object(evidence_memory, "requirements", side_effect=requirements), \
                patch.object(editing, "choose", side_effect=lambda items, *_: items):
            soup = selector.for_visit([inside, self.outside], normalized[0])
            chicken = selector.for_visit([inside, self.outside], normalized[1])
        self.assertEqual([p["name"] for p in soup], ["밖국밥집"])
        self.assertEqual([p["name"] for p in chicken], ["BHC치킨"])
        self.assertFalse(visit_requests.matches(soup[0], normalized[1]))
        self.assertFalse(visit_requests.matches(chicken[0], normalized[0]))

    def test_specific_menu_search_precedes_generic_cafe_without_reordering_visits(self):
        question = "카페 갔다가 구장 내부 간식 먹고 경기 이후 자장면 먹고 싶어"
        normalized = visit_requests.normalize(question, [visit("CAFE", "카페 갔다가", "카페"),
            visit("FOOD", "구장 내부 간식 먹고"), visit("FOOD", "경기 이후 자장면 먹고 싶어", "자장면", "AFTER")])
        selector = visit_requests.Selector(normalized, memory.empty(), "GWANGJU")
        with patch.object(editing, "candidates", return_value=[]) as search:
            selector.search(self.outside)
        self.assertEqual([c.args[2]["query"] for c in search.call_args_list], ["자장면", "카페", ""])
        self.assertIn("경기 이후 자장면 먹고 싶어", search.call_args_list[0].args[2]["conditions"])
        self.assertEqual([v["id"] for v in selector.visits], ["BEFORE:0", "BEFORE:1", "AFTER:2"])

    def test_oct16_cafe_internal_snack_and_after_game_jajang_reach_every_output(self):
        question = "16일 경기, 카페 갔다가 구장 내부 간식 먹고 경기 이후 자장면 먹고 싶어. 코스 짜줘"
        visits = [visit("CAFE", "카페 갔다가", "카페"), visit("FOOD", "구장 내부 간식 먹고"),
                  visit("FOOD", "경기 이후 자장면 먹고 싶어", "자장면", "AFTER")]
        cafe = {**self.outside, "name": "테스트카페", "category": "CAFE", "placeId": "cafe", "detail": "음식점 > 카페"}
        chinese = {**self.outside, "name": "테스트중국집", "placeId": "jajang", "detail": "음식점 > 중식 > 중국요리"}
        stadium = {**self.outside, "name": "광주-KIA 챔피언스 필드", "category": "STADIUM", "key": "STADIUM",
                   "lat": 35.16811385, "lng": 126.88943548}
        events = []

        def invoke(domain, tool, args):
            if tool != "search_places":
                return {}
            query = args.get("query")
            events.append(query)
            self.assertNotIn(query, ("간식", "크림새우"), "Internal snack must not use Kakao")
            row = chinese if query in ("자장면", "짜장면", "짜장", "자장") else cafe
            return {"places": [{**row, "id": row["placeId"], "place_name": row["name"], "x": row["lng"], "y": row["lat"],
                    "category_name": row["detail"], "category_group_code": "FD6" if row is chinese else "CE7"}]}

        def enrich(items, conditions):
            return [p for p in items if not any("자장면" in c for c in conditions) or p["placeId"] == "jajang"]

        def choice(messages, **kwargs):
            items = json.loads(messages[-1].content)["candidates"]
            return editing.CandidateChoice(place_id=items[0]["placeId"], evidence="검증된 후보", matching_ids=[p["placeId"] for p in items])

        def broad(*args, **kwargs):
            events.append("broad")
            return [cafe], {}

        game = {"date": "2026-10-16", "time": "14:00", "home": "KIA", "away": "NC", "status": "scheduled"}
        with ExitStack() as stack:
            stack.enter_context(patch.object(editing, "interpret", return_value=plan("new", [], requested_visits=visits)))
            stack.enter_context(patch.object(agent.place_quality, "active", return_value=False))
            for name, result in {"load_schedule": ({}, 7), "find_game": (game, False, [game]), "embed_many": ([.1], [.2]),
                                 "search_places": [], "stadium_anchor": stadium, "call_llm": ('{"course": []}', 0)}.items():
                stack.enter_context(patch.object(agent, name, return_value=result))
            stack.enter_context(patch.object(agent, "_live_candidates", side_effect=broad))
            stack.enter_context(patch.object(agent, "invoke_domain_tool", side_effect=invoke))
            stack.enter_context(patch.object(agent.transport, "info", return_value={"mode": "walk", "label": "도보", "lines": [], "taxi": False}))
            stack.enter_context(patch.object(evidence_memory, "requirements", return_value=[]))
            stack.enter_context(patch.object(evidence_memory, "enrich", side_effect=enrich))
            model = stack.enter_context(patch.object(agent, "llm"))
            model.return_value.with_structured_output.return_value.invoke.side_effect = choice
            result = agent.answer(question, hint_stadium="GWANGJU", course_memory=memory.empty(), course_request="NEW")
        self.assertEqual(events[0], "자장면")
        self.assertLess(events.index("자장면"), events.index("broad"))
        self.assertEqual([p["phase"] for p in result["places"]], ["BEFORE", "BEFORE", "GAME", "AFTER"])
        self.assertEqual([p["name"] for p in result["places"]][::3], [cafe["name"], chinese["name"]])
        self.assertEqual(result["places"][1]["time"], "13:30")
        self.assertEqual(result["places"][2]["time"], "14:00")
        self.assertEqual(result["game"], {"date": "2026-10-16", "time": "14:00"})
        for text in (result["answer"], result["coursePayload"]["content"]):
            self.assertIn(chinese["name"], text)
            self.assertNotIn("담지 못했어요", text)
        names = [p["name"] for p in result["places"]]
        self.assertEqual([p["name"] for p in result["coursePayload"]["stops"]], names)
        self.assertEqual([p["name"] for p in result["courseMemory"]["current"]["places"]], names)

    def test_collected_name_matches_without_inventing_menu_evidence_or_web_calls(self):
        with patch.object(evidence_memory, "requirements", side_effect=requirements), \
                patch.object(evidence_memory, "search") as search, \
                patch.object(evidence_memory, "read") as read, \
                patch.object(evidence_memory, "store") as store:
            result = evidence_memory.collected_candidates([self.churros], ["츄러스 먹고"])
            self.assertEqual(result[0]["placeUrl"], self.churros["placeUrl"])
            self.assertNotIn("verifiedFacts", result[0])
            self.assertEqual(evidence_memory.collected_candidates([self.churros], ["망고빙수"]), [])
        search.assert_not_called()
        read.assert_not_called()
        store.assert_not_called()

    def test_reviewed_menu_is_store_specific_and_unknown_items_stay_unknown(self):
        reference = next(p for p in self.sources if "SC_FOOD_GWANGJU_006:" in p["placeId"])
        menu = {"facilityId": "SC_FOOD_GWANGJU_006", "stadium": "GWANGJU", "store": reference["name"],
                "sourceUrl": reference["placeUrl"], "checkedAt": "2026-10-07", "items": [{"name": "아츄", "priceWon": 6200}]}
        # Catalogue growth must not change this branch-isolation regression case.
        with patch("travel.stadium_food.menu_catalogue", return_value={menu["facilityId"]: menu}):
            sources = food_candidates("GWANGJU")
            first = next(p for p in sources if "SC_FOOD_GWANGJU_006:" in p["placeId"])
            other = next(p for p in sources if "SC_FOOD_GWANGJU_019:" in p["placeId"])
            source_patch = patch("travel.stadium_food.food_candidates", return_value=sources)
            source_patch.start()
            self.addCleanup(source_patch.stop)
        self.assertEqual(first["menuEvidence"]["items"][0], {"name": "아츄", "priceWon": 6200})
        self.assertNotIn("menuEvidence", other)
        with patch.object(evidence_memory, "requirements", side_effect=requirements):
            self.assertEqual([p["placeId"] for p in evidence_memory.collected_candidates([first, other], ["아츄"])], [first["placeId"]])
            self.assertEqual(evidence_memory.collected_candidates([first], ["망고빙수"]), [])
        question = "구장 내부 카페에서 아츄 먹고"
        requested = visit_requests.normalize(question, [visit("CAFE", question)])[0]
        selector = visit_requests.Selector([requested], memory.empty(), "GWANGJU")
        with patch.object(evidence_memory, "requirements", side_effect=requirements):
            self.assertEqual([p["placeId"] for p in selector.for_visit([first, other], requested)], [first["placeId"]])

    def test_final_itinerary_guard_does_not_swap_or_fill_with_another_visits_food(self):
        question = "국밥 먹고 구장 안에서 치킨 먹자"
        visits = [visit("FOOD", "국밥 먹고", "국밥"), visit("FOOD", "구장 안에서 치킨 먹자", "치킨")]
        sl = slots.apply_requested_visits(slots.parse(question), visits, question)
        first, second = sl["requested_visits"]
        soup = {**self.outside, "key": "soup", "_requested_visit_ids": [first["id"]]}
        chicken = {**self.chicken, "key": "chicken", "category": "FOOD_OUT", "_requested_visit_ids": [second["id"]]}
        lookup = {"soup": soup, "chicken": chicken, "STADIUM": {"category": "STADIUM", "name": "구장"}}
        wrong = [{"key": "chicken", "phase": "BEFORE"}, {"key": "soup", "phase": "BEFORE"}]
        course, missing = agent.enforce_itinerary(wrong, lookup, [chicken, soup], sl, True)
        self.assertEqual([s["key"] for s in course], ["soup", "chicken", "STADIUM"])
        self.assertEqual(missing, [])
        course, missing = agent.enforce_itinerary(wrong[:1], lookup, [chicken], sl, True)
        self.assertEqual([s["key"] for s in course], ["chicken", "STADIUM"])
        self.assertEqual(missing, ["경기 전 1번째 식사"])

    def test_availability_replacement_keeps_internal_scope_and_the_same_menu(self):
        question = "구장 안에서 츄러스 먹자"
        sl = slots.apply_requested_visits(slots.parse(question), [visit("CAFE", question, "츄러스")], question)
        requested = sl["requested_visits"][0]
        selector = visit_requests.Selector(sl["requested_visits"], memory.empty(), "GWANGJU")
        target = {**self.churros, "key": "dessert", "phase": "BEFORE", "_request_visit_id": requested["id"]}
        other = next(p for p in self.sources if p["name"] == "스트릿츄러스" and p["placeId"] != self.churros["placeId"])
        other = {**other, "dist": .1}
        outside = {**self.outside, "category": "CAFE", "name": "외부츄러스"}
        def search(*args):
            self.assertEqual(venue_policy._PERMISSION.get(), ("GWANGJU", frozenset({"CAFE"})))
            return [outside, other]
        with patch.object(agent, "_kakao_step", side_effect=search), \
                patch.object(evidence_memory, "requirements", side_effect=requirements), \
                patch.object(agent.availability, "check", return_value=""):
            result = agent.availability_alternatives(target, 0, [target], [target], origin=self.outside,
                anchor={"lat": 35.1681, "lng": 126.8894}, pool=[], sl=sl, candidate_filter=selector, visit_date="2027-10-06")
        self.assertEqual([p["placeId"] for p in result], [other["placeId"]])
        self.assertEqual(result[0]["_request_visit_id"], requested["id"])
        self.assertEqual(venue_policy.filter_candidates([other]), [])

    def test_failed_request_is_generated_in_order_through_answer_map_and_save(self):
        visits = [visit("FOOD", "국밥 먹고", "국밥"), visit("FOOD", "구장 내부에서 츄러스 먹은", "츄러스"),
                  visit("BAR", "경기 끝나고 술집에서 술 마시고 싶어", "술집", "AFTER")]
        bar = {**self.outside, "name": "밖술집", "placeId": "bar", "detail": "음식점 > 술집", "lat": 35.17, "lng": 126.895}
        source = {**self.churros, "dist": .1, "distance": 60, "doc_id": "source"}
        def invoke(domain, tool, args):
            if tool != "search_places":
                return {}
            rows = [bar] if args.get("query") == "술집" else [self.outside] if args.get("query") == "국밥" else [self.outside, bar]
            return {"places": [{**p, "id": p["placeId"], "place_name": p["name"], "x": p["lng"], "y": p["lat"],
                                "category_name": p["detail"], "category_group_code": "FD6"} for p in rows]}
        checked = []
        def enrich(items, conditions):
            checked.append(conditions)
            self.assertFalse(any("츄러스" in c for c in conditions), "Internal dessert conditions leaked into outside shops")
            return [p for p in items if not any("국밥" in c for c in conditions) or "국밥" in p["name"]]
        def choice(messages, **kwargs):
            items = json.loads(messages[-1].content)["candidates"]
            return editing.CandidateChoice(place_id=items[0]["placeId"], evidence="검증된 후보", matching_ids=[p["placeId"] for p in items])
        game = {"date": "2027-10-06", "time": "18:30", "home": "KIA", "away": "삼성", "status": "scheduled"}
        stadium = {**self.outside, "name": "광주-KIA 챔피언스 필드", "category": "STADIUM", "key": "STADIUM",
                   "lat": 35.16811385, "lng": 126.88943548}
        with ExitStack() as stack:
            # This regression uses synthetic shops to isolate visit order/menu
            # scoping. Review quality has separate tests with reviewed branches.
            stack.enter_context(patch.object(agent.place_quality, "active", return_value=False))
            stack.enter_context(patch.object(editing, "interpret", return_value=plan("new", [], requested_visits=visits,
                internal_venue_requests=[{"category": "FOOD", "expression": "구장 내부에서 츄러스 먹은"}])))
            for name, result in {"load_schedule": ({}, 7), "find_game": (game, False, [game]), "embed_many": ([.1], [.2]),
                                 "_live_candidates": ([self.outside, source, bar], {}), "search_places": [], "stadium_anchor": stadium,
                                 "call_llm": ('{"course": []}', 0)}.items():
                stack.enter_context(patch.object(agent, name, return_value=result))
            stack.enter_context(patch.object(agent, "invoke_domain_tool", side_effect=invoke))
            stack.enter_context(patch.object(agent.transport, "info", return_value={"mode": "walk", "label": "도보", "lines": [], "taxi": False}))
            stack.enter_context(patch.object(evidence_memory, "requirements", side_effect=requirements))
            stack.enter_context(patch.object(evidence_memory, "enrich", side_effect=enrich))
            model = stack.enter_context(patch.object(agent, "llm"))
            model.return_value.with_structured_output.return_value.invoke.side_effect = choice
            result = agent.answer(QUESTION, hint_stadium="GWANGJU", course_memory=memory.empty(), course_request="NEW")
        self.assertEqual([p["name"] for p in result["places"]], ["밖국밥집", "스트릿츄러스", "광주-KIA 챔피언스 필드", "밖술집"])
        self.assertEqual([p["phase"] for p in result["places"]], ["BEFORE", "BEFORE", "GAME", "AFTER"])
        self.assertEqual(result["origin"]["name"], "광주역")
        self.assertIn(self.churros["placeUrl"], result["answer"])
        self.assertEqual([p["name"] for p in result["coursePayload"]["stops"]], [p["name"] for p in result["places"]])
        self.assertEqual(len(result["courseMemory"]["current"]["places"]), 4)
        self.assertTrue(checked)


class DaejeonEnglishMenuCourseTests(SimpleTestCase):
    def test_outside_soup_inside_english_churros_and_outside_bar_reach_map_and_save(self):
        from baseball.stadium_locations import reviewed_venue

        point = reviewed_venue("DAEJEON")
        stadium = {"name": "대전 한화생명 볼파크", "category": "STADIUM", "key": "STADIUM",
                   "lat": point["lat"], "lng": point["lng"], "detail": "", "address": "대전",
                   "placeId": None, "placeUrl": "", "distance": 0, "doc_id": None}
        soup = {"name": "밖국밥집", "category": "FOOD_OUT", "detail": "음식점 > 한식 > 국밥",
                "lat": point["lat"] + .006, "lng": point["lng"] + .006, "placeId": "soup",
                "placeUrl": "", "address": "대전", "dist": .1, "distance": 900, "doc_id": "soup"}
        bar = {**soup, "name": "밖술집", "placeId": "bar", "doc_id": "bar", "detail": "음식점 > 술집",
               "lat": point["lat"] + .007}
        visits = [visit("FOOD", "국밥 먹고", "국밥"), visit("CAFE", "구장 내부에서 츄러스 먹은", "츄러스"),
                  visit("BAR", "경기 끝나고 술집에서 술 마시고 싶어", "술집", "AFTER")]

        def invoke(domain, tool, args):
            if tool != "search_places":
                return {}
            self.assertNotIn(args.get("query"), ("츄러스", "churros"), "Internal menu search called Kakao")
            rows = [bar] if args.get("query") == "술집" else [soup] if args.get("query") == "국밥" else [soup, bar]
            return {"places": [{**p, "id": p["placeId"], "place_name": p["name"], "x": p["lng"], "y": p["lat"],
                                "category_name": p["detail"], "category_group_code": "FD6"} for p in rows]}

        def enrich(items, conditions):
            self.assertFalse(any("츄러스" in c for c in conditions))
            return [p for p in items if not any("국밥" in c for c in conditions) or "국밥" in p["name"]]

        def choice(messages, **kwargs):
            items = json.loads(messages[-1].content)["candidates"]
            return editing.CandidateChoice(place_id=items[0]["placeId"], evidence="검증된 후보", matching_ids=[p["placeId"] for p in items])

        game = {"date": "2027-10-06", "time": "18:30", "home": "한화", "away": "NC", "status": "scheduled"}
        with ExitStack() as stack:
            stack.enter_context(patch.object(editing, "interpret", return_value=plan("new", [], requested_visits=visits,
                internal_venue_requests=[{"category": "CAFE", "expression": "구장 내부에서 츄러스 먹은"}])))
            # 내부 매장을 초기 후보에 주입하지 않고 실제 저장 메뉴 검색으로 발견하게 한다.
            for name, result in {"load_schedule": ({}, 7), "find_game": (game, False, [game]), "embed_many": ([.1], [.2]),
                                 "_live_candidates": ([soup, bar], {}), "search_places": [], "stadium_anchor": stadium,
                                 "call_llm": ('{"course": []}', 0)}.items():
                stack.enter_context(patch.object(agent, name, return_value=result))
            stack.enter_context(patch.object(agent, "invoke_domain_tool", side_effect=invoke))
            stack.enter_context(patch.object(agent.transport, "info", return_value={"mode": "walk", "label": "도보", "lines": [], "taxi": False}))
            stack.enter_context(patch.object(evidence_memory, "requirements", side_effect=requirements))
            stack.enter_context(patch.object(evidence_memory, "enrich", side_effect=enrich))
            model = stack.enter_context(patch.object(agent, "llm"))
            model.return_value.with_structured_output.return_value.invoke.side_effect = choice
            result = agent.answer(QUESTION, hint_stadium="DAEJEON", course_memory=memory.empty(), course_request="NEW")
        names = [p["name"] for p in result["places"]]
        self.assertEqual(len(names), 4)
        self.assertEqual([names[0], *names[2:]], ["밖국밥집", "대전 한화생명 볼파크", "밖술집"])
        source = next(p for p in food_candidates("DAEJEON") if p["placeId"] == result["places"][1]["placeId"])
        matched = matching_menu_items(source, "츄러스")
        self.assertTrue(matched)
        self.assertEqual([p["phase"] for p in result["places"]], ["BEFORE", "BEFORE", "GAME", "AFTER"])
        self.assertEqual(result["origin"]["name"], "대전역")
        self.assertIn(source["placeUrl"], result["answer"])
        self.assertTrue(any(o["imageUrl"] in result["answer"] for item in matched for o in item["observations"]))
        self.assertIn("메뉴판 사진", result["answer"])
        self.assertEqual([p["name"] for p in result["coursePayload"]["stops"]], [p["name"] for p in result["places"]])
        self.assertEqual(venue_policy.filter_candidates([source]), [])
