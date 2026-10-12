"""Course clarification, partial evidence and demo provenance regressions."""
from copy import deepcopy
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from llm.service.chat_v2 import _model_history
from llm.service.chat_runs import Stopped
from llm.tests.test_course_editing import CURRENT, PLACES, GAME
from llm.tests.test_course_evidence import PLACE, finding, requirement
from llm.tests.test_course_schedule import NOW, game
from llm.v1.rag.course import agent, edit_context, editing, evidence_memory as evidence, grounding, literal_edits, memory, place_quality, save
from llm.v2.agent.course_output import public_course
from llm.v2.middleware.course_answer import course_text
from llm.v2.middleware.dynamic_tools import request_args


class CourseSelectionRepairTest(SimpleTestCase):
    def setUp(self):
        self.current = {**deepcopy(CURRENT), 'places': [
            {**PLACES[0], 'label': '1', 'name': 'STATION'},
            *deepcopy(PLACES[1:]), {**PLACES[0], 'label': '5', 'name': '술집', 'visitId': 'bar'}]}
        self.original = '식당이 좀 별로다. 돈카츠 가게로 수정해줘'
        self.choices = ['1번 STATION', '5번 술집']
        self.saved = {**memory.empty(), 'current': deepcopy(self.current)}
        plan = literal_edits._interpret(self.original, self.current, target=self.current['places'][0])
        plan.update(operation='clarify', targets=[], clarification='대상을 알려 주세요')
        with patch.object(agent, 'requested_date', return_value=None):
            self.outcome = editing.answer(self.original, [], self.current, 'JAMSIL', None, self.saved, parsed=plan)
        self.saved = self.outcome['courseMemory']

    def history(self, prompt):
        messages = [HumanMessage(self.original, id='h'),
                    ToolMessage('', name='plan_course', tool_call_id='c', artifact={'course_memory': self.saved}),
                    ToolMessage('', name='present_planning_questions', tool_call_id='ui', artifact={
                        'offer_writer': False, 'questions': [{'question': prompt, 'choices': self.choices}]}),
                    AIMessage('골라 주세요', id='a')]
        turns = {'h': {'status': 'completed', 'answer_id': 'a'}}
        self.assertEqual(memory.restore(messages, turns)['pending_selection'], self.saved['pending_selection'])
        projected = _model_history(messages, turns)
        state = {'messages': [*projected, HumanMessage(prompt + ': 1번 STATION')]}
        _, _, history = request_args(state, course=True)
        self.assertNotIn('planning_questions', request_args(state)[2][-1])
        return history

    def test_projected_choices_bind_original_request_and_visit_independent_of_wording(self):
        self.assertEqual(self.outcome['places'], [])
        for prompt in ['돈카츠 가게로 바꿀 식당을 골라 주세요.', '돈카츠 가게로 변경할 식당을 골라 주세요.',
                       '어느 식당을 돈카츠로 바꿀까요?', '교체할 곳을 선택해 주세요']:
            history = self.history(prompt)
            for answer in ['1번 STATION', 'STATION', prompt + ': 1번 STATION']:
                with self.subTest(prompt=prompt, answer=answer), patch.object(agent, 'llm') as model:
                    result = editing.interpret(answer, self.current, history, self.saved)
                    self.assertEqual(result['targets'], ['food'])
                    self.assertEqual(result['conditions'], ['돈까스'])
                    self.assertTrue(result['_literal_edit'])
                    model.assert_not_called()
        attached = memory.attach({**self.current, 'travel': {'mode': 'walk'}}, self.saved)
        self.assertNotIn('pending_selection', attached['courseMemory'])

    def test_stale_extra_or_unoffered_selection_never_uses_fast_path(self):
        prompt = '돈카츠 가게로 바꿀 식당을 골라 주세요.'
        history = self.history(prompt)
        for answer in [prompt + ': 1번 STATION 그리고 주차 가능', '5번 STATION', '기존 카페', '다른 질문: 1번 STATION']:
            self.assertIsNone(literal_edits.interpret(answer, self.current, self.saved, history))
        for change in ['date', 'visit', 'order', 'name', 'category']:
            current = deepcopy(self.current)
            if change == 'date': current['game']['date'] = '2026-10-16'
            if change == 'visit': current['places'][0]['visitId'] = 'new-visit'
            if change == 'order': current['places'].reverse()
            if change == 'name': current['places'][0]['name'] = '다른 식당'
            if change == 'category': current['places'][0]['category'] = 'CAFE'
            self.assertIsNone(literal_edits.interpret(prompt + ': 1번 STATION', current, self.saved, history))
        history[0]['content'] = '주차 가능한 돈카츠로 바꿔줘'
        self.assertIsNone(literal_edits.interpret('STATION', self.current, self.saved, history))

    def test_phase_name_choices_map_to_the_same_visit_and_source_without_guessing(self):
        from llm.v2.agent.chat_ui import with_place_links
        from llm.place_choices import selected_place
        self.current['places'][0]['placeUrl'] = 'https://myseatcheck.com/station/'
        self.choices = ['경기 전 STATION', '5번 술집']
        history = self.history('돈카츠 가게로 바꿀 곳이 어디예요?')
        result = literal_edits.interpret('돈카츠 가게로 바꿀 곳이 어디예요?: 경기 전 STATION', self.current, self.saved, history)
        self.assertEqual(result['targets'], ['food'])
        payload = with_place_links({'offer_writer': False, 'questions': history[-1]['planning_questions']},
                                   {'context': {'currentCourse': self.current}})
        self.assertEqual(payload['questions'][0]['choice_links'][0]['url'], 'https://myseatcheck.com/station/')
        self.assertIsNone(selected_place('경기 후 STATION', self.current['places']))

    def test_detailed_requests_and_other_chat_questions_have_no_course_binding(self):
        for question in ['돈카츠로 바꿔줘 그리고 주차도 가능해야 해', '알레르기 없는 돈카츠로 바꿔줘',
                         '식당을 돈카츠로 바꿔줘 그리고 카페도 메가커피로 바꿔줘', '경기 일정 알려줘']:
            self.assertIsNone(literal_edits.pending_selection(question, self.current, self.saved))
        self.assertIsNone(literal_edits.interpret('STATION', self.current, {}, self.history('어느 곳?')))


