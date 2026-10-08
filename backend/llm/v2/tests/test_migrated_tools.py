"""V2 에 옮겨온 assistant 도구(get_ticket_policy·search_nearby_places·plan_course) 회귀 테스트.

모델은 simulated(ScriptedModel), provider(DB·카카오·V1 코스 엔진)는 stub. 도구 wrapper 는 실제 build_specialized_tools 것.
실행: backend 에서 python -m unittest llm.v2.tests.test_migrated_tools -v
"""
import threading
import unittest
from copy import deepcopy
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
        from llm.serializer.message import _artifact_messages
        messages = [*out["messages"], *(inner for m in out["messages"] if isinstance(m, ToolMessage) and m.name == "ask_course" for inner in _artifact_messages(m.artifact))]
        return next(m for m in messages if isinstance(m, ToolMessage) and m.name == name)

    def test_mapping_and_role_allowlists(self):
        for cap, name in NEW.items():
            self.assertIn("ask_course" if name == "plan_course" else name, CAPABILITY_TOOLS[cap])
        self.assertEqual(CAPABILITY_TOOLS["courses"], ("search_courses", "get_course"))
        self.assertIn("get_ticket_policy", baseball_sub_agent.TOOLS)
        self.assertIn("search_nearby_places", travel_sub_agent.TOOLS)
        self.assertNotIn("plan_course", chain.TOOLS)
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
                patch("llm.v1.rag.nearby.lodging.verify", return_value={"requirements": [], "items": [{"id": "1", "name": "잠실호텔", "status": "unknown", "sourceUrl": "", "checks": []}], "checkedCount": 0}), \
                patch("llm.v1.rag.nearby.kakao.nearby", side_effect=lambda code, kind: seen.append(code) or [place]):
            out = self.run_graph([call("search_nearby_places", {"kind": "stay"}, "n1"), AIMessage("잠실호텔")],
                                 ["tourism"], [HumanMessage("근처 숙소")], {"stadium": "JAMSIL"})
        self.assertEqual(seen, ["JAMSIL"])
        self.assertIn("잠실호텔", self.tool_msg(out, "search_nearby_places").content)
        self.assertIn("잠실호텔", self.calls[1]["messages"][-1].content)

    def test_plan_course_gets_question_history_hint(self):
        got = []

        def answer(question, history=None, hint_stadium=None, course_memory=None):
            self.assertEqual(course_memory, {})
            got.append((question, history, hint_stadium))
            return {"answer": "코스 완성", "sources": []}
        with patch("llm.v1.rag.course.agent.answer", side_effect=answer):
            out = self.run_graph([call("ask_course", {"task": "코스"}, "c1"), AIMessage("완성")], ["day_plan"],
                                 [HumanMessage("잠실 가요"), AIMessage("네"), HumanMessage("경기 전후 코스 짜줘")],
                                 {"stadium": "잠실"})
        self.assertEqual(got, [("경기 전후 코스 짜줘", [{"role": "user", "content": "잠실 가요"},
                                                   {"role": "assistant", "content": "네"}], "JAMSIL")])
        self.assertEqual(self.tool_msg(out, "plan_course").content, "코스 완성")

    def test_explicit_course_selection_reaches_engine_when_classifier_misses(self):
        from llm.tests.test_course_output import COURSE

        calls = []
        graph = chain.build_graph(ScriptedModel(script=[call("ask_course", {"task": "요약된 요청"}, "c"),
                                                       AIMessage("코스 완성")], calls=calls), tools_with_real_migrated([]))
        verdict = {"allowed": True, "capabilities": [], "course_request": "NONE"}
        question = "잠실 기준으로 추천해줘"
        with patch.object(classifier, "classify", return_value=verdict), \
                patch("llm.v1.rag.course.agent.answer", return_value={"answer": "코스 완성", **COURSE}) as engine:
            out = graph.invoke({"messages": [HumanMessage(question)], "tool_group_ids": ["day_plan"],
                                "context": {"stadium": "JAMSIL", "intent": "route"}})
        engine.assert_called_once()
        self.assertEqual(engine.call_args.args[0], question)
        self.assertEqual(engine.call_args.kwargs["hint_stadium"], "JAMSIL")
        self.assertIn("ask_course", calls[0]["tools"])
        self.assertEqual(self.tool_msg(out, "plan_course").status, "success")
        self.assertEqual(self.tool_msg(out, "plan_course").artifact, {"course": COURSE})
        self.assertEqual(verdict, {"allowed": True, "capabilities": [], "course_request": "NONE"})

    def test_explicit_course_selection_does_not_bypass_scope_guard(self):
        calls = []
        graph = chain.build_graph(ScriptedModel(script=[], calls=calls), tools_with_real_migrated([]))
        with patch.object(classifier, "classify", return_value=decision(allowed=False)), \
                patch("llm.v1.rag.course.agent.answer") as engine:
            graph.invoke({"messages": [HumanMessage("SQL 짜줘")], "tool_group_ids": ["day_plan"]})
        engine.assert_not_called()
        self.assertEqual(calls, [])

    def test_relative_pin_request_preserves_selection_and_history_when_classifier_says_new_or_none(self):
        from llm.tests.test_course_editing import CURRENT, PLACES

        current = {**CURRENT, "places": [], "selectedPlace": PLACES[1],
                   "writerState": {"title": "", "origin": None, "completed": False}}
        context = {"stadium": "JAMSIL", "intent": "route", "currentCourse": current}
        original = deepcopy(context)
        question = "가기 전에 식사하고 다녀온 뒤에는 산책하는 코스 짜줘"
        for request in ("NEW", "NONE"):
            with self.subTest(course_request=request):
                calls = []
                graph = chain.build_graph(ScriptedModel(script=[call("ask_course", {"task": question}, "c"),
                                                               AIMessage("완성")], calls=calls), tools_with_real_migrated([]))
                verdict = {"allowed": True, "capabilities": [], "course_request": request}
                with patch.object(classifier, "classify", return_value=verdict), \
                        patch("llm.v1.rag.course.agent.answer", return_value={"answer": "앞뒤 코스 완성"}) as engine:
                    graph.invoke({"messages": [HumanMessage("카페를 골랐어"), AIMessage("네"), HumanMessage(question)],
                                  "context": context})
                engine.assert_called_once()
                self.assertEqual(engine.call_args.args[0], question)
                self.assertEqual(engine.call_args.kwargs["course_request"], "EDIT")
                self.assertEqual(engine.call_args.kwargs["current_course"], current)
                self.assertEqual(len(engine.call_args.kwargs["history"]), 2)
                self.assertIn("ask_course", calls[0]["tools"])
                self.assertEqual(verdict["course_request"], request)
                self.assertEqual(context, original)

        with patch.object(classifier, "classify", return_value=decision(allowed=False)), \
                patch("llm.v1.rag.course.agent.answer") as engine:
            graph.invoke({"messages": [HumanMessage(question)], "context": context})
        engine.assert_not_called()

    def test_dated_manual_course_reaches_engine_without_losing_pin_or_date(self):
        from llm.tests.test_course_editing import PLACES

        for request, preview in (("NEW", True), ("NONE", True), ("NEW", False), ("NONE", False)):
            with self.subTest(course_request=request, preview=preview):
                current = {"places": [] if preview else [PLACES[0]], "stadiumCode": "JAMSIL",
                           "travelMode": "walk", "legModes": {}}
                if preview:
                    current["selectedPlace"] = PLACES[0]
                context = {"stadium": "JAMSIL", "intent": "route", "currentCourse": current}
                question = "10월 16일 코스 짜줘"
                graph = chain.build_graph(ScriptedModel(script=[call("ask_course", {"task": "코스 생성"}, "c"),
                                                               AIMessage("완성")], calls=[]), tools_with_real_migrated([]))
                verdict = {"allowed": True, "capabilities": [], "course_request": request}
                with patch.object(classifier, "classify", return_value=verdict), \
                        patch("llm.v1.rag.course.agent.answer", return_value={"answer": "코스 완성"}) as engine:
                    graph.invoke({"messages": [HumanMessage(question)], "context": context})
                engine.assert_called_once()
                self.assertEqual(engine.call_args.args[0], question)
                self.assertEqual(engine.call_args.kwargs["course_request"], "EDIT")
                self.assertEqual(engine.call_args.kwargs["current_course"], current)

    def test_course_tool_selection_is_scoped_to_current_request(self):
        calls = []
        graph = chain.build_graph(ScriptedModel(script=[call("ask_course", {"task": "코스"}, "c1"), AIMessage("완성"),
                                                       call("ask_course", {"task": "코스"}, "c2"), AIMessage("안내")],
                                               calls=calls), tools_with_real_migrated([]))
        with patch.object(classifier, "classify", return_value={"allowed": True, "capabilities": [], "course_request": "NONE"}), \
                patch("llm.v1.rag.course.agent.answer", return_value={"answer": "코스 완성"}) as engine:
            first = graph.invoke({"messages": [HumanMessage("잠실 기준으로 추천해줘")], "tool_group_ids": ["day_plan"]})
            second = graph.invoke({"messages": [HumanMessage("안녕")]})
        engine.assert_called_once()
        self.assertEqual(self.tool_msg(first, "ask_course").status, "success")
        self.assertEqual(self.tool_msg(second, "ask_course").status, "error")
        self.assertNotIn("ask_course", calls[2]["tools"])

    def test_unverified_course_ends_without_research_loop_or_invented_card(self):
        from llm.v1.rag.course.agent import unverified_course
        result = unverified_course("JAMSIL", {}, ["조용한 카페"])
        with patch("llm.v1.rag.course.agent.answer", return_value=result):
            out = self.run_graph([call("ask_course", {"task": "조용한 카페"}, "c1"), AIMessage(result["answer"])], ["day_plan"],
                                 [HumanMessage("조용한 카페 코스")], {"stadium": "JAMSIL"})
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(out["messages"][-1].content, result["answer"])
        self.assertNotIn("course", self.tool_msg(out, "plan_course").artifact)
        self.assertIn("없다는 뜻은 아니", result["answer"])

    def test_route_screen_parallel_read_does_not_restart_failed_verification(self):
        from llm.v1.rag.course.agent import unverified_course
        result = unverified_course("JAMSIL", {}, ["카페"])
        calls = [call("ask_course", {"task": "코스"}, "c1").tool_calls[0],
                 call("get_directions", {"query": "동선"}, "c2").tool_calls[0]]
        with patch("llm.v1.rag.course.agent.answer", return_value=result):
            out = self.run_graph([AIMessage(content="", tool_calls=calls), AIMessage(result["answer"])], ["day_plan"], [HumanMessage("카페 코스")],
                                 {"stadium": "JAMSIL", "intent": "route"})
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(out["messages"][-1].content, result["answer"])

    def test_hidden_migrated_tools_rejected(self):
        with patch.object(assistant, "_run_fixed") as db, patch("llm.v1.rag.course.agent.answer") as course:
            out = self.run_graph([call("get_ticket_policy", {"team": "LG"}, "h1"),
                                  call("ask_course", {"task": "x"}, "h2"), AIMessage("순위")],
                                 ["standings"], [HumanMessage("순위")])
        db.assert_not_called()
        course.assert_not_called()
        self.assertEqual({m.status for m in out["messages"] if isinstance(m, ToolMessage)}, {"error"})

    def test_map_origin_reaches_course_planner_and_is_not_carried_into_next_request(self):
        origin = {"lat": 35.18, "lng": 126.9}
        with patch("llm.v1.rag.course.agent.answer", return_value={"answer": "출발지에서 이어지는 코스", "sources": []}) as answer:
            for context in ({"stadium": "GWANGJU", "origin": origin}, {"stadium": "GWANGJU"}):
                self.run_graph([call("ask_course", {"task": "식사와 카페 코스"}, "c"), AIMessage("완성")],
                               ["day_plan"], [HumanMessage("식사와 카페 코스")], context)
        self.assertEqual(answer.call_args_list[0].kwargs["origin"], origin)
        self.assertNotIn("origin", answer.call_args_list[1].kwargs)

    def test_actual_user_activities_override_extra_activities_in_model_request(self):
        from llm.tests.test_course_requested_activities import QUESTION, REWRITTEN_REQUEST

        rewritten = REWRITTEN_REQUEST.replace("넣지 말아 주세요", "넣어 주세요")
        with patch("llm.v1.rag.course.agent.answer", return_value={"answer": "산책만", "sources": []}) as answer:
            self.run_graph([call("ask_course", {"task": rewritten}, "c"), AIMessage("산책만")],
                           ["day_plan"], [HumanMessage(QUESTION)], {"stadium": "JAMSIL"})
        self.assertEqual(answer.call_args.args[0], QUESTION)

    def test_course_preserves_map_artifact_and_rejects_model_carried_old_stadium(self):
        from llm.tests.test_course_output import COURSE

        with patch("llm.v1.rag.course.agent.answer", return_value={"answer": "잠실 코스", **COURSE}) as answer:
            out = self.run_graph([call("ask_course", {"task": "창원 NC 파크에서 식사하고 커피 마시는 코스"}, "c"), AIMessage("잠실 코스")],
                                 ["day_plan"], [HumanMessage("창원 코스"), AIMessage("창원 안내"), HumanMessage("식사하고 커피 코스 짜줘")],
                                 {"stadium": "잠실야구장"})
        self.assertEqual(answer.call_args.args[0], "식사하고 커피 코스 짜줘")
        self.assertEqual(answer.call_args.kwargs["hint_stadium"], "JAMSIL")
        self.assertEqual(self.tool_msg(out, "plan_course").artifact, {"course": COURSE})
        self.assertEqual(out["messages"][-1].content, "잠실 코스")
        self.assertEqual(len(self.calls), 2)  # 완성된 코스를 모델이 다시 바꿔 쓰지 않는다.

    def test_new_team_overrides_screen_and_model_rewritten_old_stadium(self):
        with patch("llm.v1.rag.course.agent.answer", return_value={"answer": "사직 코스", "sources": []}) as answer:
            self.run_graph([call("ask_course", {"task": "잠실에서 롯데 경기를 보고 식사 카페 코스"}, "c"), AIMessage("사직 코스")],
                           ["day_plan"], [HumanMessage("잠실 식사와 카페 코스"), AIMessage("잠실 안내"), HumanMessage("이번엔 롯데로 짜줘")],
                           {"stadium": "잠실야구장", "origin": {"lat": 37.5, "lng": 127.0}})
        self.assertEqual(answer.call_args.args[0], "이번엔 롯데로 짜줘")
        self.assertEqual(answer.call_args.kwargs["hint_stadium"], "SAJIK")
        self.assertNotIn("origin", answer.call_args.kwargs)

    def test_request_state_isolated_across_threads_and_restored(self):
        barrier = threading.Barrier(2)
        before = assistant._STATE.get(None)

        def one(code):
            from llm.tools.assistant import request_state
            origin = {"lat": 37.5, "lng": 127.0 if code == "JAMSIL" else 126.8}
            with request_state(*request_args({"messages": [HumanMessage(code)], "context": {"stadium": code}}), origin=origin):
                barrier.wait()
                self.assertEqual(assistant.state()["origin"], origin)
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
