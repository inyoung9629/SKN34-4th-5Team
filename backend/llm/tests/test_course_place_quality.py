from copy import deepcopy
from datetime import date, timedelta
from unittest.mock import Mock, patch

from django.test import SimpleTestCase
from django.core.cache.backends.locmem import LocMemCache

from baseball.stadium_locations import reviewed_venue
from llm.v1.rag.course import agent, editing, place_quality as quality, quality_research, slots, venue_policy
from travel.stadium_food import food_candidates


def adapted(row):
    return {**row, "category": "CAFE" if row["kind"] == "CAFE" else "FOOD_OUT",
            "placeUrl": f"https://place.map.kakao.com/{row['placeId']}", "dist": .2, "distance": 800}


class CoursePlaceQualityTests(SimpleTestCase):
    def setUp(self):
        # Freeze the test's research date, while production expires old research.
        self.date_patch = patch.object(quality, "date", wraps=date)
        self.clock = self.date_patch.start()
        self.clock.today.return_value = date(2026, 10, 7)
        self.addCleanup(self.date_patch.stop)
        self.rows = quality.catalogue()
        cache = LocMemCache(self.id(), {})
        cache.clear()
        for target, name, kwargs in ((quality, "verdict_cache", {"return_value": cache}),
                                     (quality_research, "discover", {"return_value": []}),
                                     (agent, "invoke_domain_tool", {"return_value": {"places": []}})):
            patched = patch.object(target, name, **kwargs)
            patched.start()
            self.addCleanup(patched.stop)

    def test_research_covers_food_bar_cafe_for_each_stadium_and_real_branches(self):
        for code in quality.STADIUMS:
            rows = [p for p in self.rows.values() if p["stadium"] == code]
            self.assertEqual({p["kind"] for p in rows}, {"FOOD", "BAR", "CAFE"})
            for row in rows:
                with self.subTest(name=row["name"]), quality.request_scope(code):
                    self.assertTrue(quality.approved(row))
                    self.assertTrue(venue_policy.filter_candidates([adapted(row)]))
                    self.assertTrue(row["interior"]["sourceUrl"].startswith("https://"))

    def test_missing_zero_stale_and_unreviewed_interior_are_not_approved(self):
        good = next(iter(self.rows.values()))
        for changes in ({"rating": 0}, {"rating": None}, {"ratingCount": 0}, {"ratingCount": 1},
                        {"status": "pending"}, {"checkedAt": "2026-10-08"},
                        {"checkedAt": (date(2026, 10, 7) - timedelta(days=181)).isoformat()},
                        {"interior": {"status": "poor", "sourceUrl": good["reviewUrl"], "note": "낡은 실내"}},
                        {"interior": {"status": "unknown"}}, {"reviewUrl": ""}):
            with self.subTest(changes=changes):
                self.assertFalse(quality.approved({**good, **changes}))

    def test_caller_supplied_ratings_and_other_branches_cannot_bypass_gate(self):
        original = adapted(self.rows["123618728"])
        rows = [{**original, "placeId": "unreviewed", "placeUrl": "", "rating": 5, "ratingCount": 999},
                {**original, "name": "스타벅스 다른지점"}, {**original, "lat": original["lat"] + .01},
                {**original, "placeId": "18774818", "name": "밤비"}]
        before = deepcopy(rows)
        with quality.request_scope("GWANGJU"):
            self.assertEqual(venue_policy.filter_candidates(rows), [])
            self.assertTrue(venue_policy.filter_candidates([original]))
        self.assertEqual(rows, before)

    def test_generation_editing_and_cached_candidates_share_the_gate(self):
        for code in quality.STADIUMS:
            anchor = reviewed_venue(code)
            for kind in ("FOOD", "CAFE", "BAR"):
                with self.subTest(code=code, kind=kind), quality.request_scope(code), patch.object(agent, "invoke_domain_tool") as invoke:
                    fresh = agent._kakao_step(kind, anchor, 2500, anchor, slots.parse("식사 카페 술집"))
                    fresh = [p for p in fresh if agent._step_matches(kind, p)]
                    self.assertTrue(fresh)
                    target = {**anchor, "category": "CAFE" if kind == "CAFE" else "FOOD"}
                    modified = editing.candidates(target, anchor, {"query": "술집" if kind == "BAR" else ""}, [])
                    self.assertTrue(modified)
                    for place in fresh + modified:
                        self.assertIsNotNone(quality.resolve(place))
                    unknown = {**fresh[0], "name": "미확인 매장", "placeId": "unknown", "placeUrl": ""}
                    cached = agent.pick([unknown, *fresh], 5, slots.parse("카페" if kind == "CAFE" else "식사"), 2500)
                    self.assertNotIn("unknown", [p["placeId"] for p in cached])
                    self.assertTrue(invoke.called, "Cached research must not replace the live search")

    def test_bar_search_restores_reviewed_kind_instead_of_model_suffix(self):
        cafe = adapted(self.rows["123618728"])
        with quality.request_scope("GWANGJU"):
            self.assertIsNone(quality.filter_place(cafe, "FOOD"))
        anchor = reviewed_venue("JAMSIL")
        with quality.request_scope("JAMSIL"):
            found = agent._kakao_step("BAR", anchor, 2500, anchor, slots.parse("술집"))
            self.assertTrue(any(p["name"] == "이치고" for p in found))
            self.assertTrue(all(quality.resolve(p)["kind"] == "BAR" for p in found))

    def test_radius_keywords_and_fixed_places_are_respected(self):
        anchor = reviewed_venue("JAMSIL")
        invoke = Mock()
        args = {"latitude": anchor["lat"], "longitude": anchor["lng"], "radius": 2500, "query": "상무초밥"}
        with quality.request_scope("JAMSIL"):
            found = venue_policy.search_candidates(invoke, args, "FOOD")
            self.assertEqual({p["id"] for p in found}, {"437494532"})
            self.assertEqual(venue_policy.search_candidates(invoke, {**args, "radius": 10}, "FOOD"), [])
            self.assertEqual(venue_policy.search_candidates(invoke, {**args, "query": "존재하지않는메뉴"}, "FOOD"), [])
            cafe = adapted(self.rows["362946062"])
            self.assertEqual(editing.candidates({**anchor, "category": "CAFE"}, anchor, {"query": ""}, [cafe]), [])
        self.assertTrue(invoke.called)

    def test_all_collected_internal_food_is_exempt_from_ratings(self):
        for code in ("JAMSIL", "GWANGJU"):
            anchor = reviewed_venue(code)
            for category in ("FOOD", "CAFE"):
                question = "구장 내부 먹거리와 카페"
                with self.subTest(code=code, category=category), quality.request_scope(code), \
                        venue_policy.request_policy(question, code, [{"category": category, "expression": question}]), \
                        patch.object(quality, "catalogue", side_effect=AssertionError("Internal food must not read ratings")):
                    invoke = Mock()
                    found = venue_policy.search_candidates(invoke, {
                        "latitude": anchor["lat"], "longitude": anchor["lng"], "radius": 2500}, category)
                    self.assertTrue(found)
                    checked = venue_policy.filter_candidates(found, category)
                    self.assertEqual(len(checked), len(found))
                    self.assertTrue(all(p["source"] == "MYSEATCHECK" for p in checked))
                    self.assertEqual(quality.missing_notice(), "")
                    invoke.assert_not_called()

    def test_scope_does_not_leak_and_other_stadiums_keep_existing_search(self):
        with self.assertRaises(RuntimeError):
            with quality.request_scope("GWANGJU"):
                self.assertTrue(quality.active("FOOD"))
                with quality.request_scope("SAJIK"):
                    self.assertFalse(quality.active("FOOD"))
                self.assertTrue(quality.active("FOOD"))
                raise RuntimeError("fixture")
        self.assertFalse(quality.active())
        invoke = Mock(return_value={"places": []})
        with quality.request_scope("SAJIK"):
            venue_policy.search_candidates(invoke, {}, "FOOD")
        invoke.assert_called_once()

    def test_public_entry_activates_scope_for_generation_history_and_saved_edit(self):
        with patch.object(agent, "_answer", side_effect=lambda *a: quality.active("FOOD")):
            self.assertTrue(agent.answer("잠실 카페 코스"))
            self.assertTrue(agent.answer("술집 넣어줘", history=[{"role": "user", "content": "광주 기아 구장 코스"}]))
        for saved in ({"stadiumCode": "GWANGJU"}, {"current": {"stadiumCode": "JAMSIL"}}):
            scopes = []
            def interpret(*args):
                scopes.append(quality.active("FOOD"))
                raise ValueError("stop after scope check")
            with patch.object(editing, "interpret", side_effect=interpret):
                agent.answer("카페 바꿔줘", course_memory=saved)
            self.assertEqual(scopes, [True])
        self.assertFalse(quality.active())

    def test_review_links_identify_rating_source_without_claiming_kakao_rating(self):
        text = quality.annotation(adapted(self.rows["1509452426"]))
        self.assertIn("다이닝코드 5/5", text)
        self.assertIn("평가 4명", text)
        self.assertIn(self.rows["1509452426"]["reviewUrl"], text)
        self.assertEqual(quality.annotation(food_candidates("JAMSIL")[0]), "")

    def test_missing_candidates_explain_research_limit_without_claiming_zero_rating(self):
        with quality.request_scope("JAMSIL"):
            self.assertEqual(quality.missing_notice(), "")
            quality.search_candidates({}, "FOOD")
            answer = agent.unverified_course("JAMSIL", {})["answer"]
            self.assertIn("확인 자료가 부족한 곳은 보류", answer)
            self.assertIn("평점이 0점이라는 뜻은 아니", answer)
        self.assertEqual(quality.missing_notice(), "")

    def test_user_blocked_bar_and_entertainment_category_never_return(self):
        base = adapted(self.rows["1509452426"])
        for changes in ({"placeId": "18774818"}, {"detail": "음식점 > 유흥주점"}, {"detail": "음식점 > 룸싸롱"}):
            self.assertIsNone(quality.filter_place({**base, **changes}, "FOOD"))
        self.assertIsNotNone(quality.filter_place({**base, "detail": "음식점 > 일본식주점, 룸 있음"}, "FOOD"))

    def test_new_kakao_branch_is_verified_and_reused_without_editing_static_list(self):
        p = {"id": "545328249", "place_name": "임동초밥집", "y": "35.161", "x": "126.894",
             "road_address_name": "광주 북구 서림로 90-1", "category_group_code": "FD6",
             "category_name": "음식점 > 일식 > 초밥,롤", "distance": 880}
        url = "https://www.diningcode.com/profile.php?rid=5ZE9xOR5ralS"
        def profile(item, *_):
            return {"place": item, "url": url, "rating": 5, "ratingCount": 5, "images": [], "reviews": []}
        with quality.request_scope("GWANGJU"), patch.object(quality_research, "discover", return_value=[url]) as discover, \
                patch.object(quality_research, "profile", side_effect=profile), \
                patch.object(quality_research, "interiors", return_value={p["id"]: {
                    "status": "acceptable", "basis": "customer_review", "sourceUrl": url, "note": "실내가 깔끔하고 정돈되어 있어요"}}):
            self.assertIsNone(quality.filter_place(p, "FOOD"))
            checked = quality.verify_candidates([p], "FOOD", "초밥")
            self.assertEqual([row["id"] for row in checked], [p["id"]])
            normalized = {"placeId": p["id"], "name": p["place_name"], "lat": float(p["y"]), "lng": float(p["x"]),
                          "category": "FOOD", "detail": p["category_name"]}
            self.assertIsNotNone(quality.filter_place(normalized, "FOOD"))
            quality.verify_candidates([p], "FOOD", "초밥")
            discover.assert_called_once()
        self.assertNotIn(p["id"], quality.catalogue())
        with quality.request_scope("JAMSIL"):
            self.assertIsNone(quality.filter_place(p, "FOOD"))

    def test_search_collects_all_three_pages_before_quality_verification(self):
        point = reviewed_venue("GWANGJU")
        def provider(_, __, args):
            return {"places": [{"id": str(args["page"] * 100 + i), "place_name": f"검색초밥{i}",
                    "y": str(point["lat"] + .01), "x": str(point["lng"]), "category_group_code": "FD6"}
                    for i in range(15)], "hasNextPage": args["page"] < 3}
        invoke = Mock(side_effect=provider)
        with quality.request_scope("GWANGJU"), patch.object(quality, "search_candidates", return_value=[]), \
                patch.object(quality, "verify_candidates", side_effect=lambda rows, *_, **kwargs: list(rows)) as verify:
            info = {}
            result = venue_policy.search_candidates(invoke, {"query": "초밥"}, "FOOD", diagnostics=info)
            self.assertEqual(invoke.call_count, 3)
            self.assertEqual(len(result), 45)
            self.assertEqual(info["rawCandidateCount"], 45)
            verify.assert_called_once()

    def test_unread_or_failed_verification_never_becomes_zero_rating_or_approval(self):
        p = {**adapted(self.rows["1063103478"]), "placeId": "999999", "name": "새로운 초밥집"}
        with quality.request_scope("GWANGJU"), patch.object(quality_research, "discover", return_value=[]) as discover:
            self.assertEqual(quality.verify_candidates([p], "FOOD", "초밥"), [])
            self.assertEqual(quality.verify_candidates([p], "FOOD", "초밥"), [])
            discover.assert_called_once()
            self.assertIn("평점이 0점이라는 뜻은 아니", quality.missing_notice())

    def test_old_lookup_miss_is_retried_once_without_revisiting_proven_rejection(self):
        base = adapted(self.rows["1063103478"])
        retry = {**base, "placeId": "999991", "name": "새 중국집"}
        rejected = {**base, "placeId": "999992", "name": "근거로 제외한 중국집"}
        quality.verdict_cache().set(quality.cache_key(retry), {"row": None})
        quality.verdict_cache().set(quality.cache_key(rejected), {"row": {"status": "rejected"}})
        with quality.request_scope("GWANGJU"), patch.object(quality_research, "discover", return_value=[]) as discover:
            self.assertEqual(quality.verify_candidates([retry, rejected], "FOOD", "자장면"), [])
            self.assertEqual([c.args[0]["placeId"] for c in discover.call_args_list], ["999991"])
        with quality.request_scope("GWANGJU"), patch.object(quality_research, "discover", return_value=[]) as discover:
            self.assertEqual(quality.verify_candidates([retry, rejected], "FOOD", "자장면"), [])
            discover.assert_not_called()

    def test_jajang_alias_prioritizes_named_menu_and_chinese_restaurants(self):
        base = adapted(self.rows["1063103478"])
        rows = [{**base, "placeId": str(999980 + i), "name": name, "detail": detail, "distance": distance}
                for i, (name, detail, distance) in enumerate([
                    ("양꼬치집", "음식점 > 중식 > 양꼬치", 100),
                    ("초밥집", "음식점 > 일식 > 초밥", 150),
                    ("중국요리집", "음식점 > 중식 > 중국요리", 280),
                    ("쟁반짜장집", "음식점 > 중식 > 중국요리", 900)])]
        with quality.request_scope("GWANGJU"), patch.object(quality_research, "discover", return_value=[]) as discover:
            self.assertEqual(quality.verify_candidates(rows, "FOOD", "자장면"), [])
        self.assertEqual([c.args[0]["name"] for c in discover.call_args_list],
                         ["쟁반짜장집", "중국요리집", "양꼬치집", "초밥집"])
