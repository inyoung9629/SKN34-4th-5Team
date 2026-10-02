"""Offline adaptive retrieval tests: no paid model, search key or provider calls."""
from dataclasses import replace
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from django.test import SimpleTestCase, override_settings

from .kakao_course_candidates import (CandidatePolicy, CandidateSearchError,
                                      KakaoCourseCandidates, circle_box, in_box)
from .place_service import (PlaceConfigurationError, PlaceRateLimitError, PlaceUpstreamError,
                            PlaceValidationError, search_candidate_cell)
from .stadium_scope import contains, reviewed_frames

CENTER = {"lat": 37.5, "lng": 127.0}
FAST = CandidatePolicy(interval_seconds=0)


def document(identity, *, north=0, east=0, category="FD6", name=None, detail="음식점 > 한식"):
    return {"id": str(identity), "place_name": name or f"식당{identity}",
            "x": str(127 + east / 88000), "y": str(37.5 + north / 111195),
            "category_group_code": category, "category_name": detail,
            "road_address_name": "서울 가상로 1", "address_name": "서울 가상동 1"}


class FakeKakao:
    def __init__(self, docs):
        self.docs, self.calls = docs, []

    def __call__(self, *, rect, category, keyword, page, timeout):
        self.calls.append({"rect": rect, "category": category, "keyword": keyword, "page": page})
        found = [d for d in self.docs if in_box(d, rect) and (not category or d["category_group_code"] == category)
                 and (not keyword or keyword in d["place_name"] + d["category_name"])]
        total, pageable = len(found), min(45, len(found))
        return {"total_count": total, "pageable_count": pageable, "is_end": page * 15 >= pageable,
                "places": found[(page - 1) * 15:min(page * 15, pageable)]}


def crowded_docs():
    return [document(i * 9 + j + 1, north=(i - 4) * 12, east=(j - 4) * 12)
            for i in range(9) for j in range(9)]


def provider(docs=(), **kwargs):
    engine = FakeKakao(docs)
    return KakaoCourseCandidates("JAMSIL", fetch=engine, frames=[], policy=FAST, **kwargs), engine


