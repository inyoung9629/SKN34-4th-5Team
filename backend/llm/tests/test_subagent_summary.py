from django.test import SimpleTestCase

from llm.serializer.message import _public_tool, public_frame, tool_summary


class SubAgentSummaryTest(SimpleTestCase):
    def test_summary_is_separate_from_task_and_public(self):
        args = {"task": "SECRET_TASK 상세 지시", "summary": "  두산  다음 경기 확인 "}
        self.assertEqual(tool_summary("ask_baseball", args), "두산 다음 경기 확인")
        self.assertIsNone(tool_summary("ask_baseball", {"task": "SECRET_TASK"}))  # legacy: task 로 대체하지 않음
        self.assertIsNone(tool_summary("ask_baseball", {"summary": 3}))
        self.assertIsNone(tool_summary("get_games", args))
        self.assertEqual(len(tool_summary("ask_baseball", {"summary": "가" * 99})), 40)
        item = _public_tool("c1", "ask_baseball", "running", None, "SECRET_TASK", tool_summary("ask_baseball", args))
        _, public = public_frame("tool", item, privileged=False)
        self.assertEqual(public["summary"], "두산 다음 경기 확인")
        self.assertNotIn("title", public)
        _, done = public_frame("done", {"tools": [item], "steps": []}, privileged=False)
        self.assertEqual(done["tools"][0], {k: v for k, v in item.items() if k != "title"})
        self.assertNotIn("summary", _public_tool("c2", "ask_baseball", "running", None, "t"))

    def test_task_reaches_specialist_unchanged(self):
        from unittest.mock import MagicMock
        from llm.v2.agent import sub_agents
        agent = MagicMock()
        agent.stream.return_value = iter(())
        ask = sub_agents._delegate("ask_baseball", "d", agent)
        runtime = MagicMock(state={}, tool_call_id="c1")
        ask.func(task="원문 작업", runtime=runtime, summary="요약")
        messages = agent.stream.call_args.args[0]["messages"]
        self.assertEqual(messages[0].content, "원문 작업")

    def test_summary_description_is_model_visible(self):
        from unittest.mock import MagicMock
        from langchain_core.utils.function_calling import convert_to_openai_tool
        from llm.v2.agent import sub_agents
        params = convert_to_openai_tool(sub_agents._delegate("ask_baseball", "d", MagicMock()))["function"]["parameters"]
        desc = params["properties"]["summary"]["description"]
        self.assertIn("공개", desc); self.assertIn("40자", desc); self.assertIn("비밀", desc)
        self.assertEqual(params["required"], ["task"])
        self.assertNotIn("description", params["properties"]["task"])
