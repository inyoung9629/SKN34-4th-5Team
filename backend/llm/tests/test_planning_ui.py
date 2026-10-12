from unittest.mock import patch
from copy import deepcopy
from types import SimpleNamespace

from django.test import SimpleTestCase
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from llm.serializer.message import done_payload, project_history, public_frame
from llm.service.chat_v2 import _frames, _model_history
from llm.v2.agent.chat_ui import present_planning_questions, public_ui, with_place_links
from llm.v2.middleware.dynamic_tools import DynamicToolMiddleware


class PlanningUiTest(SimpleTestCase):
    def test_place_links_are_server_derived_and_survive_stream_done_and_history(self):
        payload = {'offer_writer': False, 'questions': [{'question': '바꿀 곳은?', 'choices': ['1번 STATION', '3번 카페', '공원']} ]}
        state = {'context': {'currentCourse': {'places': [
            {'label': '1', 'name': 'STATION', 'placeUrl': 'https://myseatcheck.com/station/'},
            {'label': '3', 'name': '카페', 'placeUrl': 'http://place.map.kakao.com/123'},
            {'label': '4', 'name': '공원'},
        ]}}}
        before = deepcopy(state)
        call = {'type': 'tool_call', 'name': 'present_planning_questions', 'id': 'ui', 'args': payload}
        request = SimpleNamespace(state=state, tool_call=call)
        result = DynamicToolMiddleware(['present_planning_questions']).wrap_tool_call(request, lambda _: present_planning_questions.invoke(call))
        expected = {**payload, 'questions': [{**payload['questions'][0], 'choice_links': [
            {'choice': '1번 STATION', 'url': 'https://myseatcheck.com/station/', 'label': '매장 상세 위치'},
            {'choice': '3번 카페', 'url': 'https://place.map.kakao.com/123', 'label': '장소 정보'},
        ]}]}
        self.assertEqual(state, before)
        self.assertNotIn('choice_links', payload['questions'][0])
        self.assertEqual(result.artifact, expected)
        self.assertEqual(with_place_links(payload, {'course_memory': {'current': state['context']['currentCourse']}}), expected)
        messages = [HumanMessage('바꿔줘', id='h'), result, AIMessage('골라 주세요', id='a')]
        turns = {'h': {'status': 'completed', 'answer_id': 'a'}}
        self.assertEqual(project_history(messages, turns)[-1]['planning'], expected)
        self.assertEqual(public_frame('done', done_payload(messages, turns), False)[1]['planning'], expected)
        class Graph:
            def stream(self, *args, **kwargs):
                yield (), 'updates', {'tools': {'messages': [result]}}
        with patch('llm.v2.agent.chain.get_graph', return_value=Graph()):
            frames = list(_frames({'messages': messages[:1]}, {'answer': '', 'messages': []}))
        self.assertEqual([data for event, data in frames if event == 'planning'], [expected])

    def test_no_guessed_links_for_ambiguous_or_stale_choices(self):
        payload = {'offer_writer': False, 'questions': [{'question': '어디?', 'choices': ['카페', '1번 잘못된 이름']} ]}
        places = [{'label': str(i), 'name': '카페', 'placeUrl': f'https://place.map.kakao.com/{i}'} for i in [1, 2]]
        self.assertEqual(with_place_links(payload, {'context': {'currentCourse': {'places': places}}}), payload)
        # An explicitly empty current course must not resurrect stored sources.
        self.assertEqual(with_place_links(payload, {'context': {'currentCourse': {}}, 'course_memory': {'current': {'places': places[:1]}}}), payload)

    def test_invalid_links_are_omitted_without_losing_choices(self):
        payload = {'offer_writer': False, 'questions': [{'question': '어디?', 'choices': ['A', 'B'], 'choice_links': [
            {'choice': 'A', 'url': 'javascript:alert(1)'}, {'choice': 'A', 'url': 'https://user:pass@example.com/'},
            {'choice': 'X', 'url': 'https://example.com/'}, {'choice': 'A', 'url': 'https://example.com/a', 'label': '임의 문구'},
            {'choice': 'A', 'url': 'https://example.com/duplicate'}, None,
        ]}]}
        self.assertEqual(public_ui(payload)['questions'][0]['choice_links'], [
            {'choice': 'A', 'url': 'https://example.com/a', 'label': '장소 정보'}])

    def test_tool_stream_history_and_public_projection(self):
        payload = {"offer_writer": True, "questions": [
            {"question": "동행은?", "choices": ["혼자", "친구"]},
            {"question": "이동은?", "choices": [str(i) for i in range(10)]},
        ]}
        result = present_planning_questions.invoke({"type": "tool_call", "id": "ui", "name": present_planning_questions.name, "args": payload})
        self.assertEqual(result.artifact, payload)
        human, answer = HumanMessage("계획 짜줘", id="h"), AIMessage("조건을 알려주세요", id="a")
        call = AIMessage("", tool_calls=[{"id": "ui", "name": present_planning_questions.name, "args": payload}])
        messages = [human, call, result, answer]
        turns = {"h": {"status": "completed", "answer_id": "a"}}
        self.assertEqual(project_history(messages, turns)[-1]["planning"], payload)
        self.assertEqual(done_payload(messages, turns)["planning"], payload)
        self.assertEqual(public_frame("done", done_payload(messages, turns), False)[1]["planning"], payload)
        self.assertIn("동행은?: 혼자 / 친구", _model_history(messages, turns)[-1].content)

        class Graph:
            def stream(self, *args, **kwargs):
                yield (), "updates", {"model": {"messages": [call]}}
                yield (), "updates", {"tools": {"messages": [result]}}
                yield (), "updates", {"model": {"messages": [answer]}}
        with patch("llm.v2.agent.chain.get_graph", return_value=Graph()):
            frames = list(_frames({"messages": [human]}, {"answer": "", "messages": []}))
        self.assertEqual([data for event, data in frames if event == "planning"], [payload])

    def test_general_questions_preserve_free_text_and_schedule_choices(self):
        for questions in [[{"question": "어느 날짜인가요?", "choices": []}],
                          [{"question": "어느 팀인가요?", "choices": ["두산", "LG"]}]]:
            payload = {"offer_writer": False, "questions": questions}
            result = present_planning_questions.invoke({"type": "tool_call", "id": "ui", "name": present_planning_questions.name, "args": payload})
            self.assertEqual(result.artifact, payload)
            human, answer = HumanMessage("일정 알려줘", id="h"), AIMessage("조건을 알려주세요", id="a")
            messages = [human, result, answer]
            turns = {"h": {"status": "completed", "answer_id": "a"}}
            self.assertEqual(project_history(messages, turns)[-1]["planning"], payload)
            self.assertEqual(public_frame("done", done_payload(messages, turns), False)[1]["planning"], payload)
            class Graph:
                def stream(self, *args, **kwargs):
                    yield (), "updates", {"tools": {"messages": [result]}}
                    yield (), "updates", {"model": {"messages": [answer]}}
            with patch("llm.v2.agent.chain.get_graph", return_value=Graph()):
                frames = list(_frames({"messages": [human]}, {"answer": "", "messages": []}))
            self.assertEqual([data for event, data in frames if event == "planning"], [payload])

    def test_tool_rejects_invalid_questions(self):
        from pydantic import ValidationError
        for question in ["", " " , "q" * 161]:
            with self.assertRaises((ValueError, ValidationError)):
                present_planning_questions.invoke({"offer_writer": False, "questions": [{"question": question, "choices": []}]})
        for choices in [["one"], ["same", "same"], ["", "ok"], ["a" * 81, "ok"], [str(i) for i in range(11)]]:
            with self.assertRaises((ValueError, ValidationError)):
                present_planning_questions.invoke({"offer_writer": False, "questions": [{"question": "조건", "choices": choices}]})
        for questions in [[], [{"question": "조건", "choices": []}] * 5]:
            with self.assertRaises(ValueError):
                present_planning_questions.invoke({"offer_writer": False, "questions": questions})

    def test_invalid_optional_payload_is_omitted_without_losing_text(self):
        for choices in [["one"], [str(i) for i in range(11)], ["same", "same"], ["", "ok"], [1, "ok"]]:
            self.assertIsNone(public_ui({"offer_writer": True, "questions": [{"question": "조건", "choices": choices}]}))
        payload = {"offer_writer": True, "questions": [], "url": "javascript:bad", "private": "not public"}
        self.assertEqual(public_ui(payload), {"offer_writer": True, "questions": []})
        human, answer = HumanMessage("조건", id="h"), AIMessage("텍스트 답변", id="a")
        invalid = ToolMessage("tool", name=present_planning_questions.name, tool_call_id="ui", artifact={"offer_writer": "yes"})
        rows = project_history([human, invalid, answer], {"h": {"status": "completed", "answer_id": "a"}})
        self.assertNotIn("planning", rows[-1])
        self.assertEqual(rows[-1]["content"], "텍스트 답변")
