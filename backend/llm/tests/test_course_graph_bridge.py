"""실제 main → specialist → main 그래프와 체크포인트 투영. provider만 fake."""
from copy import deepcopy
from unittest.mock import patch

from django.test import SimpleTestCase
from .test_v2_chat import CheckpointTestCase
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from llm.serializer.message import project_history
from llm.v1.rag.course.memory import restore
from llm.v2.agent import chain
from llm.v2.middleware import jev_guidelines
from llm.v2.tests.test_chain import ScriptedModel, call
from llm.v2.tests.test_migrated_tools import tools_with_real_migrated
from .test_course_output import COURSE


class CourseGraphBridgeTests(SimpleTestCase):
    def run_course(self, script, *, request="EDIT", context=None, memory=None, outcome=None):
        calls = []
        graph = chain.build_graph(ScriptedModel(script=script, calls=calls), tools_with_real_migrated([]))
        state = {"messages": [HumanMessage("이전 질문", id="old"), AIMessage("이전 답"), HumanMessage("카페만 바꿔줘", id="q")],
                 "context": context or {"stadium": "JAMSIL"}, "course_memory": memory or {"conditions": []}}
        verdict = {"allowed": True, "capabilities": ["day_plan"], "course_request": request}
        result = outcome or {**deepcopy(COURSE), "answer": "검증된 코스", "courseMemory": {"conditions": [], "current": {"places": COURSE["places"]}}}
        with patch.object(jev_guidelines, "classify", return_value=verdict) as jev, patch("llm.v1.rag.course.agent.answer", return_value=result) as engine:
            events = list(graph.stream(state, stream_mode="updates", subgraphs=True))
        messages = list(state["messages"])
        for ns, data in events:
            if not ns:
                for update in data.values():
                    if isinstance(update, dict):
                        messages.extend(update.get("messages") or [])
        answer = messages[-1]
        answer.id = "a"
        return messages[-4:], calls, jev, engine, events

    def test_main_final_called_once_after_specialist_engine_and_duplicate_blocked(self):
        messages, calls, jev, engine, events = self.run_course([
            call("ask_course", {"task": "모델이 임의로 요약한 지시"}, "c"),
            call("ask_course", {"task": "다시 조사"}, "duplicate"), AIMessage("메인 최종 안내"),
        ])
        jev.assert_called_once()
        engine.assert_called_once()
        self.assertEqual(engine.call_args.args[0], "카페만 바꿔줘")
        self.assertEqual(engine.call_args.kwargs["course_request"], "EDIT")
        self.assertEqual(len(calls), 3)
        self.assertNotIn("ask_course", calls[1]["tools"])
        self.assertNotIn("plan_course", calls[0]["tools"])
        self.assertEqual(messages[-1].content, "메인 최종 안내")
        self.assertTrue(any(ns and any(isinstance(m, ToolMessage) and m.name == "plan_course" for u in data.values() if isinstance(u, dict) for m in u.get("messages", [])) for ns, data in events))

    def test_same_batch_duplicate_keeps_first_course_delegation(self):
        batch = AIMessage("", tool_calls=[
            call("ask_course", {"task": "첫 코스"}, "first").tool_calls[0],
            call("ask_course", {"task": "중복 코스"}, "duplicate").tool_calls[0],
        ])
        messages, calls, jev, engine, events = self.run_course([batch, AIMessage("메인 최종 안내")])
        jev.assert_called_once()
        engine.assert_called_once()
        self.assertEqual(len(calls), 2)
        inner = [m for ns, data in events if ns for update in data.values() if isinstance(update, dict)
                 for m in update.get("messages", []) if isinstance(m, ToolMessage) and m.name == "plan_course"]
        self.assertEqual(len(inner), 1)
        self.assertEqual(inner[0].status, "success")
        results = {m.tool_call_id: m for m in calls[-1]["messages"]
                   if isinstance(m, ToolMessage) and m.name == "ask_course"}
        self.assertEqual(results["first"].status, "success")
        self.assertEqual(results["first"].content, "검증된 코스")
        self.assertEqual(results["duplicate"].status, "error")
        self.assertIn("Tool call limit exceeded", results["duplicate"].content)
        self.assertNotIn("ask_course", calls[-1]["tools"])
        self.assertEqual(messages[-1].content, "메인 최종 안내")

    def test_context_and_server_memory_survive_edit_and_public_projection_restores(self):
        memory = {"conditions": [{"text": "조용한 곳", "scope": "CAFE"}], "locked": [], "rejected": []}
        context = {"stadium": "JAMSIL", "origin": {"lat": 37.5, "lng": 127.0}, "routePath": [{"lat": 37.5, "lng": 127.0}], "currentCourse": {**COURSE, "places": [{**p, "label": str(i + 1)} for i, p in enumerate(COURSE["places"])]}}
        _, calls, _, engine, _ = self.run_course([call("ask_course", {"task": "다른 구장"}, "c"), AIMessage("설명")], context=context, memory=memory)
        self.assertEqual(engine.call_args.kwargs["course_memory"], memory)
        self.assertEqual(engine.call_args.kwargs["origin"], context["origin"])
        self.assertEqual(engine.call_args.kwargs["route_path"], context["routePath"])
        self.assertEqual(engine.call_args.kwargs["current_course"], context["currentCourse"])
        tool = next(m for m in calls[-1]["messages"] if isinstance(m, ToolMessage) and m.name == "ask_course")
        records = [HumanMessage("카페만 바꿔줘", id="q"), call("ask_course", {"task": "카페"}, "c"), tool, AIMessage("설명", id="a")]
        turns = {"q": {"status": "completed", "answer_id": "a"}}
        item = project_history(records, turns)[-1]
        self.assertEqual(item["course"]["places"], COURSE["places"])
        self.assertEqual([t["parent_id"] for t in item["tools"]], [None, "c"])
        self.assertTrue(restore(records, turns).get("current"))
        for status in ("failed", "cancelled"):
            self.assertEqual(restore(records, {"q": {"status": status}}), {})
        self.assertEqual(restore(records, {"q": {"status": "completed", "answer_deleted": True}}), {})
        self.assertNotIn("detail", item["tools"][0])
        self.assertIn("detail", project_history(records, turns, detail=True)[-1]["tools"][0])

    def test_new_discards_old_course_conditions_but_keeps_screen_route(self):
        context = {"stadium": "JAMSIL", "origin": {"lat": 37.5, "lng": 127.0}, "routePath": [{"lat": 37.5, "lng": 127.0}], "currentCourse": {**COURSE, "places": [{**p, "label": str(i + 1)} for i, p in enumerate(COURSE["places"])]}}
        _, _, _, engine, _ = self.run_course([call("ask_course", {"task": "새 코스"}, "c"), AIMessage("새 안내")], request="NEW", context=context, memory={"conditions": [{"text": "옛 조건"}]})
        self.assertEqual(engine.call_args.kwargs["history"], [])
        self.assertEqual(engine.call_args.kwargs["course_memory"], {"conditions": [], "rejected": [], "locked": []})
        self.assertNotIn("current_course", engine.call_args.kwargs)
        self.assertEqual(engine.call_args.kwargs["route_path"], context["routePath"])

    def test_actual_sse_inner_tool_parent_matches_course_specialist(self):
        from llm.service.chat_v2 import _frames
        calls = []
        graph = chain.build_graph(ScriptedModel(script=[call("ask_course", {"task": "수정"}, "c"), AIMessage("최종 안내")], calls=calls), tools_with_real_migrated([]))
        with patch.object(chain, "get_graph", return_value=graph), patch.object(jev_guidelines, "classify", return_value={"allowed": True, "capabilities": ["day_plan"]}), patch("llm.v1.rag.course.agent.answer", return_value={**COURSE, "answer": "코스"}):
            frames = list(_frames({"messages": [HumanMessage("코스")], "context": {"stadium": "JAMSIL"}}, {"messages": [], "answer": ""}))
        inner = [data for event, data in frames if event == "tool" and data["tool_name"] == "plan_course"]
        self.assertEqual([data["status"] for data in inner], ["running", "completed"])
        self.assertEqual([data.get("parent_id") for data in inner], ["c", "c"])
        self.assertFalse(any("args" in data or "result" in data for event, data in frames if event == "tool"))

    def test_executed_failure_consumes_budget_but_duplicate_denial_does_not_and_next_human_resets(self):
        from llm.v2.middleware.dynamic_tools import DynamicToolMiddleware
        denied = ToolMessage("duplicate", name="ask_course", tool_call_id="duplicate", status="error")
        failed = ToolMessage("failed", name="ask_course", tool_call_id="first", status="error", artifact=[])
        self.assertFalse(DynamicToolMiddleware.course_called({"messages": [HumanMessage("코스"), denied]}))
        self.assertTrue(DynamicToolMiddleware.course_called({"messages": [HumanMessage("코스"), failed, denied]}))
        self.assertFalse(DynamicToolMiddleware.course_called({"messages": [HumanMessage("코스"), failed, HumanMessage("다음 코스")]}))

    def test_successful_new_with_duplicate_denial_still_commits_reset(self):
        from llm.service.chat_v2 import _frames
        batch = AIMessage("", tool_calls=[call("ask_course", {"task": "코스"}, "first").tool_calls[0],
                                         call("ask_course", {"task": "중복"}, "duplicate").tool_calls[0]])
        graph = chain.build_graph(ScriptedModel(script=[batch, AIMessage("새 코스 안내")], calls=[]), tools_with_real_migrated([]))
        run = {"messages": [], "answer": ""}
        with patch.object(chain, "get_graph", return_value=graph), \
                patch.object(jev_guidelines, "classify", return_value={"allowed": True, "capabilities": ["day_plan"], "course_request": "NEW"}), \
                patch("llm.v1.rag.course.agent.answer", return_value={**COURSE, "answer": "코스"}):
            list(_frames({"messages": [HumanMessage("새 코스")], "context": {"stadium": "JAMSIL"}}, run))
        self.assertTrue(run["course_history_reset"])

    def test_specialist_failure_is_nonfatal_without_restorable_artifact(self):
        calls = []
        graph = chain.build_graph(ScriptedModel(script=[call("ask_course", {"task": "수정"}, "c"), AIMessage("기존 코스는 유지하고 실패를 안내")], calls=calls), tools_with_real_migrated([]))
        with patch.object(jev_guidelines, "classify", return_value={"allowed": True, "capabilities": ["day_plan"], "course_request": "EDIT"}), patch("llm.v1.rag.course.agent.answer", side_effect=RuntimeError("private error")):
            result = graph.invoke({"messages": [HumanMessage("카페 바꿔줘")], "context": {"stadium": "JAMSIL"}})
        self.assertEqual(len(calls), 2)
        tool = next(m for m in result["messages"] if isinstance(m, ToolMessage))
        self.assertIn("[조회 실패]", tool.content)
        self.assertNotIn("private error", tool.content)
        from llm.v2.agent.course_output import course_artifacts
        self.assertEqual(list(course_artifacts([tool])), [])


