import json
from datetime import datetime
from unittest.mock import Mock, patch

from django.test import SimpleTestCase
from langchain_core.messages import AIMessage, HumanMessage

from llm.service.usage import _call_cost
from llm.v2.course.itinerary_request import ItineraryRequest, KST, extract_itinerary
from llm.v2.course.prompt_payload import condition_state, schema_json


class CoursePromptBudgetTest(SimpleTestCase):
    def test_compact_schema_preserves_validation_constraints(self):
        compact = json.loads(schema_json(ItineraryRequest))
        full = ItineraryRequest.model_json_schema()
        self.assertEqual(compact['properties']['stops']['maxItems'], 6)
        self.assertEqual(compact['additionalProperties'], full['additionalProperties'])
        self.assertEqual(compact['$defs']['StopRequest']['required'], ['kind'])
        self.assertNotIn('title', compact)

    def test_condition_context_does_not_grow_with_evidence_and_routes(self):
        state = {'conditions': {'stadium_code': 'DAEJEON'}, 'preferences': {'transport': '도보'},
                 'itinerary_result': {'search_trace': ['x'*1000]*160}, 'unknown_field': 'secret'}
        self.assertEqual(set(condition_state(state)), {'conditions', 'preferences'})

    def test_real_itinerary_prompt_reservation_fits_after_game_extraction(self):
        fake = Mock()
        fake.invoke.return_value = AIMessage('{"stops":[{"kind":"food"},{"kind":"cafe"}],"mode":"walk"}')
        now = datetime(2026, 10, 2, 16, tzinfo=KST)
        question = '같은 조건으로 다시 코스를 짜줘. 2026년 10월 3일 대전 경기 전 식당→카페→야구장, 도보, 출발지는 없어.'
        with patch('llm.v2.agent.common.llm', return_value=fake):
            extract_itinerary(question, {'stadium_code':'DAEJEON','stadium_name':'대전 한화생명 볼파크',
                'starts_at':'2026-10-03T14:00:00+09:00'}, {'transport':'도보'},
                history=[HumanMessage(question)]*3, now=now)
        amount = _call_cost([fake.invoke.call_args.args[0]], {'model':'gpt-6-luna','max_tokens':4000})
        self.assertLess(amount, 17000, f'Need headroom for prior game extraction, reserved={amount}')

    def test_followup_does_not_duplicate_previous_history(self):
        fake=Mock()
        fake.invoke.return_value=AIMessage('{"stops":[{"kind":"food"},{"kind":"cafe"}],"replace_stop_indices":[1]}')
        previous=ItineraryRequest(stops=[{'kind':'food'},{'kind':'cafe'}]).model_dump(mode='json')
        with patch('llm.v2.agent.common.llm',return_value=fake):
            extract_itinerary('식당은 그대로 카페만 바꿔줘',{'id':1,'starts_at':'2026-10-03T14:00:00+09:00'},
                {'itinerary':'경기 전 식당→카페, 출발지 없음, 도보'},previous,
                history=[HumanMessage('이전 질문'*200)]*10,now=datetime(2026,10,2,16,tzinfo=KST))
        messages=fake.invoke.call_args.args[0]
        self.assertEqual(json.loads(messages[1].content)['recent_user_messages'],[])
        amount=_call_cost([messages],{'model':'gpt-6-luna','max_tokens':4000})
        self.assertLess(amount,15000,f'Need space for earlier calls: {amount}')
