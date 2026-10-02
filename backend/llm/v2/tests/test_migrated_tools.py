"""V2 에 옮겨온 assistant 도구(get_ticket_policy·search_nearby_places·plan_course) 회귀 테스트.

모델은 simulated(ScriptedModel), provider(DB·카카오·V1 코스 엔진)는 stub. 도구 wrapper 는 실제 build_specialized_tools 것.
실행: backend 에서 python -m unittest llm.v2.tests.test_migrated_tools -v
"""
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from llm.tools import assistant
from llm.v2.agent import baseball_sub_agent, chain, place_sub_agent, travel_sub_agent
from llm.v2.middleware import jev_guidelines as classifier
from llm.v2.middleware.dynamic_tools import CAPABILITY_TOOLS, request_args
from llm.v2.tests.test_chain import ScriptedModel, call, decision, fake_tools

NEW = {"stadium_info": "get_ticket_policy", "tourism": "search_nearby_places", "day_plan": "plan_course"}


def tools_with_real_migrated(executed):
    tools = fake_tools(executed)
    for t in assistant.build_specialized_tools():
        if t.name in NEW.values():
            tools[t.name] = t
    return tools


class MigratedToolsTest(unittest.TestCase):
    def run_graph(self, script, capabilities, messages, context=None):
        self.calls = []
        graph = chain.build_graph(ScriptedModel(script=list(script), calls=self.calls), tools_with_real_migrated([]))
        state = {"messages": messages, **({"context": context} if context else {})}
        with patch.object(classifier, "classify", return_value=decision(capabilities=capabilities)):
            return graph.invoke(state)

    def tool_msg(self, out, name):
        return next(m for m in out["messages"] if isinstance(m, ToolMessage) and m.name == name)

    def test_mapping_and_role_allowlists(self):
        for cap, name in NEW.items():
            self.assertIn(name, CAPABILITY_TOOLS[cap])
        self.assertEqual(CAPABILITY_TOOLS["courses"], ("search_courses", "get_course"))
        self.assertIn("get_ticket_policy", baseball_sub_agent.TOOLS)
        self.assertIn("search_nearby_places", travel_sub_agent.TOOLS)
        self.assertIn("plan_course", chain.TOOLS)
        for mod in (baseball_sub_agent, travel_sub_agent, place_sub_agent):
            self.assertNotIn("plan_course", mod.TOOLS)
            self.assertFalse(any(t.startswith("ask_") for t in mod.TOOLS))
        self.assertTrue({"ask_baseball", "ask_travel_research", "ask_place_data"} <= set(CAPABILITY_TOOLS["day_plan"]))

    def test_get_graph_registers_missing_and_keeps_collisions(self):
        import django
        django.setup()  # default 도구 모듈이 Django 모델을 import 한다 (DB 연결은 안 함)
        from llm.tools import create_default_tools
        from llm.tools.knowledge import create_knowledge_tools
        base = {t.name: t for t in (*create_default_tools(), *create_knowledge_tools())}
        chain.get_graph.cache_clear()
        with patch.object(chain, "build_graph", side_effect=lambda m, tb: tb), \
                patch("llm.v2.agent.common.llm", return_value=None):
            registered = chain.get_graph()
        chain.get_graph.cache_clear()
        for name in NEW.values():
            self.assertIn(name, registered)
            self.assertNotIn(name, base)
        for name, t in base.items():  # 겹치는 이름(get_games 등)은 기존 구현·스키마 그대로
            got = registered[name]
            self.assertEqual((got.func.__module__, got.func.__qualname__), (t.func.__module__, t.func.__qualname__))
            self.assertEqual(got.tool_call_schema.model_json_schema(), t.tool_call_schema.model_json_schema())

    def test_ticket_policy_executes_and_next_model_consumes(self):
        rows = {"columns": ["policy_type", "max_tickets"], "rows": [["GENERAL", 4]]}
        with patch.object(assistant, "_run_fixed", return_value=rows) as db:
            out = self.run_graph([call("get_ticket_policy", {"team": "LG"}, "p1"), AIMessage("최대 4매")],
                                 ["stadium_info"], [HumanMessage("LG 예매 몇 장?")])
        self.assertEqual(db.call_args.args[2], {"team": "LG"})
        self.assertIn("max_tickets", self.tool_msg(out, "get_ticket_policy").content)
        self.assertTrue(any(isinstance(m, ToolMessage) and "max_tickets" in m.content for m in self.calls[1]["messages"]))

    def test_nearby_uses_context_stadium_hint(self):
        place = {"placeId": "1", "name": "잠실호텔", "detail": "숙박 > 호텔", "distance": 400, "address": "송파"}
        seen = []
        with patch("llm.v1.rag.nearby.kakao.enabled", return_value=True), \
                patch("llm.v1.rag.nearby.kakao.nearby", side_effect=lambda code, kind: seen.append(code) or [place]):
            out = self.run_graph([call("search_nearby_places", {"kind": "stay"}, "n1"), AIMessage("잠실호텔")],
                                 ["tourism"], [HumanMessage("근처 숙소")], {"stadium": "JAMSIL"})
        self.assertEqual(seen, ["JAMSIL"])
        self.assertIn("잠실호텔", self.tool_msg(out, "search_nearby_places").content)
        self.assertIn("잠실호텔", self.calls[1]["messages"][-1].content)

    def test_new_course_bypasses_legacy_plan_tool_and_keeps_question_history(self):
        got = []

        class Course:
            def stream(self, inputs):
                from llm.v2.course.state import empty_state
                got.append(inputs)
                inputs["course_runtime"]["next_state"] = empty_state()
                yield "검증된 코스 완성"

        with patch("llm.v1.rag.course.agent.answer") as legacy, \
                patch("llm.v2.agent.course_chain.course_chain", Course()):
            out = self.run_graph([], ["day_plan"],
                                 [HumanMessage("잠실 가요"), AIMessage("네"), HumanMessage("경기 전후 코스 짜줘")],
                                 {"stadium": "잠실"})
        self.assertEqual(got[0]["question"], "경기 전후 코스 짜줘")
        self.assertEqual([m.content for m in got[0]["chat_history"]], ["잠실 가요", "네"])
        self.assertEqual(got[0]["context"], {"stadium": "잠실"})
        self.assertEqual(out["messages"][-1].content, "검증된 코스 완성")
        self.assertEqual(self.calls, [])
        legacy.assert_not_called()

    def test_hidden_migrated_tools_rejected(self):
        with patch.object(assistant, "_run_fixed") as db, patch("llm.v1.rag.course.agent.answer") as course:
            out = self.run_graph([call("get_ticket_policy", {"team": "LG"}, "h1"),
                                  call("plan_course", {"request": "x"}, "h2"), AIMessage("순위")],
                                 ["standings"], [HumanMessage("순위")])
        db.assert_not_called()
        course.assert_not_called()
        self.assertEqual({m.status for m in out["messages"] if isinstance(m, ToolMessage)}, {"error"})

    def test_request_state_isolated_across_threads_and_restored(self):
        barrier = threading.Barrier(2)
        before = assistant._STATE.get(None)

        def one(code):
            from llm.tools.assistant import request_state
            with request_state(*request_args({"messages": [HumanMessage(code)], "context": {"stadium": code}})):
                barrier.wait()
                return assistant.state()["hint"], assistant.state()["question"]
        with ThreadPoolExecutor(2) as pool:
            self.assertEqual(list(pool.map(one, ["JAMSIL", "GOCHEOK"])), [("JAMSIL", "JAMSIL"), ("GOCHEOK", "GOCHEOK")])
        self.assertIs(assistant._STATE.get(None), before)

    def test_tourism_instruction_covers_lodging_and_stores(self):
        text = classifier.CAPABILITY_INSTRUCTIONS["tourism"]
        for word in ("숙박", "호텔", "편의점", "상점", "후속"):
            self.assertIn(word, text)
        self.assertIn("search_nearby_places", CAPABILITY_TOOLS["tourism"])


if __name__ == "__main__":
    unittest.main()
