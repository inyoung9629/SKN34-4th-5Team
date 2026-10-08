from unittest.mock import patch

import httpx
from django.test import SimpleTestCase

from llm.v1.rag.course import evidence_memory as evidence, grounding
from travel.public_page_reader import PublicReader


def claim(**changes):
    return evidence.Finding.model_validate({
        "place_id": "fixture:menu", "term": "돈까스", "attribute": "menu", "name": "가상 식당",
        "address": "서울 송파구 올림픽로 10", "url": "https://www.diningcode.com/profile.php?rid=fixture",
        "kind": "menu_listing", "polarity": "positive", "basis": "menu_listing", "body_read": True,
        "evidence": "돈까스를 팝니다", "observed_on": "", "general_context": True,
        "promotion": "unknown", "review_category": "food", **changes})


def page(body):
    return {"body_read": True, "title": "가상 식당 - 잠실 음식점", "body_text": "가상 식당\n서울특별시 송파구 올림픽로 10\n" + body}


class CourseGroundingTests(SimpleTestCase):
    def test_known_quality_profile_skips_search_only_after_actual_menu_verification(self):
        from llm.v1.rag.course import place_quality
        f = claim()
        f._body_verified = True
        candidate = {"placeId": f.place_id, "name": f.name, "address": f.address}
        req = evidence.Requirement(term=f.term, attribute="menu", intent="required", group="meal")
        with place_quality.request_scope("JAMSIL"), patch.object(place_quality, "resolve", return_value={"reviewUrl":f.url}), \
                patch.object(grounding, "verify", return_value=[f]) as verify, patch.object(evidence, "_search_once") as search:
            result, urls, calls = evidence.search([candidate], [req])
        self.assertEqual(result, [f])
        self.assertEqual(urls, {f.url})
        self.assertEqual(calls, 0)
        self.assertFalse(verify.call_args.args[0][0].body_read)
        search.assert_not_called()
        with place_quality.request_scope("JAMSIL"), patch.object(place_quality, "resolve", return_value={"reviewUrl":f.url}), \
                patch.object(grounding, "verify", return_value=[]), patch.object(evidence, "_search_once", return_value=([],set(),1,[])) as search:
            result, _, calls = evidence.search([candidate], [req])
        search.assert_called_once()
        self.assertEqual(result, [])
        self.assertEqual(calls, 1)

    def test_metadata_hook_on_public_search_page_does_not_claim_body_was_read(self):
        def handle(request):
            if request.url.path == "/robots.txt":
                return httpx.Response(404)
            return httpx.Response(200, headers={"content-type":"text/html"}, text='<script>public-data</script>')
        with httpx.Client(transport=httpx.MockTransport(handle)) as client:
            reader = PublicReader(client=client, dns_check=lambda _: True, gap=0)
            row = reader.read('https://www.diningcode.com/list.dc?query=fixture', [], metadata_extractor=lambda html, url: {'found':'public-data' in html})
        self.assertTrue(row['metadata']['found'])
        self.assertFalse(row['body_read'])

    def test_gwangju_provider_prefix_matches_full_legacy_address_without_relaxing_identity(self):
        address = "전남광주통합특별시 북구 중흥로73번길 8"
        self.assertTrue(grounding.address_in_body(address, "광주광역시 북구 중흥로73번길 8 1층"))
        self.assertTrue(grounding.address_in_body("광주 북구 중흥로73번길 8", address))
        for other in ("광주 북구 중흥로73번길 80", "광주 북구 중흥로73번길 8-1",
                      "광주 남구 중흥로73번길 8", "경기 광주시 중흥로73번길 8"):
            self.assertFalse(grounding.address_in_body(address, other))
        self.assertFalse(grounding.address_in_body("전남광주통합특별시 해남군 중앙로 1", "광주 해남군 중앙로 1"))
        finding = claim(name="가상 국밥집", address=address, term="국밥")
        document = {"body_read": True, "title": "가상 국밥집 - 광주 음식점",
                    "body_text": "광주광역시 북구 중흥로73번길 8\n메뉴정보\n모둠국밥\n방문자 리뷰"}
        self.assertEqual(grounding.supported_quote(finding, document), "모둠국밥")

    def test_address_boundaries_distinguish_building_number_from_floor_and_hyphen(self):
        self.assertTrue(grounding.address_in_body("경기 수원시 장안구 경수대로927번길 17", "경기도 수원시 장안구 경수대로927번길 17 1층"))
        self.assertFalse(grounding.address_in_body("서울 송파구 올림픽로 10", "서울 송파구 올림픽로 100"))
        for number in ("10-1", "10 - 1"):
            self.assertFalse(grounding.address_in_body("서울 송파구 올림픽로 10", "서울 송파구 올림픽로 " + number))
        self.assertFalse(grounding.address_in_body("서울 송파구 올림픽로 10-1", "서울 송파구 올림픽로 101"))

    def test_tags_reviews_other_branches_and_missing_body_are_not_menu_evidence(self):
        for document in (page("검색 태그 돈까스\n메뉴정보\n우동\n방문자 리뷰\n돈까스도 먹고 싶다"),
                         {**page("메뉴정보\n돈까스"), "title": "다른 식당"},
                         {**page("메뉴정보\n돈까스"), "body_text": "가상 식당\n서울 송파구 올림픽로 100\n메뉴정보\n돈까스"},
                         {**page("메뉴정보\n돈까스"), "body_read": False}):
            self.assertEqual(grounding.supported_quote(claim(), document), "")

    def test_menu_name_is_derived_from_actual_menu_section_without_model_quote_or_price(self):
        document = page("검색 태그\n우동\n메뉴정보\n돈가스 12000원\n방문자 리뷰\n맛있었음")
        self.assertEqual(grounding.supported_quote(claim(), document), "돈가스")
        for text in ("돈까스 판매 중단", "돈까스 품절", "계절 한정 돈까스"):
            self.assertEqual(grounding.supported_quote(claim(), page("메뉴정보\n" + text)), "")

    def test_no_match_in_a_different_menu_section_or_from_missing_second_condition(self):
        document = page("메뉴정보\n돈까스\n방문자 리뷰\n옆 식당 냉모밀 추천")
        self.assertEqual(grounding.supported_quote(claim(term="냉모밀"), document), "")

    def test_similar_menu_names_and_neighbor_recommendations_do_not_prove_requested_dish(self):
        for text in ("스테이크 파히타", "스테이크 덮밥", "함박스테이크", "스테이크 버거"):
            self.assertEqual(grounding.supported_quote(claim(term="스테이크"), page("메뉴정보\n" + text)), "")
        self.assertEqual(grounding.supported_quote(claim(term="짬뽕"), page("메뉴정보\n짬뽕전골\n23000 원")), "")
        self.assertEqual(grounding.supported_quote(claim(), page("메뉴정보\n돈까스 소스\n3000 원")), "")
        self.assertEqual(grounding.supported_quote(claim(), page("메뉴정보\n우동\n메뉴 더보기\n비슷한 맛집\n돈까스")), "")

    def test_public_reader_body_is_required_even_when_model_claims_to_have_read(self):
        f = claim()
        with evidence.request_budget():
            budget = evidence._BUDGET.get()
            budget["deadline"] = grounding.time.monotonic() + 30
            with patch.object(grounding, "PublicReader") as reader:
                reader.return_value.read.return_value = page("메뉴정보\n돈까스")
                result = grounding.verify([f], {f.url}, budget)
                self.assertTrue(result[0]._body_verified)
                self.assertFalse(f._body_verified)
                grounding.verify([f], {f.url}, budget)
                reader.return_value.read.assert_called_once()
        reader.return_value.close.assert_called_once()
        self.assertNotIn("_body_verified", evidence.Finding.model_json_schema()["properties"])

    def test_reader_refuses_private_dns_robots_blocks_and_credentials_without_bypass(self):
        requests = []

        def handle(request):
            requests.append(str(request.url))
            return httpx.Response(200, text="User-agent: *\nDisallow: /", headers={"content-type": "text/plain"})

        with httpx.Client(transport=httpx.MockTransport(handle)) as client:
            reader = PublicReader(client=client, dns_check=lambda _: False, gap=0)
            self.assertFalse(reader.read(claim().url, ["돈까스"])["body_read"])
            self.assertEqual(requests, [])
            reader = PublicReader(client=client, dns_check=lambda _: True, gap=0)
            self.assertEqual(reader.read(claim().url, ["돈까스"])["status"], "robots_disallowed")
            self.assertEqual(len(requests), 1)
            self.assertFalse(reader.read("https://user:password@www.diningcode.com/private", [])["body_read"])
            self.assertEqual(len(requests), 1)

    def test_reader_drops_scripts_before_matching_and_never_follows_cross_host_redirect(self):
        def handle(request):
            if request.url.path == "/robots.txt":
                return httpx.Response(404)
            if request.url.path == "/redirect":
                return httpx.Response(302, headers={"location": "https://127.0.0.1/private"})
            return httpx.Response(200, headers={"content-type": "text/html"}, text=(
                "<title>가상 식당</title><h1>가상 식당</h1><p>서울 송파구 올림픽로 10</p>"
                "<div>메뉴정보</div><p>우동</p><script>돈까스</script><p>" + "설명 " * 100 + "</p>"))
        with httpx.Client(transport=httpx.MockTransport(handle)) as client:
            reader = PublicReader(client=client, dns_check=lambda _: True, gap=0)
            document = reader.read(claim().url, ["돈까스"])
            self.assertTrue(document["body_read"])
            self.assertNotIn("돈까스", document["body_text"])
            self.assertEqual(reader.read("https://www.diningcode.com/redirect", [])["status"], "redirect_not_followed")

    def test_search_variants_do_not_change_required_terms_or_add_different_foods(self):
        self.assertEqual(grounding.query_variants("돈까스"), ["돈까스", "돈가스", "돈카츠"])
        self.assertEqual(grounding.query_variants("냉모밀"), ["냉모밀", "냉소바"])
        self.assertEqual(grounding.query_variants("짬뽕"), ["짬뽕"])

    def test_jajang_spelling_and_menu_variations_are_found_in_the_actual_menu(self):
        self.assertEqual(grounding.query_variants("자장면"), ["자장면", "짜장면", "짜장", "자장"])
        for requested in ("자장면", "짜장면", "짜장", "자장"):
            for menu in ("짜장면", "자장면", "유니짜장면", "간짜장", "삼선자장"):
                with self.subTest(requested=requested, menu=menu):
                    self.assertEqual(grounding.supported_quote(claim(term=requested), page("메뉴정보\n" + menu + " 8000원\n방문자 리뷰")), menu)
        self.assertNotIn("짜장면면", grounding.query_variants("자장면"))

    def test_jajang_variants_do_not_accept_rice_sauce_reviews_or_other_shops(self):
        for menu in ("짜장밥", "자장 소스", "짜장면 소스", "짜장라면", "짜장면 판매 중단"):
            self.assertEqual(grounding.supported_quote(claim(term="자장면"), page("메뉴정보\n" + menu)), "")
        self.assertEqual(grounding.supported_quote(claim(term="자장면"), page("메뉴정보\n마라탕\n방문자 리뷰\n짜장면 먹고 싶다")), "")
        self.assertEqual(grounding.supported_quote(claim(term="자장면"), {**page("메뉴정보\n짜장면"), "title": "다른 식당"}), "")

    def test_exact_spelling_alias_is_verified_but_menu_variants_keep_semantic_check(self):
        for menu, semantic_calls in (("짜장면", 0), ("유니짜장면", 1)):
            with evidence.request_budget(), patch.object(grounding, "PublicReader") as reader, \
                    patch.object(grounding, "semantic_checks", return_value={0}) as semantic:
                budget = evidence._BUDGET.get()
                budget["deadline"] = grounding.time.monotonic() + 90
                reader.return_value.read.return_value = page("메뉴정보\n" + menu)
                f = claim(term="자장면")
                verified = grounding.verify([f], {f.url}, budget)
                self.assertEqual(len(verified), 1)
                self.assertEqual(verified[0].term, "자장면")
                self.assertTrue(verified[0]._body_verified)
                self.assertEqual(semantic.call_count, semantic_calls)

    def test_abbreviated_branch_title_requires_matching_address_and_branch_number(self):
        self.assertEqual(grounding.supported_quote(claim(name="가상 식당 잠실점"), page("메뉴정보\n돈까스")), "돈까스")
        self.assertEqual(grounding.supported_quote(claim(name="가상 식당 2호점"),
                         {**page("메뉴정보\n돈까스"), "title": "가상 식당 1호점 - 잠실"}), "")

    def test_exact_quote_still_needs_semantic_support_for_the_requested_trait(self):
        f = claim(term="조용함", attribute="review_feature", kind="customer_review", basis="customer_experience",
                  observed_on="2026-10-05", evidence="커피가 맛있어서 다시 방문하고 싶다", promotion="not_disclosed")
        with evidence.request_budget(), patch.object(grounding, "PublicReader") as reader, \
                patch.object(grounding, "semantic_checks", return_value=set()) as semantic:
            budget = evidence._BUDGET.get()
            budget["deadline"] = grounding.time.monotonic() + 90
            reader.return_value.read.return_value = page("실제 이용 후기 2026-10-05\n" + f.evidence)
            self.assertEqual(grounding.verify([f], {f.url}, budget), [])
            self.assertEqual(budget["grounding"][0]["status"], "semantic_unverified")
        semantic.assert_called_once()

    def test_later_candidate_batches_keep_their_own_body_read_allowance(self):
        with evidence.request_budget(), patch.object(grounding, "PublicReader") as reader:
            budget = evidence._BUDGET.get()
            budget["deadline"] = grounding.time.monotonic() + 90
            reader.return_value.read.return_value = page("메뉴정보\n우동")
            for batch in range(3):
                claims = [claim(url=f"https://www.diningcode.com/profile.php?rid=fixture-{batch}-{i}") for i in range(6)]
                self.assertEqual(grounding.verify(claims, {f.url for f in claims}, budget), [])
                self.assertEqual(len(budget["pages"]), 4 * (batch + 1))
            self.assertEqual(reader.return_value.read.call_count, 12)
