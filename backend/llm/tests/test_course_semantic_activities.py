from unittest.mock import patch
from django.test import SimpleTestCase
from llm.v1.rag.course import agent, editing, memory, slots
from llm.tests.test_course_editing import plan


class SemanticActivitiesTest(SimpleTestCase):
    def test_steak_expression_produces_meal_cafe_and_stay_in_requested_order(self):
        question = "스테이크 썰고 카페 갔다가 구장 가고 이후에 호텔 추천해줘"
        visits = [{"kind": "FOOD", "phase": "BEFORE", "expression": "스테이크 썰고"},
                  {"kind": "CAFE", "phase": "BEFORE", "expression": "카페 갔다가"},
                  {"kind": "SPOT", "phase": "BEFORE", "expression": "구장 가고"},
                  {"kind": "STAY", "phase": "AFTER", "expression": "호텔 추천해줘"}]
        sl = slots.apply_requested_visits(slots.parse(question), visits, question)
        self.assertEqual(agent.plan_steps(sl, True), (["FOOD", "CAFE"], ["STAY"]))

    def test_meal_denial_or_cafe_dessert_does_not_restore_a_meal(self):
        for question, expression in (("밥은 이미 먹었고 카페에서 케이크 먹고 경기 보러 갈래", "카페에서 케이크 먹고"),
                                     ("식사는 빼고 카페만 들를래", "카페만 들를래")):
            visits = [{"kind": "CAFE", "phase": "BEFORE", "expression": expression}]
            sl = slots.apply_requested_visits(slots.parse(question), visits, question)
            self.assertEqual(agent.plan_steps(sl, True), (["CAFE"], []))

    def test_invented_expression_cannot_overwrite_request(self):
        question = "카페만 들렀다 경기 보러 갈래"
        original = slots.parse(question)
        self.assertEqual(slots.apply_requested_visits(original, [
            {"kind": "FOOD", "phase": "BEFORE", "expression": "스테이크"}], question), original)

    def test_public_conversation_passes_semantic_visits_to_generation_without_second_interpreter(self):
        visits = [{"kind": "FOOD", "phase": "BEFORE", "expression": "스테이크 썰고"}]
        parsed = {**plan("new", []), "requested_visits": visits}
        with patch.object(editing, "interpret", return_value=parsed) as interpret, \
                patch.object(agent, "_answer", return_value={"answer": "", "places": []}) as generate:
            agent.answer("스테이크 썰고 경기", hint_stadium="MUNHAK", course_memory=memory.empty())
        self.assertEqual(generate.call_args.kwargs["requested_visits"], visits)
        self.assertEqual(interpret.call_count, 1)

    def test_meal_expression_remains_a_turn_condition_when_model_omits_menu_preference(self):
        parsed = {**plan("new", []), "preferences": [], "forget_conditions": [], "requested_visits": [
            {"kind": "FOOD", "phase": "BEFORE", "expression": "스테이크 썰고"}]}
        with patch.object(agent, "llm") as llm:
            llm.return_value.with_structured_output.return_value.invoke.return_value = parsed
            result = editing.interpret("스테이크 썰고 구장 가자", {"stadiumCode": "MUNHAK", "places": []}, [])
        self.assertIn({"scope": "FOOD", "text": "스테이크 썰고"}, result["request_preferences"])
        saved = editing.update_memory(memory.empty(), result, {"places": []})
        self.assertFalse(any("스테이크" in p["text"] for p in saved["conditions"]))
        self.assertTrue(any("스테이크" in p["text"] for p in editing.request_memory(saved, result)["conditions"]))