class CandidateRetrievalTests(SimpleTestCase):
    def test_cell_coordinate_rounding_is_bounded_and_not_a_radius_override(self):
        from .place_service import _near_cell
        rect = circle_box(CENTER, 100)
        boundary = {**document(1), 'y': str(rect[3] + .4/111195)}
        distant = {**document(2), 'y': str(rect[3] + 10/111195)}
        self.assertTrue(_near_cell(boundary, rect))
        self.assertFalse(_near_cell(distant, rect))
        source = KakaoCourseCandidates('JAMSIL', frames=[], zones=[], policy=FAST,
            fetch=Mock(return_value={'places':[boundary], 'total_count':1,
                                     'pageable_count':1, 'is_end':True}))
        self.assertEqual(source.search(CENTER, 100, 'food'), [])

    def test_subdivision_recovers_all_81_not_only_first_45_and_dedupes_seams(self):
        source, engine = provider(crowded_docs())
        results = source.search(CENTER, 100, "food")
        self.assertEqual({p["placeId"] for p in results}, {str(i) for i in range(1, 82)})
        self.assertGreater(source.audit()["split_count"], 0)
        self.assertGreater(len(engine.calls), 3)
        self.assertEqual(source.audit()["status"], "complete")
        self.assertEqual(source.audit()["candidate_count"], 81)
        self.assertEqual(source.audit()["queries"][0]["queries"], [{"category": "FD6", "keyword": ""}])

    def test_exactly_45_uses_three_pages_without_unnecessary_split(self):
        source, engine = provider(crowded_docs()[:45])
        self.assertEqual(len(source.search(CENTER, 100, "food")), 45)
        self.assertEqual([c["page"] for c in engine.calls], [1, 2, 3])
        self.assertEqual(source.splits, 0)

    def test_is_end_does_not_hide_capped_total(self):
        fetch = Mock(return_value={"places": [], "total_count": 46, "pageable_count": 0, "is_end": True})
        source = KakaoCourseCandidates("JAMSIL", frames=[], fetch=fetch,
                                       policy=replace(FAST, max_depth=0))
        with self.assertRaises(CandidateSearchError) as error:
            source.search(CENTER, 100, "food")
        self.assertEqual(error.exception.reason, "dense_cell_unresolved")
        self.assertEqual(source.audit()["status"], "candidate_search_incomplete")

    def test_empty_is_complete_only_with_consistent_zero_metadata(self):
        source, _ = provider()
        self.assertEqual(source.search(CENTER, 100, "food"), [])
        self.assertEqual(source.audit()["status"], "complete")

    def test_circle_filter_rejects_square_corners_but_keeps_near_boundary(self):
        source, _ = provider([document(1, north=99.9), document(2, north=85, east=85)])
        self.assertEqual([p["placeId"] for p in source.search(CENTER, 100, "food")], ["1"])

    def test_repeated_and_contained_circle_reuse_request_cache(self):
        source, engine = provider(crowded_docs())
        first = source.search(CENTER, 300, "food")
        calls = len(engine.calls)
        self.assertEqual(source.search(CENTER, 300, "food"), first)
        self.assertEqual(len(source.search(CENTER, 100, "food")), 81)
        self.assertEqual(len(engine.calls), calls)
        self.assertGreater(source.cache_hits, 0)
        second, _ = provider(crowded_docs())
        second.search(CENTER, 100, "food")
        self.assertGreater(second.calls, 0)  # no cross-user/persistent provider cache

    def test_dense_radius_growth_only_queries_uncovered_strips(self):
        source, engine = provider(crowded_docs() + [document(100, north=250)])
        source.search(CENTER, 100, "food")
        old = engine.calls[0]["rect"]
        calls = len(engine.calls)
        self.assertEqual(len(source.search(CENTER, 300, "food")), 82)
        for call in engine.calls[calls:]:
            w, s, e, n = call["rect"]
            # No positive-area overlap with already-completed inner box.
            self.assertFalse(max(w, old[0]) < min(e, old[2]) and max(s, old[1]) < min(n, old[3]))

    def test_sparse_growth_requeries_full_area_once_without_losing_candidates(self):
        source, engine = provider([document(1, north=30), document(2, north=250)])
        source.search(CENTER, 100, "food")
        self.assertEqual({p["placeId"] for p in source.search(CENTER, 300, "food")}, {"1", "2"})
        self.assertEqual(len(engine.calls), 2)
        self.assertEqual(engine.calls[-1]["rect"], circle_box(CENTER, 300))

    def test_empty_walk_growth_uses_four_queries_per_radius_not_four_strips_each(self):
        source, engine = provider()
        for radius in (100, 300, 500, 800):
            self.assertEqual(source.search(CENTER, radius, "walk"), [])
        self.assertEqual(len(engine.calls), 16)

    def test_sparse_growth_still_subdivides_new_dense_area(self):
        docs = [document(i * 9 + j + 1, north=180 + i * 6, east=(j - 4) * 6)
                for i in range(9) for j in range(9)]
        source, _ = provider(docs)
        self.assertEqual(source.search(CENTER, 100, "food"), [])
        self.assertEqual(len(source.search(CENTER, 300, "food")), 81)
        self.assertGreater(source.splits, 0)

    def test_timeout_error_after_deadline_is_budget_not_upstream_failure(self):
        now = [0.]
        def slow(**kwargs):
            now[0] += 2
            raise PlaceUpstreamError
        source = KakaoCourseCandidates("JAMSIL", fetch=slow, frames=[], clock=lambda: now[0],
                                       policy=replace(FAST, wall_seconds=1))
        with self.assertRaises(CandidateSearchError) as error:
            source.search(CENTER, 100, "walk")
        self.assertEqual(error.exception.reason, "time_budget")
        self.assertEqual(error.exception.status, "candidate_search_incomplete")

    def test_each_next_center_gets_its_own_circle(self):
        source, _ = provider([document(1, north=350)])
        self.assertEqual(source.search(CENTER, 100, "food"), [])
        results = source.search({**CENTER, "lat": 37.5 + 300 / 111195}, 100, "food")
        self.assertEqual(results[0]["placeId"], "1")

    def test_call_budget_never_succeeds_with_partial_top45(self):
        source = KakaoCourseCandidates("JAMSIL", fetch=FakeKakao(crowded_docs()), frames=[],
                                       policy=replace(FAST, max_calls=1))
        with self.assertRaises(CandidateSearchError) as error:
            source.search(CENTER, 100, "food")
        self.assertEqual(error.exception.reason, "call_budget")
        self.assertEqual(source.calls, 1)

    def test_collocated_46_are_explicitly_unresolved(self):
        source, _ = provider([document(i) for i in range(46)])
        with self.assertRaises(CandidateSearchError) as error:
            source.search(CENTER, 100, "food")
        self.assertEqual(error.exception.reason, "dense_cell_unresolved")
        self.assertLess(source.calls, 120)

    def test_metadata_changes_duplicates_and_missing_pages_do_not_mean_no_match(self):
        for defect in ("count", "duplicate", "missing", "end"):
            engine = FakeKakao(crowded_docs()[:20])
            def bad(**kwargs):
                result = engine(**kwargs)
                if kwargs["page"] == 2:
                    if defect == "count":
                        result["total_count"] += 1
                    elif defect == "duplicate":
                        result["places"][0] = crowded_docs()[0]
                    elif defect == "missing":
                        result["places"] = []
                    else:
                        result["is_end"] = False
                return result
            source = KakaoCourseCandidates("JAMSIL", fetch=bad, frames=[], policy=FAST)
            with self.subTest(defect=defect), self.assertRaises(CandidateSearchError) as error:
                source.search(CENTER, 100, "food")
            self.assertEqual(error.exception.reason, "inconsistent_pages")

    def test_changed_child_union_cannot_be_reported_complete(self):
        engine = FakeKakao(crowded_docs())
        def changed(**kwargs):
            result = engine(**kwargs)
            if len(engine.calls) == 1:
                result["total_count"] += 1
            return result
        source = KakaoCourseCandidates("JAMSIL", fetch=changed, frames=[], policy=FAST)
        with self.assertRaises(CandidateSearchError) as error:
            source.search(CENTER, 100, "food")
        self.assertEqual(error.exception.reason, "counts_changed_during_split")

    def test_errors_stop_without_retry_or_hidden_fallback(self):
        for exception, reason in ((PlaceRateLimitError, "rate_limit"), (PlaceConfigurationError, "configuration"),
                                  (PlaceUpstreamError, "upstream_or_invalid_metadata")):
            fetch = Mock(side_effect=exception)
            source = KakaoCourseCandidates("JAMSIL", fetch=fetch, frames=[], policy=FAST)
            with self.subTest(reason=reason), self.assertRaises(CandidateSearchError) as error:
                source.search(CENTER, 100, "food")
            self.assertEqual(error.exception.reason, reason)
            self.assertEqual(fetch.call_count, 1)

    def test_late_response_is_discarded_and_timeout_is_remaining_budget(self):
        now = [0.]
        def slow(**kwargs):
            self.assertEqual(kwargs["timeout"], 1.)
            now[0] += 2
            return {"places": [], "total_count": 0, "pageable_count": 0, "is_end": True}
        source = KakaoCourseCandidates("JAMSIL", fetch=slow, frames=[], clock=lambda: now[0],
                                       policy=replace(FAST, wall_seconds=1))
        with self.assertRaises(CandidateSearchError) as error:
            source.search(CENTER, 100, "food")
        self.assertEqual(error.exception.reason, "time_budget")
        self.assertFalse(source.completed)

    def test_stadium_geometry_and_explicit_tenant_names_remain_separate(self):
        source, _ = provider([document(1, north=0), document(2, north=70),
                              document(3, north=80, name="치킨 잠실야구장점"),
                              document(4, north=85, name="식당 야구장앞점")])
        source.frames = [[(37.4999, 126.9999), (37.5001, 126.9999), (37.5001, 127.0001), (37.4999, 127.0001)]]
        self.assertEqual({p["placeId"] for p in source.search(CENTER, 100, "food")}, {"2", "4"})
        self.assertEqual(source.audit()["excluded_affiliation_count"], 2)

    def test_all_reviewed_ballpark_centers_inside_but_named_neighbors_outside(self):
        from baseball.stadium_locations import reviewed_locations
        from .stadium_facilities import _root
        frames = reviewed_frames("JAMSIL")
        locations = reviewed_locations(_root().parent)
        self.assertEqual(len(frames), 9)
        for code, location in locations.items():
            with self.subTest(code=code):
                self.assertTrue(any(contains(f, location["lat"], location["lng"]) for f in frames))
                for p in location["excluded"]:
                    self.assertFalse(any(contains(f, p["lat"], p["lng"]) for f in frames))

    def test_cafe_and_walk_are_not_restaurant_keyword_shortlists(self):
        docs = [document(1, category="CE7", detail="음식점 > 카페"),
                document(2, detail="음식점 > 제과점", name="빵집"),
                document(3, category="", detail="스포츠,레저 > 공원", name="가상공원"),
                document(4, category="FD6", name="공원식당")]
        source, _ = provider(docs)
        self.assertEqual({p["placeId"] for p in source.search(CENTER, 100, "cafe")}, {"1", "2"})
        self.assertEqual({p["placeId"] for p in source.search(CENTER, 100, "walk")}, {"3"})


