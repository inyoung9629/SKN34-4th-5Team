from contextlib import ExitStack
from copy import deepcopy
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from baseball.stadium_locations import reviewed_venue
from llm.tests.test_course_editing import CURRENT, GAME, plan
from llm.v1.rag.course import agent, editing, evidence_memory, memory, venue_policy, visit_requests
from travel.stadium_food import food_candidates
from travel.stadium_signatures import SIGNATURES, details, menu_label, reference_details


QUESTION = "구장에서 먹고 싶어"


def visit(expression=QUESTION, **kwargs):
    return {"kind": "FOOD", "phase": "BEFORE", "expression": expression,
            "query": "", "conditions": [], "signature_default": True, **kwargs}


class SignatureFoodTests(SimpleTestCase):
    def test_all_nine_defaults_have_store_specific_collected_menu_evidence(self):
        self.assertEqual(len(SIGNATURES), 9)
        for code, signature in SIGNATURES.items():
            with self.subTest(code=code):
                point = reviewed_venue(code)
                invoke = Mock()
                with venue_policy.request_policy(QUESTION, code, visits=[visit()]):
                    rows = venue_policy.search_candidates(invoke, {
                        "latitude": point["lat"], "longitude": point["lng"], "radius": 2500}, "FOOD")
                    self.assertTrue(rows)
                    for row in rows:
                        self.assertEqual(row["source"], "MYSEATCHECK")
                        self.assertEqual(row["_signature_menu"], menu_label(signature))
                        self.assertTrue(row["placeUrl"].startswith("https://myseatcheck.com/"))
                        self.assertTrue(row.get("menuEvidence"))
                    checked = venue_policy.filter_candidates(rows, "FOOD")
                    self.assertEqual(len(checked), len(rows))
                    invoke.assert_not_called()

    def test_combination_requires_both_dishes_at_the_same_branch(self):
        branches = [p for p in food_candidates("JAMSIL") if p["name"] == "통밥"]
        self.assertEqual(len(branches), 2)
        self.assertEqual([p["placeId"].split(":")[1] for p in branches if details(p)], ["SC_FOOD_JAMSIL_036"])
        altered = deepcopy(next(p for p in branches if details(p)))
        altered["menuEvidence"]["items"] = [{"name": "삼겹살정식"}]
        self.assertIsNone(details(altered))

    def test_daegu_keeps_the_actual_store_name_instead_of_renaming_it_hanmandu(self):
        rows = [p for p in food_candidates("DAEGU") if details(p)]
        self.assertTrue(rows)
        self.assertTrue(all(p["name"] == "북촌손만두" for p in rows))
        self.assertTrue(all(details(p)["_signature_menu"] == "짬뽕만두" for p in rows))
        self.assertEqual(SIGNATURES["DAEGU"]["store"], "한만두")

    def test_generic_wording_enables_intent_parsing_without_enabling_nearby_or_departure(self):
        for question in (QUESTION, "야구장에서 뭐 먹을까", "구장 식사 추천", "구장 대표 먹거리 추천"):
            self.assertTrue(venue_policy.mentions_internal(question), question)
        for question in ("구장 근처에서 먹자", "구장에서 나와서 먹자", "구장에서 출발해서 밥 먹자"):
            self.assertFalse(venue_policy.mentions_internal(question), question)

    def test_explicit_menu_conditions_and_cafe_requests_disable_default(self):
        for specified in (visit("구장 안 치킨 먹자", query="치킨"),
                          visit("구장 안 치킨 먹자", conditions=["치킨"]),
                          visit("구장 내부 카페", kind="CAFE")):
            normalized = visit_requests.normalize(specified["expression"], [specified])[0]
            self.assertFalse(normalized["signature_default"])
            with venue_policy.request_policy(specified["expression"], "GWANGJU", visits=[specified]):
                rows = venue_policy.search_candidates(Mock(), {}, specified["kind"])
                self.assertTrue(rows)
                self.assertTrue(all(not p.get("_signature_menu") for p in rows))

    def test_forged_or_stale_signature_does_not_enable_an_unrequested_menu(self):
        question = "구장 내부에서 치킨 먹자"
        dessert = next(p for p in food_candidates("CHANGWON") if details(p))
        forged = {**dessert, "category": "FOOD", **details(dessert)}
        with venue_policy.request_policy(question, "CHANGWON", visits=[visit(question, query="치킨", signature_default=False)]):
            self.assertEqual(venue_policy.filter_candidates([forged]), [])
        with venue_policy.request_policy(QUESTION, "CHANGWON", visits=[visit()]):
            self.assertTrue(venue_policy.filter_candidates([forged]))
        self.assertEqual(venue_policy.filter_candidates([forged]), [])
        with venue_policy.request_policy("식사 추가", "CHANGWON", []):
            self.assertEqual(venue_policy.filter_candidates([forged]), [])

    def test_missing_catalogue_does_not_fall_back_to_kakao_or_another_dish(self):
        invoke = Mock()
        with venue_policy.request_policy(QUESTION, "DAEGU", visits=[visit()]), \
                patch.object(venue_policy, "food_candidates", return_value=[]):
            self.assertEqual(venue_policy.search_candidates(invoke, {}, "FOOD"), [])
            result = venue_policy.explain_missing_signature({"answer": "조건 확인 실패", "places": []})
            self.assertIn("한만두 짬뽕만두", result["answer"])
            self.assertIn("코스에 넣지 못했어요", result["answer"])
        invoke.assert_not_called()

    def test_signature_and_specific_food_visits_do_not_share_their_defaults(self):
        specific = "구장 안에서 치킨 먹고"
        question = specific + " " + QUESTION
        normalized = visit_requests.normalize(question, [visit(specific, query="치킨", signature_default=False), visit()])
        selector = visit_requests.Selector(normalized, memory.empty(), "GWANGJU")
        with venue_policy.request_policy(question, "GWANGJU", visits=normalized):
            for v in normalized:
                with selector.policy(v):
                    rows = venue_policy.search_candidates(Mock(), {}, "FOOD")
                    if v["signature_default"]:
                        self.assertTrue(all(p["_signature_menu"] == "크림새우" for p in rows))
                    else:
                        self.assertTrue(any(p["name"] == "BHC치킨" for p in rows))
                        self.assertTrue(all(not p.get("_signature_menu") for p in rows))

    def test_chooser_preserves_signature_reason_and_saved_exclusions(self):
        code = "CHANGWON"
        anchor = reviewed_venue(code)
        with venue_policy.request_policy(QUESTION, code, visits=[visit()]), \
                patch.object(agent, "invoke_domain_tool") as invoke, \
                patch.object(evidence_memory, "requirements", return_value=[]) as requirements:
            rows = editing.candidates({**anchor, "category": "FOOD"}, anchor, {"query": ""}, [])
            chosen = editing.choose(rows, {"conditions": [QUESTION]}, QUESTION)
            self.assertIn("밀크셰이크", chosen["reason"])
            self.assertEqual(chosen["sourceCategory"], "CAFE")
            requirements.assert_not_called()
            requirements.return_value = [evidence_memory.Requirement(term="코아양과", attribute="catalog", intent="exclude", group="1")]
            self.assertIsNone(editing.choose(rows, {"conditions": [QUESTION, "코아양과 제외"]}, QUESTION))
            requirements.assert_called_once_with(["코아양과 제외"])
            invoke.assert_not_called()

    def test_snack_in_compound_request_is_generic_without_erasing_other_visits(self):
        question = "나 일식집에서 초밥을 먹고 구장에서 간식을 먹은 다음 경기 끝나고 술마시고 싶어. 코스 짜줘"
        requests = [visit("일식집에서 초밥을 먹고", query="초밥", conditions=["일식"], signature_default=False),
                    visit("구장에서 간식을 먹은 다음", query="간식", conditions=["간식"], signature_default=False),
                    visit("경기 끝나고 술마시고 싶어", kind="BAR", phase="AFTER", query="술집", signature_default=False)]
        normalized = visit_requests.normalize(question, requests)
        self.assertEqual([v["signature_default"] for v in normalized], [False, True, False])
        self.assertEqual([v["query"] for v in normalized], ["초밥", "", "술집"])
        self.assertIn("일식", normalized[0]["conditions"])
        self.assertEqual(normalized[1]["conditions"], [requests[1]["expression"]])
        selector = visit_requests.Selector(normalized, memory.empty(), "GWANGJU")
        with selector.policy(normalized[1]), patch.object(evidence_memory, "requirements") as requirements:
            rows = venue_policy.search_candidates(Mock(), {}, "FOOD")
            chosen = evidence_memory.collected_candidates(rows, normalized[1]["conditions"])
        requirements.assert_not_called()
        self.assertEqual([p["name"] for p in chosen], ["STATION"])
        self.assertIn("크림새우", chosen[0]["reason"])
        self.assertEqual(venue_policy.filter_candidates(rows), [])

    def test_generic_fallback_preserves_explicit_menu_and_dietary_constraints(self):
        for query, conditions in (("치킨", []), ("크림새우", []), ("야구공빵", []),
                                  ("", ["새우 알레르기"]), ("", ["비건"]), ("", ["요아정 제외"])):
            request = visit("구장에서 간식 먹고 싶어", query=query, conditions=conditions, signature_default=False)
            normalized = visit_requests.normalize(request["expression"], [request])[0]
            self.assertFalse(normalized["signature_default"])
            selector = visit_requests.Selector([normalized], memory.empty(), "GWANGJU")
            with selector.policy(normalized):
                self.assertFalse(venue_policy._signature_only("FOOD"))
                self.assertTrue(all(c in venue_policy.source_conditions(normalized["conditions"]) for c in conditions))

    def test_gwangju_primary_uses_station_source_and_keeps_yogurt_only_as_an_option(self):
        sources = food_candidates("GWANGJU")
        primary = [p for p in sources if details(p)]
        self.assertEqual([p["name"] for p in primary], ["STATION"])
        self.assertIn("1루 3층", primary[0]["address"])
        self.assertIn("106블럭", primary[0]["address"])
        self.assertTrue(primary[0]["placeUrl"].endswith("1루-3층-station/"))
        yogurt = next(p for p in sources if p["name"] == "요아정")
        self.assertIsNone(details(yogurt))
        self.assertEqual([r["menu"] for r in reference_details(yogurt)], ["홈런의 정석"])
        self.assertTrue(any("야구공빵" in m["name"] for p in sources for m in p.get("menuEvidence", {}).get("items", [])))

    def test_reference_dishes_need_actual_branch_menu_evidence(self):
        sources = food_candidates("GWANGJU")
        references = [r for p in sources for r in reference_details(p)]
        self.assertEqual({r["menu"] for r in references},
                         {"크림새우", "원샷 핫 로제 눈꽃", "칠리치즈 핫도그", "아츄", "홈런의 정석"})
        for row in references:
            self.assertTrue(row["sourceUrl"].startswith("https://myseatcheck.com/"))
            self.assertTrue(row["items"])
            if row["menu"] == "원샷 핫 로제 눈꽃":
                self.assertIn("4층", row["location"])
        station = next(p for p in sources if p["name"] == "STATION")
        without_menu = {**station, "menuEvidence": {"items": []}}
        self.assertEqual(reference_details(without_menu), [])
        self.assertIsNone(details(without_menu))
        self.assertEqual(reference_details({**station, "source": "KAKAO"}), [])

    def test_other_matched_dishes_can_be_selected_with_source_and_reference_links(self):
        for menu in ("원샷 핫 로제 눈꽃", "칠리치즈 핫도그", "아츄", "홈런의 정석"):
            with self.subTest(menu=menu), patch.object(evidence_memory, "requirements", return_value=[
                    evidence_memory.Requirement(term=menu, attribute="menu", intent="required", group="1")]):
                rows = evidence_memory.collected_candidates(food_candidates("GWANGJU"), [menu])
                self.assertTrue(rows)
                for row in rows:
                    self.assertIn(menu, row["reason"])
                    self.assertEqual(row["_menu_reference_url"], "https://notes0236.tistory.com/154")
                    self.assertTrue(row["placeUrl"].startswith("https://myseatcheck.com/"))


