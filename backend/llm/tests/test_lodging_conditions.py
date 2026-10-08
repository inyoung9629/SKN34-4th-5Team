import json
from contextlib import ExitStack
from unittest.mock import Mock, patch

from django.core.cache import cache
from django.test import SimpleTestCase

from llm.tools import assistant
from llm.v1.rag.course import agent as course
from llm.v1.rag.nearby import lodging


URL = "https://nol.yanolja.com/stay/domestic/3012501"
PLACE = {"placeId": "123", "name": "잠실 포레스타 호텔", "address": "서울 송파구 백제고분로7길 23-6",
         "lat": 37.51, "lng": 127.08, "distance": 600, "detail": "숙박 > 호텔", "placeUrl": "https://place.map.kakao.com/123"}
CANDIDATE = {"id": "123", "name": PLACE["name"], "address": PLACE["address"]}


def report():
    return {"requirements": [{"id": "parking", "label": "주차 가능"}, {"id": "smoke", "label": "금연 객실"}],
            "properties": [{"id": "123", "name": PLACE["name"], "address": "서울특별시 송파구 백제고분로7길 23-6",
                            "url": URL, "checks": [{"requirement_id": "parking", "status": "match", "evidence": "주차가능"},
                                                    {"requirement_id": "smoke", "status": "match", "evidence": "금연객실"}]}]}