class CandidateCellTests(SimpleTestCase):
    def test_strict_read_only_contract_and_rect_forwarding(self):
        payload = {"meta": {"total_count": 1, "pageable_count": 1, "is_end": True}, "documents": [document(1)]}
        with patch("travel.place_service._request_kakao", return_value=payload) as fetch:
            result = search_candidate_cell(rect=circle_box(CENTER, 100), category="FD6", timeout=2)
        self.assertEqual(result["total_count"], 1)
        self.assertIn("rect", fetch.call_args.args[0])
        self.assertNotIn("radius", fetch.call_args.args[0])
        self.assertEqual(fetch.call_args.kwargs["timeout"], 2)

    def test_bad_metadata_missing_counts_and_invalid_rows_are_rejected(self):
        valid = {"total_count": 1, "pageable_count": 1, "is_end": True}
        for meta in ({"is_end": True}, {**valid, "total_count": True}, {**valid, "pageable_count": 46},
                     {**valid, "pageable_count": 2}, {**valid, "is_end": "true"}):
            with patch("travel.place_service._request_kakao", return_value={"meta": meta, "documents": [document(1)]}), \
                    self.assertRaises(PlaceUpstreamError):
                search_candidate_cell(rect=circle_box(CENTER, 100), category="FD6")
        for doc in (document(1, north=500), document(1, category="CE7"), {**document(1), "id": "１２"},
                    {**document(1), "place_url": "https://[bad"}):
            with patch("travel.place_service._request_kakao", return_value={"meta": valid, "documents": [doc]}), \
                    self.assertRaises(PlaceUpstreamError):
                search_candidate_cell(rect=circle_box(CENTER, 100), category="FD6")

    def test_invalid_input_never_calls_api(self):
        for fields in ({"rect": (127, 37.6, 126, 37.5)}, {"rect": (float("nan"), 37, 128, 38)},
                       {"page": 4}, {"timeout": 0}, {"category": "HP8"}):
            with patch("travel.place_service._request_kakao") as fetch, self.assertRaises(PlaceValidationError):
                search_candidate_cell(**{"rect": circle_box(CENTER, 100), "category": "FD6", **fields})
            fetch.assert_not_called()

    @override_settings(KAKAO_REST_API_KEY="offline-test-key")
    def test_429_is_not_retried_or_treated_as_empty(self):
        with patch("travel.place_service.build_opener") as opener:
            opener.return_value.open.side_effect = HTTPError("https://dapi.kakao.com", 429, "limit", {}, None)
            with self.assertRaises(PlaceRateLimitError):
                search_candidate_cell(rect=circle_box(CENTER, 100), category="FD6")
            self.assertEqual(opener.return_value.open.call_count, 1)
