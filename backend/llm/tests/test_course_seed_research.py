from unittest.mock import patch
from django.test import SimpleTestCase
from llm.v1.rag.course import visit_requests, place_quality, editing, memory


class SeedResearchTests(SimpleTestCase):
    def test_external_scope_duplicate_keeps_the_full_expression_and_other_conditions(self):
        expression = "구장 밖 양식 식당에서 식사하고"
        raw = {"kind": "FOOD", "phase": "BEFORE", "expression": expression,
               "query": "양식", "conditions": ["구장 밖", "조용함", "구장 밖 도보 10분 이내"]}
        visit = visit_requests.normalize(expression, [raw])[0]
        self.assertFalse(visit["internal"])
        self.assertEqual(visit["conditions"], ["조용함", "구장 밖 도보 10분 이내", expression])
        raw["expression"] = "양식 식당"
        self.assertIn("구장 밖", visit_requests.normalize("양식 식당", [raw])[0]["conditions"])

    def test_only_first_explicit_visit_defers_research_until_origin_search(self):
        point = {"lat": 37.51, "lng": 127.08}
        def visit(i, kind, query, phase="BEFORE"):
            return {"id": str(i), "kind": kind, "query": query, "phase": phase,
                    "expression": query or "카페", "conditions": [], "internal": False}
        def policies(visits, origin):
            actual = []
            def candidates(*args):
                actual.append((args[2]["query"], place_quality._FRESH.get()))
                return []
            with patch.object(editing, "candidates", side_effect=candidates):
                visit_requests.Selector(visits, memory.empty(), "JAMSIL").search(point, origin=origin)
            self.assertTrue(place_quality._FRESH.get())
            return actual
        first = [visit(1, "FOOD", "양식"), visit(2, "CAFE", "")]
        self.assertEqual(policies(first, point), [("양식", False), ("", False)])
        self.assertEqual(policies(first, None), [("양식", True), ("", True)])
        self.assertEqual(policies(first[::-1], point), [("양식", True), ("", False)])
        self.assertEqual(policies([visit(1, "FOOD", "자장면"), visit(2, "CAFE", "빙수")], point),
                         [("자장면", True), ("빙수", True)])
        self.assertEqual(policies([visit(1, "FOOD", "양식", "AFTER")], point), [("양식", True)])
