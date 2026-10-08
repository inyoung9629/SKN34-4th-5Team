from unittest.mock import patch

from django.test import SimpleTestCase

from llm.v1.rag.course import agent as course


class CourseCandidatesTest(SimpleTestCase):
    def test_compound_cuisine_menu_query_does_not_hide_other_restaurants(self):
        from llm.v1.rag.course.editing import candidates
        anchor = {"lat": 37.5, "lng": 127.0, "category": "FOOD"}
        calls = []
        def search(_domain, _tool, args):
            calls.append(args.get("query"))
            self.assertEqual(args["radius"], 2500)
            name = "요리반점" if args.get("query") == "중식" else "짜장전문점"
            return {"places": [{"id": name, "place_name": name, "x": "127.001", "y": "37.5", "category_name": "음식점 > 중식"}]}
        with patch.object(course, "invoke_domain_tool", side_effect=search):
            result = candidates(anchor, anchor, {"query": "중식 자장면", "conditions": ["중식", "자장면"]}, [])
        self.assertEqual(calls, ["중식 자장면", "중식"])
        self.assertEqual({p["name"] for p in result}, {"짜장전문점", "요리반점"})

    def test_jajang_spelling_search_preserves_radius_and_excludes_the_existing_course(self):
        from llm.v1.rag.course.editing import candidates
        anchor = {"lat": 37.5, "lng": 127.0, "category": "FOOD"}
        calls = []
        def search(_domain, _tool, args):
            calls.append(args.get("query"))
            self.assertEqual(args["radius"], 2500)
            if args.get("query") != "짜장면":
                return {"places": []}
            return {"places": [{"id": "fresh", "place_name": "가상 중식집", "x": "127.001", "y": "37.5"},
                               {"id": "fixed", "place_name": "기존 식당", "x": "127.001", "y": "37.5"}]}
        with patch.object(course, "invoke_domain_tool", side_effect=search):
            result = candidates(anchor, anchor, {"query": "자장면"}, [{"placeId": "fixed", "name": "기존 식당", **anchor}])
        self.assertEqual(calls, ["자장면", "짜장면", "중식"])
        self.assertEqual([p["placeId"] for p in result], ["fresh"])

    def test_verified_candidates_survive_later_route_searches_and_new_candidate_failures(self):
        from llm.v1.rag.course import editing
        food = {"name": "초밥집", "category": "FOOD_OUT", "placeId": "1", "lat": 35.1947, "lng": 129.062}
        other = {**food, "name": "새 후보", "placeId": "2", "lng": 129.063}
        confirmed = {**food, "conditionChecks": [{"term": "초밥", "status": "match"}]}
        state = {"conditions": [{"scope": "FOOD", "text": "초밥"}]}
        cache = {}
        with patch.object(editing, "choose", side_effect=[[confirmed], []]) as choose:
            self.assertEqual(course.remembered_candidates([food], state, cache), [confirmed])
            self.assertEqual(course.remembered_candidates([other, food], state, cache), [confirmed])
            self.assertEqual(choose.call_args.args[0], [other])
            self.assertEqual(course.remembered_candidates([food], state, cache), [confirmed])
            self.assertEqual(choose.call_count, 2)
        with patch.object(editing, "choose", return_value=[]) as choose:
            self.assertEqual(course.remembered_candidates([food], {"conditions": [{"scope": "FOOD", "text": "스테이크"}]}, cache), [])
            choose.assert_called_once()
        self.assertEqual(course.remembered_candidates([food], {**state, "rejected": [food]}, cache), [])

    def test_empty_keyword_tries_spelling_variant_with_same_radius_before_category(self):
        from llm.v1.rag.course.editing import candidates
        anchor = {"lat": 37.5, "lng": 127.0, "category": "FOOD"}
        queries = []

        def lookup(_domain, _tool, args):
            queries.append(args.get("query", ""))
            self.assertEqual(args["radius"], 2500)
            if args.get("query") != "돈가스":
                return {"places": []}
            return {"places": [{"id": "1", "place_name": "가상 식당", "x": "127.001", "y": "37.5"},
                               {"id": "2", "place_name": "범위 밖", "x": "128", "y": "37.5"}]}

        with patch.object(course, "invoke_domain_tool", side_effect=lookup):
            result = candidates(anchor, anchor, {"query": "돈까스"}, [])
        self.assertEqual(queries, ["돈까스", "돈가스"])
        self.assertEqual([p["placeId"] for p in result], ["1"])
        self.assertEqual(result[0]["_search_query"], "돈가스")

    def test_requested_menu_searches_specific_keywords_only_for_requested_activity(self):
        from llm.v1.rag.course.evidence_memory import Requirement
        anchor = {"lat": 37.5, "lng": 127.0}
        state = {"conditions": [{"scope": "FOOD", "text": "돈까스 판매"}, {"scope": "CAFE", "text": "조용한 카페"}]}
        req = Requirement(term="돈까스", attribute="menu", intent="required", group="meal")
        item = {"name": "가상 식당", "category": "FOOD", "lat": 37.5, "lng": 127.001, "placeId": "fixture:menu"}
        with patch("llm.v1.rag.course.evidence_memory.requirements", return_value=[req]) as parse, \
                patch("llm.v1.rag.course.editing.candidates", return_value=[item, item]) as search:
            result = course.requested_menu_candidates(anchor, state, ["FOOD"])
        parse.assert_called_once_with(["돈까스 판매"])
        self.assertEqual(search.call_args.args[2], {"query": "돈까스"})
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["category"], "FOOD_OUT")

    def test_only_actual_keyword_hits_get_hints_and_duplicate_hits_merge(self):
        from llm.v1.rag.course.evidence_memory import Requirement
        anchor = {"lat": 37.5, "lng": 127.0}
        state = {"conditions": [{"scope": "FOOD", "text": "돈까스와 냉모밀 판매"}]}
        requested = [Requirement(term=term, attribute="menu", intent="required", group=term)
                     for term in ("돈까스", "냉모밀")]
        item = {"name": "가상 식당", "category": "FOOD", "lat": 37.5, "lng": 127.001, "placeId": "fixture:menu"}
        fallback = {**item, "name": "가상 일반 식당", "placeId": "fixture:fallback", "_search_query": ""}

        def search(_target, _anchor, plan, _fixed):
            return [{**item, "_search_query": plan["query"]}, fallback]

        with patch("llm.v1.rag.course.evidence_memory.requirements", return_value=requested), \
                patch("llm.v1.rag.course.editing.candidates", side_effect=search):
            result = course.requested_menu_candidates(anchor, state, ["FOOD"])
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["_menu_queries"], ["돈까스", "냉모밀"])
        self.assertEqual(result[1]["_menu_queries"], [])

    def test_after_game_walk_is_not_added_before_game_or_duplicated_as_tourism(self):
        sl = course.slots.parse("경기 보기 전에 식당에서 먹고 카페에서 커피 들고 구장 갈 거야. 경기 끝나고 산책도 할 거야")
        self.assertEqual(sl["scope"], "both")
        self.assertEqual(sl["extra_phases"], {"walk": "AFTER"})
        before, after = course.plan_steps(sl, evening=False)
        self.assertNotIn("WALK", before)
        self.assertIn("WALK", after)
        lookup = {"meal": {"category": "FOOD_OUT", "name": "식당"},
                  "game": {"category": "STADIUM", "name": "구장"},
                  "park1": {"category": "WALK", "name": "어린이공원"},
                  "park2": {"category": "SPOT", "name": "산호공원"}}
        steps = [{"key": "meal", "phase": "BEFORE"}, {"key": "park1", "phase": "BEFORE"},
                 {"key": "game", "phase": "GAME"}, {"key": "park2", "phase": "AFTER"}]
        aligned = course.align_extra_stops(steps, lookup, sl)
        self.assertEqual([s["key"] for s in aligned], ["meal", "game", "park2"])
        self.assertTrue(course.is_extra_place(lookup["park2"], "walk"))

    def test_anchor_uses_canonical_stadium_without_rag_index(self):
        stadium = {"id": 1, "stadium_code": "CHANGWON", "stadium_name_ko": "창원 NC 파크",
                   "latitude": "35.22262735", "longitude": "128.58238155", "address": "경남 창원시 삼호로 63"}
        with patch.object(course, "invoke_domain_tool", return_value={"item": stadium}):
            # SimpleTestCase는 DB 접근을 금지하므로 RAG 청크에 접근하면 실패한다.
            anchor = course.stadium_anchor("CHANGWON")
        self.assertEqual(anchor["lat"], 35.22262735)
        self.assertEqual(anchor["lng"], 128.58238155)
        self.assertEqual(anchor["name"], "창원 NC 파크")

    def test_category_search_paginates_past_stadium_concessions_and_keeps_outside_places(self):
        anchor = {"lat": 35.2226, "lng": 128.5824, "address": "경남 창원시 삼호로 63"}
        calls = []

        def search(_domain, name, args):
            if name == "search_tourism":
                return {"places": []}
            self.assertEqual(name, "search_places")
            self.assertEqual(args["method"], "category")
            self.assertNotIn("query", args)
            calls.append((args["category"], args["page"]))
            if args["page"] == 1:
                return {"hasNextPage": True, "places": [{"id": "inside", "place_name": "구장 내부 매점",
                         "road_address_name": "경남 창원시 삼호로 63", "y": "35.2226", "x": "128.5824"}]}
            category = args["category"]
            item = {"id": category, "place_name": "든든한 식당" if category == "FD6" else "동네 카페",
                    "category_name": "음식점 > 한식" if category == "FD6" else "음식점 > 카페",
                    "road_address_name": "경남 창원시 삼호로 70", "y": "35.224", "x": "128.583",
                    "place_url": "https://place.map.kakao.com/example"}
            return {"hasNextPage": False, "places": [item, item]}

        with patch.object(course, "invoke_domain_tool", side_effect=search):
            candidates, _ = course._live_candidates("CHANGWON", anchor, "식사하고 커피", None)
        for category in ("FD6", "CE7"):
            self.assertEqual([page for kind, page in calls if kind == category], [1, 2])
        self.assertEqual([(p["category"], p["name"]) for p in candidates],
                         [("FOOD_OUT", "든든한 식당"), ("CAFE", "동네 카페")])
        self.assertTrue(all(p["placeUrl"] and p["distance"] > 0 for p in candidates))

    def test_provider_failure_does_not_discard_places_from_an_earlier_page(self):
        anchor = {"lat": 35.2226, "lng": 128.5824, "address": "삼호로 63"}

        def search(_domain, name, args):
            if name == "search_tourism":
                return {"places": []}
            if args["page"] == 2:
                return "[조회 실패] 일시적인 오류"
            return {"hasNextPage": True, "places": [{"id": args["category"], "place_name": "확인된 가게",
                     "road_address_name": "삼호로 70", "y": "35.224", "x": "128.583"}]}

        with patch.object(course, "invoke_domain_tool", side_effect=search):
            candidates, data = course._live_candidates("CHANGWON", anchor, "코스", None)
        self.assertEqual(len(candidates), 2)
        self.assertIn("조회 실패", data["food"]["warning"])