@override_settings(COURSE_WEB_VERIFICATION_ENABLED=True, PLACE_EVIDENCE_STORAGE_POLICIES=[])
class CoursePartialEvidenceRepairTest(TestCase):
    def test_known_partial_evidence_survives_without_search_or_unverified_match(self):
        other = {**PLACE, 'placeId': 'fixture:unknown'}
        token = evidence.allow_new_source_search.set(False)
        try:
            with place_quality.request_scope('JAMSIL'), \
                    patch.object(place_quality, 'resolve', return_value={'reviewUrl': 'https://example.com/menu'}), \
                    patch.object(grounding, 'verify', return_value=[finding()]), \
                    patch.object(evidence, '_search_once') as search:
                result = evidence.enrich([PLACE, other], ['돈까스'])
                self.assertEqual([p['placeId'] for p in result], [PLACE['placeId']])
                search.assert_not_called()
                from travel.place_knowledge_models import PlaceEnrichmentAttempt
                self.assertFalse(PlaceEnrichmentAttempt.objects.filter(status='api_error').exists())
                self.assertTrue(PlaceEnrichmentAttempt.objects.filter(place_id=other['placeId'], status='no_evidence').exists())
        finally:
            evidence.allow_new_source_search.reset(token)

    def test_no_evidence_and_exclusion_stay_unknown_and_cancellation_propagates(self):
        token = evidence.allow_new_source_search.set(False)
        try:
            with place_quality.request_scope('JAMSIL'), \
                    patch.object(place_quality, 'resolve', return_value={'reviewUrl': 'https://example.com/menu'}), \
                    patch.object(grounding, 'verify', return_value=[]), patch.object(evidence, '_search_once') as search:
                self.assertEqual(evidence.search([PLACE], [requirement()])[0], [])
                self.assertEqual(evidence.search([PLACE], [requirement().model_copy(update={'intent': 'exclude'})])[0], [])
                search.assert_not_called()
            with patch.object(evidence, 'search', side_effect=Stopped), self.assertRaises(Stopped):
                evidence.enrich([PLACE], ['돈까스'])
        finally:
            evidence.allow_new_source_search.reset(token)

    def test_search_enabled_legacy_path_still_reads_and_combines_sources(self):
        token = evidence.allow_new_source_search.set(True)
        try:
            with patch.object(place_quality, 'active', return_value=False), \
                    patch.object(evidence, '_search_once', return_value=([], set(), 1, [])) as search, \
                    patch.object(grounding, 'verify', return_value=[]):
                self.assertEqual(evidence.search([PLACE], [requirement()]), ([], set(), 1))
                search.assert_called_once()
        finally:
            evidence.allow_new_source_search.reset(token)


class CourseDemoRepairTest(SimpleTestCase):
    def test_demo_schedule_rebuild_public_result_and_save_keep_notice(self):
        for code, clock in [('GWANGJU', '14:00'), ('JAMSIL', '17:00')]:
            raw = game(day='2026-10-16', time=clock, stadium=code)
            raw.update(source='demo', game_type='DEMO', source_status_label='가상 경기')
            chosen, _, _ = agent.find_game(code, '2026-10-16', '2026-10-12', {'items': [raw]}, now=NOW)
            self.assertEqual(chosen['source'], 'demo')
            self.assertIn('시연용 가상 경기', agent._game_text(chosen, code))
            self.assertIn('시연용 가상 경기', agent.schedule_notice(chosen))
            current = {**CURRENT, 'stadiumCode': code, 'game': {'date': '2026-10-16', 'time': clock}}
            with patch.object(editing, 'route_legs', return_value=[{'minutes': 5, 'meters': 400, 'by': 'walk'}] * 3):
                result = editing.rebuild(deepcopy(PLACES), current, code, None, '순서 바꿔줘', [], _game=chosen)
            self.assertEqual(public_course(result)['game'], {'date': '2026-10-16', 'time': clock, 'isDemo': True})
            self.assertIn('시연용 가상 경기', result['answer'])
            self.assertEqual(result['places'][2]['time'], '13:40' if clock == '14:00' else '16:40')
            self.assertIn('시연용 가상 경기', save.course_payload(result['places'], game=chosen)['content'])
            with patch('llm.v2.middleware.course_answer.timezone.localdate', return_value=NOW.date()):
                text = course_text({**result, 'courseOnly': True})
                self.assertEqual(text.count('시연용 가상 경기'), 1)

    def test_only_matching_server_game_restores_demo_flag_and_real_game_is_unchanged(self):
        stored = {**deepcopy(CURRENT), 'game': {**CURRENT['game'], 'isDemo': True}}
        client = edit_context.validate(stored)
        self.assertNotIn('isDemo', client['game'])
        restored = memory.restore_place_details(client, {'current': stored})
        self.assertTrue(restored['game']['isDemo'])
        client['game']['time'] = '15:00'
        self.assertNotIn('isDemo', memory.restore_place_details(client, {'current': stored})['game'])
        self.assertNotIn('시연용 가상 경기', agent._game_text(GAME, 'JAMSIL'))
        self.assertNotIn('시연용 가상 경기', save.course_payload(PLACES, game=GAME)['content'])
