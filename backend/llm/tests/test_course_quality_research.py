import json
from urllib.parse import unquote
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase
from llm.v1.rag.course import agent, quality_research as research


class QualityResearchTests(SimpleTestCase):
    def setUp(self):
        self.place = {"placeId": "1", "stadium": "GWANGJU", "name": "임동초밥집", "address": "광주 북구 서림로 90-1"}
        self.url = "https://www.diningcode.com/profile.php?rid=5ZE9xOR5ralS"
        self.profile = {"@type": "FoodEstablishment", "name": "임동초밥집",
                        "aggregateRating": {"ratingValue": "5", "bestRating": 5, "reviewCount": 5},
                        "review": [{"description": "실내가 깔끔하고 좌석이 정돈되어 있어요", "datePublished": "2026-09-01"}], "image": []}
        self.page = {"body_read": True, "title": "임동초밥집 - 광주 스시 - 다이닝코드",
                     "body_text": "5 (5명의\n평가)\n광주광역시 북구 서림로 90-1 1층",
                     "metadata": {"profile": self.profile}}

    def test_public_search_data_discovers_urls_without_executing_javascript(self):
        raw = json.dumps({"list": [{"v_rid": "5ZE9xOR5ralS"}]})
        html = "<script>localStorage.setItem('listData', '" + raw.replace('"', '\\"') + "');throw Error('never run');</script>"
        self.assertEqual(research.metadata(html, "https://www.diningcode.com/list.dc")['profiles'], [self.url])
        html = '<script type="application/ld+json">' + json.dumps(self.profile) + '</script>'
        self.assertEqual(research.metadata(html, self.url)["profile"], self.profile)

    def test_actual_profile_identity_and_rendered_rating_are_required(self):
        reader = Mock()
        reader.read.return_value = self.page
        self.assertEqual(research.profile(self.place, self.url, reader)["ratingCount"], 5)
        for update in ({"title": "다른 초밥집 - 광주"}, {"body_read": False},
                       {"body_text": "5 (5명의 평가) 광주 북구 서림로 90-11"},
                       {"body_text": "4 (5명의 평가) 광주 북구 서림로 90-1"}):
            reader.read.return_value = {**self.page, **update}
            self.assertIsNone(research.profile(self.place, self.url, reader))

    def test_city_and_exact_branch_precede_unscoped_results_without_forcing_neighbourhood(self):
        reader = Mock(read=Mock(return_value={"metadata": {"profiles": [self.url]}}))
        result = research.discover({**self.place, "stadium": "JAMSIL", "name": "미카도스시 잠실새내점"}, reader)
        queries = [unquote(call.args[0]).split("query=")[1] for call in reader.read.call_args_list]
        self.assertEqual(queries, ["서울 미카도스시 잠실새내점", "미카도스시 잠실새내점"])
        self.assertEqual(result, [self.url])

    def test_zero_or_single_rating_stays_unverified(self):
        for count in (0, 1):
            changed = {**self.profile, "aggregateRating": {"ratingValue": 5, "reviewCount": count}}
            reader = Mock(read=Mock(return_value={**self.page, "metadata": {"profile": changed}}))
            self.assertIsNone(research.profile(self.place, self.url, reader))

    def test_page_seen_for_wrong_shop_is_reused_but_identity_is_rechecked(self):
        public_reader = Mock(read=Mock(return_value=self.page))
        reader = research.ProfileReader(public_reader)
        wrong = {**self.place, "name": "다른 초밥집"}
        self.assertIsNone(research.profile(wrong, self.url, reader))
        self.assertEqual(research.profile(self.place, self.url, reader)["ratingCount"], 5)
        self.assertIsNone(research.profile({**self.place, "address": "광주 북구 서림로 999"}, self.url, reader))
        public_reader.read.assert_called_once()
        self.assertTrue(public_reader.read.call_args.kwargs["complete_text"])
        reader.close()
        self.assertEqual(reader.pages, {})
        public_reader.close.assert_called_once()

    def test_review_judgement_requires_its_actual_quote_and_correct_branch(self):
        proof = research.profile(self.place, self.url, Mock(read=Mock(return_value=self.page)))
        def check(**changes):
            row = {"place_id": "1", "status": "acceptable", "basis": "customer_review", "review_index": 0,
                   "evidence": "실내가 깔끔하고 좌석이 정돈되어 있어요", **changes}
            with patch.object(agent, "llm") as model:
                model.return_value.with_structured_output.return_value.invoke.return_value = {"items": [row]}
                return research.interiors([proof])
        self.assertIn("1", check())
        self.assertEqual(check(evidence="음식점이 깨끗하고 신축 건물입니다"), {})
        self.assertEqual(check(place_id="2"), {})
        self.assertEqual(check(basis="photo_review", review_index=-1), {})
        self.assertEqual(check(review_index=7), {})

    def test_review_quote_ignores_line_wrapping_but_keeps_exact_contiguous_words(self):
        raw = "손님 공간이 청\n결하고 좌석도 정\n돈되어 있어요. 음식은 별로였어요."
        proof = {"place": self.place, "url": self.url, "images": [],
                 "reviews": [{"date": "2026-09-01", "text": raw}]}
        def check(quote):
            with patch.object(agent, "llm") as model:
                model.return_value.with_structured_output.return_value.invoke.return_value = {"items": [{
                    "place_id": "1", "status": "acceptable", "basis": "customer_review", "review_index": 0, "evidence": quote}]}
                return research.interiors([proof])
        result = check("손님 공간이 청결하고 좌석도 정돈되어 있어요.")
        self.assertEqual(result["1"]["note"], raw.split(" 음식")[0])
        self.assertEqual(check("손님 공간이 청결하고 좌석도 새것이라 좋아요."), {})
        self.assertEqual(check("손님 공간이 청결하고 음식은 별로였어요."), {})
        self.assertEqual(check("손님 공간이 청결하고 좌석도 정돈되어 있지 않아요."), {})
