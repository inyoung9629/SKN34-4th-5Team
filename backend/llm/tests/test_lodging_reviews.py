from copy import deepcopy
from datetime import date
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from llm.tests.test_lodging_conditions import CANDIDATE, PLACE, URL, report
from llm.v1.rag.nearby import lodging


def review(text, polarity="positive", day="2026-09-25"):
    return {"requirement_id": "clean", "observed_on": day, "quote": text, "polarity": polarity}


class LodgingReviewTest(SimpleTestCase):
    def test_provider_schema_requires_every_property_including_defaulted_fields(self):
        def check(node):
            if isinstance(node, dict):
                if node.get("type") == "object" and "properties" in node:
                    self.assertEqual(set(node["required"]), set(node["properties"]))
                    self.assertFalse(node["additionalProperties"])
                self.assertNotIn("default", node)
                for value in node.values():
                    check(value)
            elif isinstance(node, list):
                for value in node:
                    check(value)
        check(lodging.search_schema())
        self.assertEqual(set(lodging.search_schema()["properties"]), {"requirements", "properties"})
        self.assertEqual(lodging.search_schema()["properties"]["properties"]["type"], "array")

    def setUp(self):
        clock = patch.object(lodging.timezone, "localdate", return_value=date(2026, 10, 5))
        clock.start()
        self.addCleanup(clock.stop)

    def normalize(self, reviews):
        raw = report()
        raw["requirements"] = [{"id": "clean", "label": "깔끔한 숙소", "basis": "review"}]
        raw["properties"][0]["reviews"] = reviews
        return lodging._normalize([CANDIDATE], raw, {URL})

    def test_recent_independent_positive_reviews_can_support_cleanliness(self):
        result = self.normalize([review("침구와 욕실이 아주 깨끗해서 편하게 쉬었어요"),
                                 review("머리카락이나 먼지가 없고 방 관리가 잘 되어 있습니다", day="2026-09-20")])
        self.assertEqual(result["items"][0]["status"], "match")
        self.assertEqual(lodging.answer_text(result), f"- {PLACE['name']} [야놀자]({URL})")

    def test_source_dotted_dates_are_not_discarded_as_missing_reviews(self):
        result = self.normalize([review("침구와 욕실이 아주 깨끗해서 편하게 쉬었어요", day="2026.09.25"),
                                 review("먼지가 없고 방 관리가 잘 되어 있습니다", day="2026/9/20")])
        self.assertEqual(result["items"][0]["status"], "match")

    def test_hotel_in_business_name_does_not_override_motel_classification(self):
        raw = report()
        raw["requirements"] = [{"id": "type", "label": "호텔"}]
        raw["properties"][0].update(lodging_type="motel", type_evidence="숙소 유형: 모텔",
                                     checks=[{"requirement_id": "type", "status": "match", "evidence": PLACE["name"]}])
        self.assertEqual(lodging._normalize([CANDIDATE], raw, {URL})["items"][0]["status"], "mismatch")
        raw["properties"][0].update(lodging_type="hotel", type_evidence=PLACE["name"])
        self.assertEqual(lodging._normalize([CANDIDATE], raw, {URL})["items"][0]["status"], "unknown")
        raw["properties"][0]["type_evidence"] = "2성급 호텔"
        self.assertEqual(lodging._normalize([CANDIDATE], raw, {URL})["items"][0]["status"], "match")
        raw["requirements"][0]["intent"] = "exclude"
        self.assertEqual(lodging._normalize([CANDIDATE], raw, {URL})["items"][0]["status"], "mismatch")

    def test_stale_future_duplicate_and_advertising_text_do_not_make_a_consensus(self):
        quote = "침구와 욕실이 아주 깨끗해서 편하게 쉬었어요"
        result = self.normalize([review(quote), review(quote, day="2026-09-20"),
                                 review("깔끔한 방에서 기분 좋게 묵었습니다", day="2024-01-01"),
                                 review("욕실도 깨끗하고 정돈되어 있어 만족했어요", day="2027-01-01"),
                                 review("쿠폰으로 예약하면 깨끗한 객실을 이용할 수 있어요")])
        self.assertEqual(result["items"][0]["status"], "unknown")

    def test_repeated_negative_reviews_are_not_overridden_by_good_rating(self):
        result = self.normalize([review("방은 정돈되어 있고 욕실도 깨끗했어요"),
                                 review("침대 아래 먼지와 머리카락이 너무 많았습니다", "negative"),
                                 review("욕실에 곰팡이가 있어서 청소가 아쉬웠어요", "negative")])
        result["items"][0].update(rating=5, reviewCount=2000, weightedScore=4.99)
        self.assertEqual(result["items"][0]["status"], "mismatch")
        self.assertEqual(lodging.recommendations(result), [])

    def test_mixed_reviews_stay_unknown(self):
        result = self.normalize([review("침구와 욕실이 아주 깨끗해서 편하게 쉬었어요"),
                                 review("욕실에 곰팡이가 있어서 청소가 아쉬웠어요", "negative")])
        self.assertEqual(result["items"][0]["status"], "unknown")

    def test_rating_fallback_weights_sample_count_and_never_promotes_unknown(self):
        first, second = deepcopy(CANDIDATE), {**CANDIDATE, "id": "456"}
        raw = report()
        raw["properties"][0]["checks"].pop()
        a = raw["properties"][0]
        a.update(rating=5, rating_scale=5, review_count=1, rating_evidence="5.0(1)")
        b = {**deepcopy(a), "id": "456", "rating": 4.8, "review_count": 500, "rating_evidence": "4.8(500)"}
        raw["properties"].append(b)
        result = lodging._normalize([first, second], raw, {URL})
        selected = lodging.recommendations(result)
        self.assertEqual(selected[0]["id"], "456")
        self.assertEqual(selected[0]["status"], "unknown")
        self.assertEqual(selected[0]["recommendation"], "rating_fallback")
        self.assertIn("미확인", lodging.notice(result))
        self.assertEqual(result["items"][1]["status"], "unknown")

    def test_missing_or_unproven_rating_never_gets_invented(self):
        for data in ({}, {"rating": 5, "review_count": 100, "rating_evidence": "평점이 좋아요"},
                     {"rating": 5, "review_count": 100, "rating_evidence": "4.8(100)"}):
            self.assertFalse(lodging._rating(data))
        self.assertAlmostEqual(lodging._rating({"rating": 9.6, "rating_scale": 10, "review_count": 500,
                                               "rating_evidence": "9.6(500)"})["rating"], 9.6)

    def test_rating_does_not_override_an_unverified_explicit_exclusion(self):
        result = {"items": [{"id": "123", "status": "unknown", "sourceUrl": URL, "reviewCount": 100,
                             "weightedScore": 4.8, "checks": [{"intent": "exclude", "status": "unknown"}]}]}
        self.assertFalse(lodging.recommendations(result))

    def test_next_batch_is_checked_and_stops_when_later_candidate_matches(self):
        places = [{**PLACE, "placeId": str(i)} for i in range(12)]
        def search(batch, *_):
            raw = report()
            raw["properties"] = []
            for p in batch:
                found = {**report()["properties"][0], "id": p["id"]}
                if p["id"] != "5":
                    found["checks"] = []
                raw["properties"].append(found)
            return raw, {URL}
        lookup = Mock(side_effect=search)
        result = lodging.verify(places, "주차와 금연 숙소", _search=lookup)
        self.assertEqual(lookup.call_count, 2)
        self.assertEqual(result["checkedCount"], 8)
        self.assertEqual(lodging.eligible(places, result)[0]["placeId"], "5")

    def test_later_batch_cannot_drop_an_unconfirmed_condition(self):
        fixed = [{"id": "clean", "label": "청결", "basis": "review", "intent": "required"}]
        result = lodging._normalize([CANDIDATE], report(), {URL}, fixed)
        self.assertEqual(result["items"][0]["status"], "unknown")

    def test_api_failure_retains_rating_evidence_from_previous_batch(self):
        raw = report()
        raw["properties"][0].update(rating=4.8, rating_scale=5, review_count=100, rating_evidence="4.8(100)")
        raw["properties"][0]["checks"].pop()
        places = [PLACE] + [{**PLACE, "placeId": str(i)} for i in range(6)]
        lookup = Mock(side_effect=[(raw, {URL}), TimeoutError()])
        result = lodging.verify(places, "주차와 금연 숙소", _search=lookup)
        self.assertEqual(lodging.recommendations(result)[0]["id"], "123")
        self.assertIn("미확인", lodging.notice(result))

    def test_unopened_detail_is_read_before_its_rating_can_be_used(self):
        raw = report()
        raw["properties"][0].update(rating=4.8, rating_scale=5, review_count=200, rating_evidence="4.8(200)")
        fresh = deepcopy(raw)
        fresh["properties"][0].update(rating=4.7, rating_evidence="4.7(200)")
        with patch.object(lodging, "_search", return_value=(raw, set())), \
                patch.object(lodging, "_read_details", return_value=(fresh["properties"], {URL})) as lookup:
            result, sources = lodging.search([CANDIDATE], "숙소", [])
        self.assertEqual(lookup.call_count, 1)
        self.assertEqual(lookup.call_args.args[1], [URL])
        self.assertEqual(lodging._normalize([CANDIDATE], result, sources)["items"][0]["rating"], 4.7)

    def test_open_attempt_without_property_read_does_not_validate_old_search_snippet(self):
        with patch.object(lodging, "_search", return_value=(report(), set())), \
                patch.object(lodging, "_read_details", return_value=([], set())):
            result, sources = lodging.search([CANDIDATE], "숙소", [])
        self.assertEqual(lodging._normalize([CANDIDATE], result, sources)["items"][0]["status"], "unknown")

    def test_first_search_cannot_omit_hotel_requirement(self):
        fixed = [{"id": "hotel", "label": "호텔 유형", "basis": "detail", "intent": "required"}]
        with patch.object(lodging, "requirements", return_value=fixed), \
                patch.object(lodging, "search", return_value=(report(), {URL})):
            result = lodging.verify([PLACE], "깔끔한 호텔 유형 생략 회귀 테스트")
        self.assertEqual(result["requirements"], ["호텔 유형"])
        self.assertEqual(result["items"][0]["status"], "unknown")

    def test_opened_page_missing_from_report_is_read_again(self):
        omitted = {"requirements": report()["requirements"], "properties": []}
        with patch.object(lodging, "_search", return_value=(omitted, {URL})), \
                patch.object(lodging, "_read_details", return_value=(report()["properties"], {URL})) as lookup:
            result, sources = lodging.search([CANDIDATE], "숙소", [])
        self.assertEqual(lookup.call_count, 1)
        self.assertEqual(lodging._normalize([CANDIDATE], result, sources)["items"][0]["status"], "match")

    def test_stay_after_game_is_a_visit_order_not_property_requirement(self):
        result = lodging._normalize([], {}, set(), [{"id": "after", "label": "경기 후 숙박"}])
        self.assertEqual(result["criteria"], [])

    def test_platform_region_prefix_is_allowed_only_with_same_address(self):
        candidate = {"name": "느낌호텔", "address": "인천 남동구 선수촌공원로23번길 10-11"}
        found = {"name": "인천(구월동) 느낌호텔", "address": "인천광역시 남동구 선수촌공원로23번길 10-11"}
        self.assertTrue(lodging.same_property(candidate, found))
        self.assertFalse(lodging.same_property(candidate, {**found, "address": "인천 남동구 선수촌공원로23번길 10-1"}))
        self.assertFalse(lodging.same_property(candidate, {**found, "name": "인천(구월동) 느낌호텔 2호점"}))

    def test_review_quote_must_exist_under_its_real_date_and_type_comes_from_page(self):
        raw = report()["properties"][0]
        raw.update(lodging_type="hotel", type_evidence="상호가 호텔", reviews=[
            review("침구와 욕실이 아주 깨끗해서 편하게 쉬었어요", day="2026-09-25"),
            review("먼지가 많아서 청소 상태가 아쉬웠습니다", "negative", day="2026-09-25"),
            review("침구와 욕실이 아주 깨끗해서 편하게 쉬었어요", day="2025-10-10")])
        body = PLACE["address"] + "\n2026.09.25\n침구와 욕실이 아주 깨끗해서 편하게 쉬었어요\n쿠폰 마감\n2026.10.05(월)\n1박"
        result = lodging._ground_property(raw, {"title": PLACE["name"] + " 모텔 예약, 위치", "body_text": body})
        self.assertEqual(len(result["reviews"]), 1)
        self.assertEqual(result["lodging_type"], "motel")
        self.assertEqual(result["reviews"][0]["observed_on"], "2026-09-25")

    def test_search_model_reviews_cannot_pass_without_independent_body_read(self):
        raw = report()
        raw["properties"][0]["reviews"] = [review("침구와 욕실이 아주 깨끗해서 편하게 쉬었어요")]
        with patch.object(lodging, "_search", return_value=(raw, {URL})), \
                patch.object(lodging, "_read_details", return_value=([], set())):
            result, _ = lodging.search([CANDIDATE], "깔끔한 숙소", [])
        self.assertEqual(result["properties"][0]["reviews"], [])

    def test_review_blocks_are_recent_deduplicated_and_do_not_include_checkin_dates(self):
        body = "2026.10.05(월)\n체크인\n2026.09.25\n침구와 욕실이 깨끗했습니다\n2026.08.02\n욕실이 넓고 아주 깨끗합니다\n2026.09.25\n침구와 욕실이 깨끗했습니다\n쿠폰 마감"
        blocks = lodging._review_blocks(body)
        self.assertEqual([b["date"] for b in blocks], ["2026-09-25", "2026-08-02"])

    def test_review_block_ends_before_payment_ui_and_ai_summary(self):
        body = "2026.09.25\n침구와 욕실이 깨끗했습니다\n결제 혜택\n후기 요약\n여러 이용자가 아주 조용하다고 평가한 숙소"
        self.assertEqual(lodging._review_blocks(body)[0]["text"], "침구와 욕실이 깨끗했습니다")