class LodgingConditionsTest(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def normalize(self, raw=None, sources=None):
        return lodging._normalize([CANDIDATE], raw or report(), {URL} if sources is None else sources)

    def test_all_conditions_need_evidence_and_same_property(self):
        result = self.normalize()
        self.assertEqual(result["items"][0]["status"], "match")
        picked = lodging.eligible([PLACE], result)
        self.assertEqual(picked[0]["placeUrl"], URL)
        self.assertEqual(picked[0]["placeId"], PLACE["placeId"])
        self.assertEqual(picked[0]["lat"], PLACE["lat"])

    def test_missing_condition_is_unknown_not_match_or_mismatch(self):
        raw = report()
        raw["properties"][0]["checks"].pop()
        result = self.normalize(raw)
        self.assertEqual(result["items"][0]["status"], "unknown")
        self.assertEqual(result["items"][0]["checks"][1]["status"], "unknown")
        self.assertEqual(lodging.eligible([PLACE], result), [])
        self.assertIn("금연 객실", lodging.notice(result))

    def test_explicit_conflicting_condition_excludes_property(self):
        raw = report()
        raw["properties"][0]["checks"][0].update(status="mismatch", evidence="주차 불가")
        self.assertEqual(self.normalize(raw)["items"][0]["status"], "mismatch")

    def test_other_branch_same_name_and_street_is_not_attached(self):
        for change in ({"address": "서울 송파구 백제고분로7길 23-5"},
                       {"name": "잠실 포레스타 2 호텔"},
                       {"address": "부산 송파구 백제고분로7길 23-6"}):
            with self.subTest(change=change):
                raw = report()
                raw["properties"][0].update(change)
                item = self.normalize(raw)["items"][0]
                self.assertEqual(item["status"], "unknown")
                self.assertEqual(item["sourceUrl"], "")

    def test_guessed_url_without_search_provenance_is_rejected(self):
        item = self.normalize(sources=set())["items"][0]
        self.assertEqual(item["status"], "unknown")
        self.assertFalse(item["sourceUrl"])

    def test_source_links_only_point_to_public_property_pages(self):
        for bad in ("https://nol.yanolja.com.evil.test/stay/domestic/1", "javascript:alert(1)",
                    "https://nol.yanolja.com@evil.test/stay/domestic/1", "https://nol.yanolja.com:443/stay/domestic/1",
                    "https://nol.yanolja.com/search", "http://nol.yanolja.com/stay/domestic/1",
                    "https://nol.yanolja.com/stay/domestic/1\\evil"):
            self.assertEqual(lodging.source_url(bad), "")
        self.assertEqual(lodging.source_url(URL + "?checkInDate=2026-10-05&price=90000"), URL)

    def test_prices_and_availability_are_never_exposed_as_evidence(self):
        raw = report()
        raw["requirements"].append({"id": "price", "label": "10만원 이하"})
        raw["properties"][0]["checks"][0]["evidence"] = "주차 가능, 50,000원, 예약 가능, 잔여 2개"
        raw["properties"][0]["price"] = 50000
        result = self.normalize(raw)
        text = lodging.answer_text(result)
        self.assertEqual(result["items"][0]["status"], "unknown")
        for forbidden in ("50,000", "10만원", "예약 가능", "잔여"):
            self.assertNotIn(forbidden, text)
        self.assertNotIn("price", json.dumps(result))

    def test_verified_reason_is_link_only_and_internal_room_evidence_is_retained(self):
        raw = report()
        raw["properties"][0]["checks"][1]["evidence"] = "디럭스 트윈 객실에 한해 금연"
        result = self.normalize(raw)
        self.assertEqual(lodging.reason(result["items"][0]), "")
        self.assertIn("디럭스 트윈", result["items"][0]["checks"][1]["evidence"])
        self.assertNotIn("디럭스", lodging.answer_text(result))
        self.assertIn(URL, lodging.answer_text(result))

    def test_search_timeout_does_not_claim_incompatible(self):
        result = lodging.verify([PLACE], "주차 숙소", _search=Mock(side_effect=TimeoutError()))
        self.assertEqual(result["checkedCount"], 0)
        self.assertEqual(result["items"][0]["status"], "unknown")
        self.assertIn("확인하지 못", result["notice"])
        self.assertFalse(lodging.eligible([PLACE], result))

    def test_shortlist_is_bounded_and_only_user_history_is_sent(self):
        search = Mock(return_value=(report(), {URL}))
        candidates = [{**PLACE, "placeId": str(i)} for i in range(12)]
        lodging.verify(candidates, "주차는 필요 없고 금연만", [
            {"role": "user", "content": "주차되는 호텔"}, {"role": "assistant", "content": "가격은 50000원"}], _search=search)
        self.assertEqual(len(search.call_args.args[0]), 4)
        self.assertEqual(search.call_count, 3)
        self.assertEqual(search.call_args.args[2], ["주차되는 호텔"])

    def test_repeated_request_reuses_verification_and_condition_change_does_not(self):
        with patch.object(lodging, "search", return_value=(report(), {URL})) as search, \
                patch.object(lodging, "requirements", return_value=report()["requirements"]):
            lodging.verify([PLACE], "주차 숙소")
            lodging.verify([PLACE], "주차 숙소")
            self.assertEqual(search.call_count, 1)
            lodging.verify([PLACE], "금연 숙소")
            self.assertEqual(search.call_count, 2)

    def test_nearby_tool_uses_real_question_instead_of_model_keyword(self):
        question = "주차 가능하고 금연 객실인 호텔, 숙박 비용은 제외"
        result = self.normalize()
        with assistant.request_state("JAMSIL", question), patch.object(lodging, "verify", return_value=result) as verify:
            text = assistant.search_nearby_places("stay", keyword="호텔", _nearby=lambda *_: [PLACE])
        self.assertEqual(verify.call_args.args[1], question)
        self.assertIn(URL, text)
        self.assertIn('"status": "match"', text)

    def test_search_uses_domain_filter_and_bounded_output_and_accounts_for_usage(self):
        response = Mock(status="completed", output_text=json.dumps(report()),
                        usage=Mock(input_tokens=123, output_tokens=45))
        response.output = [Mock(model_dump=lambda: {"type": "web_search_call", "action": {"type": "open_page", "url": URL}})]
        client = Mock()
        client.responses.create.return_value = response
        with patch("openai.OpenAI", return_value=client), patch.object(lodging.usage, "record_external") as meter, \
                patch.object(lodging, "_read_details", return_value=([], set())):
            raw, sources = lodging.search([CANDIDATE], "금연 호텔", [])
        args = client.responses.create.call_args.kwargs
        self.assertEqual(args["tools"][0]["filters"]["allowed_domains"], ["nol.yanolja.com"])
        self.assertEqual(args["tool_choice"], "required")
        self.assertLessEqual(args["max_output_tokens"], 6000)
        self.assertFalse(args["store"])
        self.assertEqual(sources, {URL})
        meter.assert_called_once_with(123, 45)


class LodgingCourseTest(SimpleTestCase):
    def run_course(self, status, origin=None):
        stadium = {"key": "STADIUM", "name": "잠실야구장", "lat": 37.512, "lng": 127.0719,
                   "category": "STADIUM", "distance": 0, "detail": "", "placeId": None, "address": "서울 송파구", "placeUrl": "", "doc_id": "stadium"}
        cafe = {**PLACE, "key": "cafe", "category": "CAFE", "name": "카페", "detail": "카페", "dist": .1, "doc_id": "cafe"}
        game = {"date": "2027-10-06", "time": "18:30", "home": "LG", "away": "삼성", "status": "scheduled"}
        checked = {"requirements": ["금연 객실"], "items": [{"id": "123", "name": PLACE["name"], "status": status,
                   "checks": [{"condition": "금연 객실", "status": status, "evidence": "디럭스 금연객실" if status == "match" else ""}], "sourceUrl": URL}], "checkedCount": 1}
        mocks = {"load_schedule": ({}, 1), "find_game": (game, False, [game]), "embed_many": ([.1], [.2]),
                 "stadium_anchor": stadium, "_live_candidates": ([cafe], {}), "search_places": [],
                 "invoke_domain_tool": {}, "call_llm": ('{"course": []}', 0)}
        with ExitStack() as stack:
            for name, result in mocks.items():
                stack.enter_context(patch.object(course, name, return_value=result))
            stack.enter_context(patch.object(course.kakao, "nearby", return_value=[PLACE]))
            stack.enter_context(patch.object(lodging, "verify", return_value=checked))
            stack.enter_context(patch.object(course.transport, "info", return_value={"mode": "walk", "label": "도보", "taxi": False, "lines": []}))
            return course.answer("경기 전에 카페, 경기 후 금연 객실 숙소까지 코스 짜줘", hint_stadium="JAMSIL", origin=origin)

    def test_verified_stay_reaches_map_answer_and_saved_course(self):
        result = self.run_course("match")
        stay = next(p for p in result["places"] if p["category"] == "STAY")
        self.assertEqual(stay["placeUrl"], URL)
        for text in (stay["reason"], result["answer"], result["coursePayload"]["content"]):
            self.assertNotIn("디럭스 금연객실", text)
        self.assertIn(URL, result["answer"])
        self.assertEqual(result["places"][-1]["category"], "STAY")

    def test_unknown_stay_cannot_reappear_through_fallback_or_origin_search(self):
        for origin in (None, {"lat": 37.51, "lng": 127.073}):
            result = self.run_course("unknown", origin)
            self.assertNotIn("STAY", [p["category"] for p in result["places"]])
            self.assertIn("미확인 조건", result["answer"])

    def test_verified_pool_prevents_new_unchecked_lodging_search(self):
        with patch.object(course, "invoke_domain_tool") as invoke:
            self.assertEqual(course._kakao_step("STAY", PLACE, 2500, PLACE, {"verified_stays": []}), [])
        invoke.assert_not_called()
