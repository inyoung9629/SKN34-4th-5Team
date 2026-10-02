"""Six-domain tool registry and retrieval compatibility checks."""
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from llm.tools import DOMAIN_TOOL_NAMES, create_default_tools, create_domain_tools
from llm.tools import knowledge


class ToolPackagesTest(SimpleTestCase):
    def test_factories_preserve_canonical_schemas_and_sql(self):
        from llm.tools import baseball, community, stadium, travel, weather

        factories = (
            baseball.create_baseball_domain_tools, stadium.create_stadium_tools,
            travel.create_travel_tools, community.create_community_tools,
            weather.create_weather_tools,
        )
        partition = [tool for factory in factories for tool in factory()]
        self.assertEqual(len(partition), 22)
        self.assertEqual({tool.name for tool in partition}, set(DOMAIN_TOOL_NAMES))
        self.assertEqual(tuple(tool.name for tool in create_domain_tools()), DOMAIN_TOOL_NAMES)
        default = create_default_tools()
        self.assertEqual(tuple(tool.name for tool in default[-2:]),
                         ("get_baseball_schema", "execute_baseball_select"))
        self.assertEqual(len({tool.name for tool in default}), len(default))
        tools = {tool.name: tool for tool in default}
        self.assertEqual(tools["get_games"].args_schema.model_fields["start_date"].annotation.__name__, "date")
        self.assertIn("sql", tools["execute_baseball_select"].args_schema.model_fields)
        self.assertNotIn("sql", tools["get_standings"].args_schema.model_fields)

    def test_knowledge_retrieval_uses_vector_then_keyword_fallback(self):
        with (
            patch.object(knowledge, "vector_search", return_value=[]),
            patch.object(knowledge, "transform_query", return_value="잠실 대중교통"),
            patch.object(knowledge, "keyword_fallback_search", return_value=[
                {"content": "구장 교통", "metadata": {"stadium_code": "JAMSIL", "category": "TRANSPORT"}}
            ]) as fallback,
        ):
            result = knowledge.search_documents(
                "잠실 교통", {"stadium_code": "JAMSIL"},
            )
        self.assertEqual(result["search_method"], "keyword_fallback")
        self.assertEqual(result["documents"][0]["content"], "구장 교통")
        fallback.assert_called_once_with("잠실 대중교통", "JAMSIL", ["TRANSPORT"], None, 20)

    def test_knowledge_tool_registration_and_venue_sources(self):
        from llm.v1.rag.domain_tools import tools_for
        names = [tool.name for tool in tools_for("venue")]
        self.assertEqual(len(names), len(set(names)))
        self.assertIn("search_documents_tool", names)
        self.assertIn("search_kbo_documents", names)
        registry = {tool.name: tool for tool in tools_for("venue")}
        self.assertEqual(len(names), 29)
        self.assertFalse(any(name.startswith("legacy_") for name in names))
        self.assertIn("team", registry["get_games"].args_schema.model_fields)
        self.assertNotIn("start_date", registry["get_games"].args_schema.model_fields)
        self.assertIn("start_date", {tool.name: tool for tool in create_domain_tools()}["get_games"].args_schema.model_fields)
        self.assertIn("sql", registry["execute_baseball_select"].args_schema.model_fields)
        self.assertIs(next(tool for tool in tools_for("venue") if tool.name == "search_documents_tool"),
                      knowledge.search_documents_tool)
        context = {"slots": {"stadium_code": "JAMSIL"}}
        token = knowledge.search_context.set(context)
        try:
            with patch.object(knowledge, "search_documents", return_value={
                "documents": [{"content": "주차 안내", "metadata": {"stadium_code": "JAMSIL"}}],
                "search_method": "vector",
            }) as search:
                result = knowledge.search_documents_tool.invoke({"query": "주차"})
        finally:
            knowledge.search_context.reset(token)
        self.assertIn("주차 안내", result)
        self.assertEqual(context["last"]["search_method"], "vector")
        search.assert_called_once_with("주차", context["slots"])

    def test_kbo_tool_keeps_assistant_state_and_sources(self):
        state = {"hint": "JAMSIL", "tools": [], "sources": []}
        token = knowledge.assistant_context.set(state)
        try:
            with patch.object(knowledge, "search_kbo_rows", return_value=[{
                "content": "공식 좌석 안내", "doc_id": "doc-1", "category": "SEAT",
                "stadium": "JAMSIL", "status": "CONFIRMED",
            }]) as search:
                text = knowledge.search_kbo_documents("좌석")
        finally:
            knowledge.assistant_context.reset(token)
        self.assertIn("공식 좌석 안내", text)
        self.assertEqual(state["tools"], ["rag"])
        self.assertEqual(state["sources"], [{
            "doc_id": "doc-1", "grade": "OFFICIAL", "category": "SEAT", "stadium": "JAMSIL",
        }])
        search.assert_called_once_with("좌석", "JAMSIL", None, _search=None, _embed=None)

    def test_kbo_tool_without_request_keeps_new_state(self):
        token = knowledge.assistant_context.set(None)
        try:
            with patch.object(knowledge, "search_kbo_rows", return_value=[]):
                self.assertEqual(knowledge.search_kbo_documents("좌석", stadium_code="JAMSIL"), "검색 결과 없음")
            state = knowledge.assistant_context.get()
            self.assertEqual(state["tools"], ["rag"])
            self.assertEqual(state["sources"], [])
        finally:
            knowledge.assistant_context.reset(token)

    def test_kbo_retrieval_preserves_category_retry(self):
        search = Mock(side_effect=[[], [{"content": "문서"}]])
        rows = knowledge.search_kbo_rows("좌석", "JAMSIL", ["SEAT"], 5, search, lambda _: [0.1])
        self.assertEqual(rows, [{"content": "문서"}])
        self.assertEqual(search.call_count, 2)
        self.assertEqual(search.call_args.args[-1], None)

    def test_kbo_retrieval_uses_v1_candidate_breadth(self):
        search = Mock(side_effect=[[], [{"content": str(i)} for i in range(50)]])
        rows = knowledge.search_kbo_rows("좌석", "JAMSIL", ["SEAT"], 5, search, lambda _: [0.1])
        self.assertEqual([c.args[1] for c in search.call_args_list], [50, 50])
        self.assertEqual(len(rows), 5)