class SignatureCourseIntegrationTests(SimpleTestCase):
    def test_generic_internal_meal_replacement_preserves_other_stops(self):
        question = "식당만 바꿔줘. 구장에서 먹고 싶어"
        parsed = plan("replace", ["food"], internal_venue_requests=[
            {"category": "FOOD", "expression": QUESTION, "signature_default": True}])
        before = deepcopy(CURRENT)
        with patch.object(editing, "interpret", return_value=parsed), \
                patch.object(agent, "stadium_anchor", return_value=reviewed_venue("JAMSIL")), \
                patch.object(agent, "load_schedule", return_value=({}, 1)), \
                patch.object(agent, "find_game", return_value=(GAME, False, [])), \
                patch.object(agent, "invoke_domain_tool", return_value={}) as invoke:
            result = agent.answer(question, hint_stadium="JAMSIL", current_course=before, course_request="EDIT")
        self.assertEqual(before, CURRENT)
        self.assertEqual(result["places"][0]["name"], "통밥")
        self.assertIn("삼겹살정식 + 김치말이국수", result["answer"])
        self.assertIn(result["places"][0]["placeUrl"], result["answer"])
        self.assertEqual([p["placeId"] for p in result["places"][1:]], [p["placeId"] for p in CURRENT["places"][1:]])
        self.assertFalse(any(c.args[1] == "search_places" for c in invoke.call_args_list))

    def test_signature_reaches_generated_answer_map_and_saved_course_for_every_stadium(self):
        cases = [(code, QUESTION, visit()) for code in SIGNATURES]
        # Reproduce the former parser output too: generic snacks were incorrectly
        # treated as a specific menu. Both possible food/cafe classifications work.
        snack = "구장에서 간식을 먹은 다음"
        cases.extend(("GWANGJU", snack, visit(snack, kind=kind, query="간식", conditions=["간식"], signature_default=False))
                     for kind in ("FOOD", "CAFE"))
        for code, question, requested in cases:
            with self.subTest(code=code, kind=requested["kind"], question=question), ExitStack() as stack:
                point = reviewed_venue(code)
                stadium = {**point, "name": "시험 구장", "key": "STADIUM", "category": "STADIUM",
                           "distance": 0, "detail": "", "address": "", "placeId": None, "placeUrl": "", "doc_id": None}
                game = {"date": "2027-10-06", "time": "18:30", "home": "홈팀", "away": "원정팀", "status": "scheduled"}
                stack.enter_context(patch.object(editing, "interpret", return_value=plan("new", [], requested_visits=[requested])))
                for name, value in {"load_schedule": ({}, 7), "find_game": (game, False, [game]),
                                    "embed_many": ([.1], [.2]), "search_places": [], "stadium_anchor": stadium,
                                    "call_llm": ('{"course": []}', 0)}.items():
                    stack.enter_context(patch.object(agent, name, return_value=value))
                # Run actual live candidate adapter and selector; internal searches must not call Kakao.
                invoke = stack.enter_context(patch.object(agent, "invoke_domain_tool", return_value={}))
                stack.enter_context(patch.object(agent.transport, "info", return_value={"mode": "walk", "label": "도보", "lines": [], "taxi": False}))
                stack.enter_context(patch.object(evidence_memory, "requirements", return_value=[]))
                result = agent.answer(question, hint_stadium=code, course_memory=memory.empty(), course_request="NEW")
                stops = [p for p in result["places"] if p["category"] != "STADIUM"]
                self.assertEqual(len(stops), 1, result["answer"])
                self.assertIn(stops[0]["placeId"], [p["placeId"] for p in food_candidates(code) if details(p)])
                self.assertIn(menu_label(SIGNATURES[code]), result["answer"])
                self.assertIn(menu_label(SIGNATURES[code]), stops[0]["reason"])
                self.assertIn(stops[0]["placeUrl"], result["answer"])
                self.assertIn("매장 상세 위치", result["answer"])
                if code == "GWANGJU":
                    self.assertEqual(stops[0]["name"], "STATION")
                    self.assertIn("https://notes0236.tistory.com/154", result["answer"])
                self.assertEqual([p["name"] for p in result["coursePayload"]["stops"]], [p["name"] for p in result["places"]])
                self.assertEqual(result["courseMemory"]["conditions"], [])
                self.assertFalse(any(c.args[1] == "search_places" for c in invoke.call_args_list))
