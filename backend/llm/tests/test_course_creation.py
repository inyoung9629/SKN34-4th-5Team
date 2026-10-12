"""Creation interpretation shares lodging conditions without weakening verification."""
from unittest.mock import Mock, patch

from django.core.cache import cache
from django.test import SimpleTestCase

from llm.service.chat_runs import Stopped
from llm.tests.test_course_editing import plan
from llm.tests.test_lodging_conditions import PLACE, URL, report
from llm.v1.rag.course import agent, creation, editing, memory
from llm.v1.rag.nearby import lodging


QUESTION = "경기 후 주차 가능한 호텔에서 숙박하는 코스 짜줘"


def creation_result(**changes):
    return {"requested_visits": [{"kind": "STAY", "phase": "AFTER", "expression": "호텔에서 숙박"}],
            "internal_venue_requests": [], "preferences": [], "request_preferences": [],
            "lodging_requirements": [{"id": "parking", "label": "주차 가능"}, {"id": "type", "label": "호텔"}],
            "additional_requests": [], "course_only": True, **changes}


class CreationTest(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_course_controls_remain_in_planning_without_becoming_venue_conditions(self):
        texts = ["2026년 10월 7일 경기 코스", "지도에 찍은 출발지에서 출발", "각 장소는 한 곳씩",
                 "모든 이동은 도보", "모든 장소는 실내", "도보 10분 이내"]
        model = Mock()
        model.with_structured_output.return_value.invoke.return_value = creation_result(
            request_preferences=[{"scope": "ALL", "text": text} for text in texts])
        with patch.object(agent, "llm", return_value=model):
            parsed = creation.interpret(QUESTION, "JAMSIL")
        state = {"conditions": parsed["request_preferences"]}
        self.assertEqual(memory.relevant(state, "FOOD"), texts[-2:])
        self.assertEqual(memory.planning_text(state).splitlines(), texts)
        self.assertEqual([p["scope"] for p in state["conditions"][:4]],
                         ["ITINERARY", "TRAVEL", "ITINERARY", "TRAVEL"])

    def test_new_course_interprets_visits_and_lodging_in_one_call(self):
        model = Mock()
        model.with_structured_output.return_value.invoke.return_value = creation_result()
        with patch.object(agent, "llm", return_value=model) as get_model, patch.object(agent, "_answer", return_value={"answer": "코스", "places": []}) as generate:
            result = agent.answer(QUESTION, hint_stadium="JAMSIL", course_memory=memory.empty(), course_request="NEW")
        get_model.assert_called_once_with(reasoning_effort="medium")
        model.with_structured_output.assert_called_once_with(creation.CreationPlan, method="json_schema")
        model.with_structured_output.return_value.invoke.assert_called_once()
        requirements = generate.call_args.kwargs["lodging_requirements"]
        self.assertEqual([r["label"] for r in requirements], ["주차 가능", "호텔"])
        self.assertEqual(generate.call_args.kwargs["requested_visits"][0]["phase"], "AFTER")
        self.assertTrue(result["courseOnly"])

    def test_simple_visits_use_low_effort_but_explicit_evidence_keeps_medium(self):
        for question, effort in (("카페 갔다가 구장에서 간식 먹고 술집 가고 산책하는 코스", "low"),
                                 ("메가커피 가는 코스", "low"), ("깔끔한 카페 코스", "medium"),
                                 ("주차 가능한 금연 숙박 코스", "medium"),
                                 ("한식 또는 일식 식당 코스", "medium")):
            model = Mock()
            model.with_structured_output.return_value.invoke.return_value = creation_result()
            with self.subTest(question=question), patch.object(agent, "llm", return_value=model) as get_model:
                creation.interpret(question, "JAMSIL")
            get_model.assert_called_once_with(reasoning_effort=effort)

    def test_compact_schema_failure_keeps_existing_interpreter_and_cancellation_does_not_retry(self):
        model = Mock()
        model.with_structured_output.return_value.invoke.return_value = plan("new", [], preferences=[], forget_conditions=[])
        with patch.object(creation, "interpret", side_effect=ValueError), patch.object(agent, "llm", return_value=model):
            result = editing.interpret(QUESTION, {"stadiumCode": "JAMSIL", "places": []}, [], new_course=True)
        self.assertEqual(result["operation"], "new")
        self.assertNotIn("lodging_requirements", result)
        with patch.object(creation, "interpret", side_effect=Stopped), patch.object(agent, "llm") as fallback:
            with self.assertRaises(Stopped):
                editing.interpret(QUESTION, {"stadiumCode": "JAMSIL", "places": []}, [], new_course=True)
        fallback.assert_not_called()

    def test_separate_request_disables_direct_answer_even_with_inconsistent_boolean(self):
        with patch.object(editing, "interpret", return_value={**plan("new", []), "course_only": True,
                "additional_requests": ["볼넷이 뭔지도 알려줘"], "lodging_requirements": []}), \
                patch.object(agent, "_answer", return_value={"answer": "코스", "places": []}):
            result = agent.answer(QUESTION, hint_stadium="JAMSIL", course_request="NEW")
        self.assertFalse(result["courseOnly"])

    def test_fixed_conditions_skip_extraction_but_keep_grounding_and_cache_separation(self):
        with patch.object(lodging, "requirements") as extract, patch.object(lodging, "search", return_value=(report(), {URL})) as search:
            first = lodging.verify([PLACE], QUESTION, fixed_requirements=report()["requirements"])
            changed = lodging.verify([PLACE], QUESTION, fixed_requirements=[{"id": "pets", "label": "반려동물 허용"}])
        extract.assert_not_called()
        self.assertEqual(search.call_count, 2)
        self.assertEqual(first["items"][0]["status"], "match")
        self.assertEqual(changed["items"][0]["status"], "unknown")
        self.assertEqual(changed["items"][0]["checks"][0]["condition"], "반려동물 허용")

    def test_explicit_empty_requirements_do_not_trigger_a_second_extractor(self):
        with patch.object(lodging, "requirements") as extract, patch.object(lodging, "search", return_value=(report(), {URL})):
            result = lodging.verify([PLACE], "숙소 코스", fixed_requirements=[])
        extract.assert_not_called()
        self.assertEqual(result["criteria"], [])
        self.assertEqual(result["items"][0]["status"], "match")

    def test_negation_optional_and_review_basis_survive_creation_schema(self):
        requirements = [{"id": "pets", "label": "반려동물 허용 또는 금연 객실", "intent": "required"},
                        {"id": "motel", "label": "모텔", "intent": "exclude"},
                        {"id": "quiet", "label": "조용함", "basis": "review", "intent": "optional"}]
        model = Mock()
        model.with_structured_output.return_value.invoke.return_value = creation_result(lodging_requirements=requirements)
        with patch.object(agent, "llm", return_value=model):
            parsed = creation.interpret("모텔은 제외하고 반려동물 허용 또는 금연 숙소 코스. 가능하면 조용한 곳", "JAMSIL")
        self.assertEqual(parsed["lodging_requirements"][0]["label"], requirements[0]["label"])
        self.assertEqual(parsed["lodging_requirements"][1]["intent"], "exclude")
        self.assertEqual(parsed["lodging_requirements"][2]["basis"], "review")
        self.assertEqual(parsed["lodging_requirements"][2]["intent"], "optional")

    def test_missing_or_combined_hotel_type_uses_dedicated_extractor_without_guessing(self):
        model = Mock()
        repaired = [{"id": "parking", "label": "주차"}, {"id": "hotel", "label": "호텔"}]
        for label in ("주차", "주차 가능한 호텔"):
            model.with_structured_output.return_value.invoke.return_value = creation_result(lodging_requirements=[{"id": "parking", "label": label}])
            with patch.object(agent, "llm", return_value=model), patch.object(lodging, "requirements", return_value=repaired) as extract:
                result = creation.interpret(QUESTION, "JAMSIL")
            extract.assert_called_once_with(QUESTION, [])
            self.assertEqual(result["lodging_requirements"], repaired)
