from copy import deepcopy
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from preprocessing.read_myseatcheck_menus import consensus, compile_catalogue
from travel.stadium_food import food_candidates, matching_menu_items
from llm.v1.rag.course import editing, venue_policy, evidence_memory


def photo(items, status="MENU_READABLE"):
    return {"status": status, "note": "", "items": items}


def item(name="아메리카노", price=4000, option=""):
    return {"name": name, "option": option, "priceWon": price, "priceText": str(price) if price else ""}


class MenuPhotoValidationTests(SimpleTestCase):
    def test_uncertain_price_is_null_and_uncertain_name_is_omitted(self):
        result = consensus(photo([item(), item("콜라")]), photo([item(price=4500), item("코카콜라")]))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "아메리카노")
        self.assertIsNone(result[0]["priceWon"])
        self.assertEqual(consensus(photo([item()]), photo([], "NO_MENU")), [])

    def test_same_readable_name_survives_disagreeing_option_without_invented_price(self):
        result = consensus(photo([item(option="ICE")]), photo([item(option="아이스")]))
        self.assertEqual(result[0]["name"], "아메리카노")
        self.assertEqual(result[0]["option"], "")
        self.assertIsNone(result[0]["priceWon"])

    def test_multiple_sizes_are_not_combined_and_zero_or_boolean_prices_are_not_trusted(self):
        self.assertEqual(consensus(photo([item(option="S"), item(option="L", price=5000)]), photo([item()])), [])
        for price in (0, True, -1, 2000000):
            result = consensus(photo([item(price=price)]), photo([item(price=price)]))
            self.assertIsNone(result[0]["priceWon"])

    def test_each_store_uses_only_its_own_photos_and_conflicting_prices_stay_unknown(self):
        stores = [{"record_id": "a", "stadium_code": "TEST", "store_facility": "같은상호", "source_location": "1루",
                   "source_url": "https://myseatcheck.com/a/", "imageUrls": ["photo-a", "photo-b"]},
                  {"record_id": "b", "stadium_code": "TEST", "store_facility": "같은상호", "source_location": "3루",
                   "source_url": "https://myseatcheck.com/b/", "imageUrls": []}]
        reads = {url: {"imageUrl": url, "firstRead": photo([item(price=price)]), "secondRead": photo([item(price=price)])}
                 for url, price in (("photo-a", 4000), ("photo-b", 4500))}
        catalogue, audit = compile_catalogue(stores, reads)
        self.assertEqual(len(catalogue["records"]), 1)
        saved = catalogue["records"][0]["items"][0]
        self.assertIsNone(saved["priceWon"])
        self.assertEqual(saved["priceStatus"], "CONFLICTING_PHOTOS")
        self.assertEqual(audit["records"][1]["status"], "NO_PHOTOS")
        self.assertEqual(len(saved["observations"]), 2)


class MenuNameMatchingTests(SimpleTestCase):
    def test_korean_and_english_churros_share_only_search_spelling(self):
        saved = {"name": "OAKLAND CHURROS DIP", "priceWon": 10000}
        place = {"menuEvidence": {"items": [saved]}}
        for query in ("츄러스", "추러스", "츄로스", "추로스", "churro", "CHURROS"):
            with self.subTest(query=query):
                self.assertEqual(matching_menu_items(place, query), [saved])
        self.assertEqual(saved["name"], "OAKLAND CHURROS DIP")
        self.assertEqual(matching_menu_items(place, "초코 츄러스"), [])
        korean = {"menuEvidence": {"items": [{"name": "오리지널 츄러스"}]}}
        self.assertEqual(matching_menu_items(korean, "churros"), korean["menuEvidence"]["items"])

    def test_similar_english_words_do_not_invent_a_menu_match(self):
        place = {"menuEvidence": {"items": [{"name": "Churrasco"}, {"name": "churrospice"}]}}
        self.assertEqual(matching_menu_items(place, "츄러스"), [])

    def test_existing_korean_menu_spellings_still_match(self):
        for name, query in (("자장면", "짜장면"), ("돈가스", "돈까스"), ("돈카츠", "돈까스")):
            with self.subTest(name=name):
                place = {"menuEvidence": {"items": [{"name": name}]}}
                self.assertEqual(matching_menu_items(place, query), place["menuEvidence"]["items"])


class InternalMenuSearchTests(SimpleTestCase):
    def setUp(self):
        source = next(p for p in food_candidates("GWANGJU") if p["name"] == "BHC치킨")
        self.source = source
        self.menu = {"facilityId": source["placeId"].split(":")[1], "stadium": "GWANGJU", "store": source["name"],
                     "sourceUrl": source["placeUrl"], "imageUrl": "https://myseatcheck.com/wp-content/uploads/test.webp",
                     "checkedAt": "2026-10-07", "reviewStatus": "VISION_DOUBLE_READ", "items": [item()]}
        self.patch = patch("travel.stadium_food.menu_catalogue", return_value={self.menu["facilityId"]: self.menu})
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.anchor = {"lat": source["lat"], "lng": source["lng"]}

    def test_menu_can_cross_display_category_without_kakao_and_permission_expires(self):
        question = "구장 내부에서 아메리카노 마시자"
        invoke = Mock()
        with venue_policy.request_policy(question, "GWANGJU", [{"category": "CAFE", "expression": question}]), \
                patch("llm.v1.rag.course.agent.invoke_domain_tool", invoke):
            rows = editing.candidates({**self.anchor, "category": "CAFE"}, self.anchor, {"query": "아메리카노"}, [])
            found = venue_policy.filter_candidates(rows)
            matching = next(p for p in found if p["placeId"] == self.source["placeId"])
            self.assertEqual(matching["category"], "CAFE")
            self.assertEqual(matching["sourceCategory"], "FOOD")
            req = evidence_memory.Requirement(term="아메리카노", attribute="menu", intent="required", group="1")
            with patch.object(evidence_memory, "requirements", return_value=[req]):
                self.assertTrue(evidence_memory.collected_candidates([matching], [question]))
        self.assertEqual(venue_policy.filter_candidates([matching]), [])
        invoke.assert_not_called()

    def test_unrequested_menu_and_forged_menu_do_not_reclassify_a_store(self):
        question = "구장 내부 카페"
        place = {**self.source, "category": "CAFE", "_internal_menu_query": "아메리카노"}
        with venue_policy.request_policy(question, "GWANGJU", [{"category": "CAFE", "expression": question}]):
            self.assertEqual(venue_policy.filter_candidates([place]), [])
        question = "구장 내부에서 망고빙수 먹자"
        place.update(_internal_menu_query="망고빙수", menuEvidence={"items": [item("망고빙수")]})
        with venue_policy.request_policy(question, "GWANGJU", [{"category": "CAFE", "expression": question}]):
            self.assertEqual(venue_policy.filter_candidates([place]), [])

    def test_menu_metadata_must_match_the_canonical_store_and_page(self):
        self.menu["sourceUrl"] = "https://myseatcheck.com/another-store/"
        source = next(p for p in food_candidates("GWANGJU") if p["placeId"] == self.source["placeId"])
        self.assertNotIn("menuEvidence", source)
