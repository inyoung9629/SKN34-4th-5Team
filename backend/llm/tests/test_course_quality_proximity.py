from contextlib import ExitStack
from datetime import date
from unittest.mock import Mock, patch

from django.core.cache.backends.locmem import LocMemCache
from django.test import SimpleTestCase

from llm.v1.rag.course import agent, availability, editing, evidence_memory, place_quality as quality, quality_research as research, slots, venue_policy


class QualityProximityTests(SimpleTestCase):
    def setUp(self):
        self.center = {"lat": 35.178, "lng": 126.892}
        self.cache = LocMemCache(self.id(), {})
        self.cache.clear()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(quality, "catalogue", return_value={}))
        self.stack.enter_context(patch.object(quality, "verdict_cache", return_value=self.cache))
        self.stack.enter_context(quality.request_scope("GWANGJU"))
        self.discover = self.stack.enter_context(patch.object(research, "discover", return_value=["https://www.diningcode.com/profile.php?rid=fixture123"]))
        self.stack.enter_context(patch.object(research, "profile", side_effect=lambda p, url, _: {
            "place": p, "url": url, "rating": 4.5, "ratingCount": 10, "images": [], "reviews": []}))
        self.stack.enter_context(patch.object(research, "interiors", side_effect=lambda profiles: {
            p["place"]["placeId"]: {"status": "acceptable", "sourceUrl": p["url"], "note": "검증용 실제 매장 근거"} for p in profiles}))

    def place(self, pid, meters, *, cached=False, center=None, kind="CAFE"):
        point = center or self.center
        p = {"id": str(pid), "place_name": f"매장{pid}", "y": str(point["lat"] + meters / 111195), "x": str(point["lng"]),
             "category_group_code": "CE7" if kind == "CAFE" else "FD6", "category_name": "음식점 > 카페" if kind == "CAFE" else "음식점 > 중식 > 중국요리",
             "road_address_name": f"광주 북구 검증로 {pid}", "place_url": f"https://place.map.kakao.com/{pid}", "distance": 1}
        if cached:
            row = {**quality.canonical(p), "status": "approved", "rating": 4.5, "ratingCount": 10,
                   "reviewUrl": "https://www.diningcode.com/profile.php?rid=fixture123", "checkedAt": date.today().isoformat(),
                   "interior": {"status": "acceptable", "sourceUrl": "https://www.diningcode.com/profile.php?rid=fixture123", "note": "검증된 실내"}}
            self.cache.set(quality.cache_key(p), {"row": row})
        return p

    def search(self, places, *, center=None, radius=2500, query="카페", **kwargs):
        point = center or self.center
        invoke = Mock(return_value={"places": places})
        result = venue_policy.search_candidates(invoke,
            {"latitude": point["lat"], "longitude": point["lng"], "radius": radius, "query": query},
            "CAFE" if query == "카페" else "FOOD", **kwargs)
        invoke.assert_called_once()  # Live discovery remains enabled even on a warm cache.
        return result

    def test_three_close_cached_alternatives_avoid_all_24_extra_investigations(self):
        cached = [self.place(i, m, cached=True) for i, m in enumerate((100, 150, 200), 100)]
        fresh = [self.place(200 + i, 230 + i * 40) for i in range(24)]
        result = self.search([*reversed(cached), *fresh])
        self.assertEqual([p["id"] for p in result], [p["id"] for p in cached])
        self.discover.assert_not_called()
        self.assertEqual(quality._RESEARCH.get()["tried"], set())

    def test_distant_cached_places_never_stop_investigation_of_closer_new_places(self):
        far = [self.place(i, m, cached=True) for i, m in enumerate((1500, 1800, 2000), 100)]
        near = [self.place(i, m) for i, m in enumerate((100, 150, 200), 200)]
        result = self.search([*far, *near])
        self.assertEqual([p["id"] for p in result[:3]], [p["id"] for p in near])
        self.assertEqual(self.discover.call_count, 3)

    def test_even_within_800m_cache_does_not_hide_substantially_closer_candidates(self):
        cached = [self.place(i, m, cached=True) for i, m in enumerate((650, 700, 750), 100)]
        fresh = [self.place(i, m) for i, m in enumerate((80, 110, 160), 200)]
        self.assertEqual([p["id"] for p in self.search([*cached, *fresh])[:3]], [p["id"] for p in fresh])
        self.assertEqual(self.discover.call_count, 3)

    def test_new_origin_changes_nearby_candidates_and_provider_distance_is_not_trusted(self):
        old = [self.place(i, m, cached=True) for i, m in enumerate((100, 150, 200), 100)]
        moved = {**self.center, "lng": self.center["lng"] + .015}
        fresh = [self.place(i, m, center=moved) for i, m in enumerate((100, 150, 200), 200)]
        result = self.search([*old, *fresh], center=moved, radius=800)
        self.assertEqual([p["id"] for p in result], [p["id"] for p in fresh])
        self.assertEqual(self.discover.call_count, 3)

    def test_generation_checks_near_origin_instead_of_reusing_stadium_cache(self):
        far = [self.place(i, m, cached=True) for i, m in enumerate((100, 150, 200), 100)]
        origin = {**self.center, "lng": self.center["lng"] - .014}
        near = [self.place(i, m, center=origin) for i, m in enumerate((100, 150, 200), 200)]
        with patch.object(agent, "invoke_domain_tool", return_value={"places": far + near}) as invoke:
            result = agent.build_origin_course(origin, self.center, [], slots.parse("경기 전 카페만"), False)
        self.assertIn(result[0]["place"]["placeId"], [p["id"] for p in near])
        self.assertEqual(self.discover.call_count, 3)
        self.assertEqual(invoke.call_args.args[2]["longitude"], origin["lng"])

    def test_same_brand_does_not_count_as_three_alternatives(self):
        places = [self.place(i, m) for i, m in enumerate((100, 120, 150, 170, 190), 100)]
        for i, p in enumerate(places[:3]):
            p["place_name"] = f"동일브랜드 {i}호점"
            row = self.place(900 + i, 100, cached=True)
            proof = self.cache.get(quality.cache_key(row))["row"]
            self.cache.set(quality.cache_key(p), {"row": {**proof, **quality.canonical(p)}})
        self.search(places)
        self.assertEqual(self.discover.call_count, 2)

    def test_specific_menu_or_extra_conditions_cannot_use_rating_only_shortcut(self):
        for query, reuse in (("자장면", True), ("식사", False)):
            with self.subTest(query=query), quality.request_scope("GWANGJU"):
                self.cache.clear()
                self.discover.reset_mock()
                rows = [self.place(i, m, cached=True, kind="FOOD") for i, m in enumerate((100, 150, 200), 100)]
                rows.append(self.place(200, 220, kind="FOOD"))
                self.search(rows, query=query, quality_reuse=reuse)
                self.discover.assert_called_once()

    def test_specific_menu_covers_stadium_before_dense_departure_area_exhausts_budget(self):
        stadium = {**self.center, "lng": self.center["lng"] + .018}
        near_origin = [self.place(200 + i, 80 + i * 25, kind="FOOD") for i in range(20)]
        near_stadium = self.place(300, 150, center=stadium, kind="FOOD")
        cached_lamb = self.place(400, 900, kind="FOOD")
        cached_lamb.update(place_name="양꼬치집", category_name="음식점 > 중식 > 양꼬치")
        proof = {**quality.canonical(cached_lamb), "status": "approved", "rating": 4.2, "ratingCount": 3,
                 "reviewUrl": "https://www.diningcode.com/profile.php?rid=fixture123", "checkedAt": date.today().isoformat(),
                 "interior": {"status": "acceptable", "sourceUrl": "https://www.diningcode.com/profile.php?rid=fixture123", "note": "정돈된 좌석"}}
        self.cache.set(quality.cache_key(cached_lamb), {"row": proof})
        # Most nearby candidates have no readable matching profile. A cached
        # lamb shop is not evidence that the requested jajang menu was found.
        with patch.object(research, "profile", side_effect=lambda p, url, _: {
                "place": p, "url": url, "rating": 4.6, "ratingCount": 5, "images": [], "reviews": []}
                if p["placeId"] == near_stadium["id"] else None):
            result = self.search([cached_lamb, *near_origin, near_stadium], center=stadium,
                                 query="자장면", quality_center=self.center)
        examined = [call.args[0]["placeId"] for call in self.discover.call_args_list]
        self.assertEqual(examined[:2], [near_origin[0]["id"], near_stadium["id"]])
        self.assertEqual(len(examined), 10)  # The existing per-query limit stays bounded.
        self.assertIn(near_stadium["id"], [p["id"] for p in result])
        self.assertEqual(result[0]["id"], cached_lamb["id"])  # Final ranking still uses departure distance.

    def test_generic_search_keeps_departure_proximity_with_different_search_center(self):
        stadium = {**self.center, "lng": self.center["lng"] + .018}
        near_origin = [self.place(200 + i, 80 + i * 25) for i in range(3)]
        near_stadium = self.place(300, 100, center=stadium)
        result = self.search([near_stadium, *near_origin], center=stadium, quality_center=self.center)
        self.assertEqual([p["id"] for p in result], [p["id"] for p in near_origin])
        self.assertEqual(self.discover.call_count, 3)

    def test_jajang_expands_cuisine_even_when_keyword_has_cached_lamb_shop(self):
        lamb = self.place(100, 150, kind="FOOD")
        lamb.update(place_name="양꼬치집", category_name="음식점 > 중식 > 양꼬치")
        chinese = self.place(200, 200, kind="FOOD")
        def search(_, args, *a, **kw):
            return [chinese] if args.get("query") == "중식" else [lamb]
        with patch.object(venue_policy, "search_candidates", side_effect=search) as lookup:
            result = editing.candidates({**self.center, "category": "FOOD"}, self.center,
                                        {"query": "자장면", "conditions": ["자장면"]}, [])
        self.assertEqual([c.args[1].get("query") for c in lookup.call_args_list], ["자장면", "중식"])
        self.assertEqual([p["placeId"] for p in result], [lamb["id"], chinese["id"]])

    def test_excluded_cache_cannot_suppress_new_candidates(self):
        old = [self.place(i, m, cached=True) for i, m in enumerate((100, 150, 200), 100)]
        new = [self.place(i, m) for i, m in enumerate((230, 250, 270), 200)]
        result = self.search(old + new, candidate_filter=lambda p: int(p["id"]) >= 200)
        self.assertEqual([p["id"] for p in result], [p["id"] for p in new])
        self.assertEqual(self.discover.call_count, 3)

    def test_closed_cached_places_do_not_count_as_available_alternatives(self):
        old = [self.place(i, m, cached=True) for i, m in enumerate((100, 150, 200), 100)]
        new = [self.place(i, m) for i, m in enumerate((230, 250, 270), 200)]
        with patch.object(availability, "check", side_effect=lambda p, *_: "휴무" if int(p["id"]) < 200 else None):
            self.search(old + new)
        self.assertEqual(self.discover.call_count, 3)

    def test_cold_generic_search_stops_when_three_local_alternatives_are_confirmed(self):
        rows = [self.place(100 + i, 100 + i * 20) for i in range(10)]
        result = self.search(rows)
        self.assertEqual([p["id"] for p in result], [p["id"] for p in rows[:3]])
        self.assertEqual(self.discover.call_count, 3)

    def test_extra_requirements_disable_rating_only_shortcut(self):
        for attribute, term, expected in (("catalog", "카페", True), ("catalog", "메가커피", False),
                                           ("menu", "짜장면", False), ("review_feature", "조용함", False)):
            with self.subTest(term=term), patch.object(evidence_memory, "requirements", return_value=[
                evidence_memory.Requirement(term=term, attribute=attribute, intent="required", group="1")]):
                self.assertEqual(quality.reusable_conditions([term]), expected)

    def test_generic_discovery_deferral_does_not_cache_a_failure(self):
        row = self.place(200, 150)
        with quality.research_policy(False):
            self.assertEqual(self.search([row]), [])
        self.assertIsNone(self.cache.get(quality.cache_key(row)))
        self.discover.assert_not_called()
        self.assertEqual([p["id"] for p in self.search([row])], [row["id"]])

    def test_edit_excludes_fixed_places_before_deciding_cache_is_sufficient(self):
        old = [self.place(i, m, cached=True) for i, m in enumerate((100, 150, 200), 100)]
        new = [self.place(i, m) for i, m in enumerate((230, 250, 270), 200)]
        fixed = [{"placeId": p["id"], "name": p["place_name"], "lat": float(p["y"]), "lng": float(p["x"])} for p in old]
        with patch.object(agent, "invoke_domain_tool", return_value={"places": old + new}):
            result = editing.candidates({**self.center, "category": "CAFE"}, self.center, {"query": "카페"}, fixed)
        self.assertEqual([p["placeId"] for p in result], [p["id"] for p in new])
        self.assertEqual(self.discover.call_count, 3)