class CoursePostgresBridgeTests(CheckpointTestCase):
    def test_failed_new_and_edit_preserve_memory_after_real_graph_save_reload(self):
        from llm.models import ChatSession
        from llm.service.chat_thread import ChatThread
        from llm.service import chat_v2
        from llm.v1.rag.course.memory import snapshot
        for request in ("NEW", "EDIT"):
            with self.subTest(request=request):
                session = ChatSession.objects.create(guest="19191919-1919-4919-8919-191919191919")
                thread = ChatThread(session.id)
                current = snapshot(deepcopy(COURSE))
                saved = {"conditions": [{"scope": "CAFE", "text": "조용한 곳"}], "locked": [], "rejected": [],
                         "current": current, "undo": {"before": deepcopy(current)}}
                old = HumanMessage("이전 코스", id="old-h")
                thread.update([old, ToolMessage("코스", name="plan_course", tool_call_id="old-t", artifact={"course_memory": saved}),
                               AIMessage("기존 코스 완료", id="old-a")], {old.id: {"status": "completed", "answer_id": "old-a"}})
                prefix, turns, human = thread.ask("코스 요청")
                calls = []
                batch = AIMessage("", tool_calls=[call("ask_course", {"task": "코스"}, "first").tool_calls[0],
                                                 call("ask_course", {"task": "중복"}, "duplicate").tool_calls[0]])
                graph = chain.build_graph(ScriptedModel(script=[batch, AIMessage("기존 코스는 유지하고 실패를 안내")], calls=calls), tools_with_real_migrated([]))
                with patch.object(chain, "get_graph", return_value=graph), \
                        patch.object(jev_guidelines, "classify", return_value={"allowed": True, "capabilities": ["day_plan"], "course_request": request}), \
                        patch("llm.v1.rag.course.agent.answer", side_effect=RuntimeError("private error")) as engine:
                    frames = [f for f in chat_v2._stream_turn(thread, prefix, turns, human, {"stadium": "JAMSIL"}, None) if f is not None]
                records, final_turns = ChatThread(session.id).state()
                self.assertEqual(restore(records, final_turns), saved)
                self.assertEqual(final_turns[human.id]["status"], "completed")
                self.assertEqual(frames[-1][0], "done")
                self.assertTrue(any(m.id == old.id for m in records))
                engine.assert_called_once()
                self.assertEqual(len(calls), 2)
                self.assertNotIn("ask_course", calls[-1]["tools"])
                tools = frames[-1][1]["tools"]
                self.assertTrue(tools)
                self.assertTrue(all(t["status"] == "failed" for t in tools))
                self.assertEqual(sorted(t["tool_name"] for t in tools), ["ask_course", "ask_course", "plan_course"])
                self.assertTrue(all(not ({"args", "result", "detail"} & t.keys()) for t in tools))
                self.assertNotIn("private error", str(frames))
                outer = next(m for m in records if isinstance(m, ToolMessage) and m.tool_call_id == "first")
                self.assertEqual(outer.status, "error")
                from llm.serializer.message import _artifact_messages
                self.assertEqual(_artifact_messages(outer.artifact)[-1].status, "error")

    def test_two_turns_edit_delete_and_next_turn_restore_only_valid_prefix(self):
        from llm.models import ChatSession
        from llm.service.chat_thread import ChatThread
        session = ChatSession.objects.create(guest="98989898-9898-4989-8989-989898989898")
        thread = ChatThread(session.id)
        for index in (1, 2):
            prefix, turns, human = thread.ask(f"질문 {index}")
            inner = ToolMessage("코스", name="plan_course", tool_call_id=f"inner{index}", artifact={"course": COURSE, "course_memory": {"conditions": [], "marker": index}})
            nested = ToolMessage("코스", name="ask_course", tool_call_id=f"c{index}", artifact=[call("plan_course", {"request": "원문"}, f"inner{index}"), inner])
            answer = AIMessage("안내", id=f"answer{index}")
            thread.update([human, call("ask_course", {"task": "원문"}, f"c{index}"), nested, answer], {human.id: {"status": "completed", "answer_id": answer.id}})
        records, turns = thread.state()
        self.assertEqual(restore(records, turns)["marker"], 2)
        second = [m for m in records if isinstance(m, HumanMessage)][-1]
        prefix, kept, _ = thread.edit(second.id, "새 수정")
        self.assertEqual(restore(prefix, kept)["marker"], 1)
        thread.delete_message("answer1")
        prefix, turns, _ = thread.ask("다음 질문")
        self.assertEqual(restore(prefix, turns), {})
