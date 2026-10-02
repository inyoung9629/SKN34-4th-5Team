"""Production evidence path regressions with synthetic bodies; no paid APIs."""
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
from django.test import SimpleTestCase, TestCase, override_settings

from llm.v2.course.evidence_memory import EvidenceMemory, place_key
from llm.v2.course.knowledge_verification import (
    KnowledgeVerification, KnowledgeFoodVerifier, KnowledgeReviewVerifier, food_report, review_report,
)
from llm.v2.course.review_verification import evaluate_reviews
from llm.v2.course.serper_evidence import (
    SerperResearch, ResearchPolicy, PageExtraction, extract_pages, grounded_facts,
)
from llm.v2.course.itinerary_service import generate_itinerary
from travel.place_knowledge_models import PlaceKnowledgeObservation, PlaceKnowledgeSource
from travel.public_page_reader import PublicReader, allowed_url
from .test_itinerary_planner import NOW, GAME, STADIUM, COVERAGE, request, place, route


URL = 'https://www.diningcode.com/profile.php?rid=fixture'
GENERAL = dict(scope='general', weekdays=[], start_minute=None, end_minute=None)
PLACE = place('가상식당', 50, placeId='123', source='KAKAO', address='서울특별시 송파구 올림픽로 10')


def spec(**extra):
    return request(stops=[{'kind': 'food', 'food': {'all_of': [{'any_of': [
        {'kind': 'menu', 'name': '돈까스'}]}]}, **extra}])


def fact(**extra):
    return dict(attribute='menu', term='돈까스', qualifiers=[], polarity='positive',
        basis='menu_listing', url=URL, kind='menu_listing', context=dict(GENERAL),
        experience_key='', promotion='unknown', observed_on=None, observation_date_kind='unknown',
        checked_at=NOW, valid_until=NOW+timedelta(days=30), summary='menu:돈까스 [positive]',
        origin='live', body_hash='body-one', published_on=None) | extra


def review(number, **extra):
    return fact(attribute='quietness', term='', basis='customer_experience', kind='customer_review',
        url=f'https://review.example.com/{number}', experience_key=f'experience-{number}',
        summary=f'evidence-{number}', promotion='not_disclosed', observed_on=NOW.date(),
        observation_date_kind='published', **extra)


def memory(facts=(), status='ok'):
    return SimpleNamespace(load=Mock(return_value=(list(facts), status)), cooling_down=Mock(return_value=False),
        save=Mock(return_value={'stored': 1}), record_attempt=Mock(return_value=True))


def page(**extra):
    return dict(source_id='one', url=URL, body_read=True, body_text=
        '가상식당 서울특별시 송파구 올림픽로 10 돈까스 판매 메뉴 2026-10-01 매장이 조용하다',
        visible_text_complete=True, text_sha256='body-one') | extra


def extraction(**extra):
    value = dict(source_id='one', identity='match', name_quote='가상식당', address_quote='올림픽로 10',
        kind='menu_listing', food=[dict(condition_id='0.0', availability='present',
        quote='돈까스 판매 메뉴', current_menu=True, published_on=None, date_quote='')], reviews=[])
    return PageExtraction.model_validate({'pages': [value | extra]})


class EvidenceLogicTests(SimpleTestCase):
    def test_stored_pass_never_searches_even_when_enabled(self):
        research = SerperResearch(searcher=Mock(side_effect=AssertionError('No search')))
        session = KnowledgeVerification(spec(), web_enabled=True, memory=memory([fact(origin='stored')]), research=research, now=NOW)
        session.allow_web = True
        self.assertEqual(KnowledgeFoodVerifier(session).verify(PLACE, spec().stops[0].food)['status'], 'pass')
        research.searcher.assert_not_called()

    def test_local_miss_does_not_search_until_service_enables_second_pass(self):
        research = SerperResearch(searcher=Mock(side_effect=AssertionError('No search')))
        session = KnowledgeVerification(spec(), web_enabled=True, memory=memory(), research=research, now=NOW)
        self.assertEqual(KnowledgeFoodVerifier(session).verify(PLACE, spec().stops[0].food)['status'], 'unknown')
        research.searcher.assert_not_called()

    def test_db_failure_or_cooldown_never_buys_searches(self):
        for unavailable in (False, True):
            mem = memory(status='memory_unavailable' if unavailable else 'ok')
            mem.cooling_down.return_value = True
            session = KnowledgeVerification(spec(), web_enabled=True, memory=mem, now=NOW)
            session.allow_web = True
            with patch.object(session.research, 'run') as run:
                KnowledgeFoodVerifier(session).verify(PLACE, spec().stops[0].food)
            run.assert_not_called()

    def test_contradiction_is_unknown_and_exclusion_does_not_turn_missing_into_pass(self):
        req = spec().stops[0].food
        self.assertEqual(food_report([fact(), fact(polarity='negative', basis='explicit_non_sale')], req, NOW)['status'], 'unknown')
        req.all_of[0].any_of[0].exclude = True
        self.assertEqual(food_report([], req, NOW)['status'], 'unknown')
        self.assertEqual(food_report([fact()], req, NOW)['status'], 'fail')
        self.assertEqual(food_report([fact(polarity='negative', basis='explicit_non_sale')], req, NOW)['status'], 'pass')

    def test_dish_qualifiers_are_not_broadened(self):
        req = spec().stops[0].food
        req.all_of[0].any_of[0].qualifiers = ['치즈 없음']
        self.assertEqual(food_report([fact()], req, NOW)['status'], 'unknown')

    def test_reviews_need_independence_no_contradictions_and_actual_visit_context(self):
        req = spec(reviews={'all_of': [{'aspect': 'quietness', 'priority': 'required'}]}).stops[0].reviews
        self.assertFalse(evaluate_reviews(review_report([review(1)], NOW), req)['eligible'])
        self.assertTrue(evaluate_reviews(review_report([review(1), review(2)], NOW), req)['eligible'])
        self.assertFalse(evaluate_reviews(review_report([review(1), review(2), review(3, polarity='negative')], NOW), req)['eligible'])
        limited = dict(scope='limited', weekdays=[NOW.weekday()], start_minute=720, end_minute=900)
        report = review_report([review(1, context=limited), review(2, context=limited)], NOW)
        self.assertFalse(evaluate_reviews(report, req)['eligible'])
        self.assertTrue(evaluate_reviews(report, req, arrival=NOW, departure=NOW+timedelta(minutes=30))['eligible'])
        self.assertFalse(evaluate_reviews(report, req, arrival=NOW+timedelta(hours=4), departure=NOW+timedelta(hours=5))['eligible'])

    def test_internal_candidate_cannot_use_external_memory_or_search(self):
        mem = memory([fact()])
        session = KnowledgeVerification(spec(), memory=mem, now=NOW)
        result = KnowledgeFoodVerifier(session).verify({**PLACE, 'stadiumAffiliation': {'scope': 'internal'}}, spec().stops[0].food)
        self.assertEqual(result['reason'], 'internal_restaurant')
        mem.load.assert_not_called()

    def test_grounding_rejects_fabricated_branch_quotes_dates_and_sources(self):
        conditions = spec().stops[0].food.conditions()
        valid = grounded_facts(extraction(), PLACE, [page()], conditions, [], NOW)
        self.assertEqual(len(valid), 1)
        self.assertNotIn('quote', valid[0])
        for change in ({'name_quote': '다른식당'}, {'source_id': 'invented'}, {'address_quote': '올림픽로 11'}, {'identity': 'unknown'}):
            self.assertEqual(grounded_facts(extraction(**change), PLACE, [page()], conditions, [], NOW), [])
        data = extraction().model_dump()
        data['pages'][0]['food'][0].update(published_on=NOW.date(), date_quote='없는 날짜')
        self.assertEqual(grounded_facts(data, PLACE, [page()], conditions, [], NOW), [])

    def test_missing_menu_is_never_non_sale(self):
        data = extraction().model_dump()
        data['pages'][0]['food'][0]['availability'] = 'absent'
        self.assertEqual(grounded_facts(data, PLACE, [page()], spec().stops[0].food.conditions(), [], NOW), [])

    def test_road_number_prefix_is_not_the_same_branch(self):
        for address in ('올림픽로 100', '올림픽로 10-1'):
            wrong_page = page(body_text='가상식당 서울특별시 송파구 ' + address + ' 돈까스 판매 메뉴')
            self.assertEqual(grounded_facts(extraction(address_quote=address), PLACE, [wrong_page],
                spec().stops[0].food.conditions(), [], NOW), [])

    def test_luna_has_no_search_tools_and_exact_model(self):
        fake = Mock()
        fake.responses.parse.return_value = SimpleNamespace(status='completed', output_parsed=extraction(), output=[])
        with patch('openai.OpenAI') as client, patch.dict('os.environ', {'LLM_MODEL': 'gpt-6-luna'}):
            client.return_value.__enter__.return_value = fake
            extract_pages(PLACE, [page()], spec().stops[0].food.conditions(), [], timeout=2, policy=ResearchPolicy())
        args = fake.responses.parse.call_args.kwargs
        self.assertEqual(args['model'], 'gpt-6-luna')
        self.assertEqual(args['tools'], [])
        self.assertEqual(args['tool_choice'], 'none')
        self.assertFalse(args['store'])
        self.assertNotIn('temperature', args)

    def test_shared_search_budget_and_no_provider_retry(self):
        searcher = Mock(return_value=[])
        research = SerperResearch(searcher=searcher, now=NOW)
        for i in range(10):
            research.run({**PLACE, 'placeId': str(i)}, {}, [], sufficient=lambda _: False)
        self.assertEqual(searcher.call_count, 4)
        self.assertEqual(research.audit()['openai_web_search_calls'], 0)
        broken = Mock(side_effect=ValueError('429'))
        research = SerperResearch(searcher=broken, now=NOW)
        for i in range(2):
            research.run({**PLACE, 'placeId': str(i)}, {}, [], sufficient=lambda _: False)
        self.assertEqual(broken.call_count, 1)

    def test_token_refusal_is_limited_without_more_search_or_fake_model_call(self):
        from llm.service.usage import UsageExhausted
        reader=Mock()
        reader.read.return_value=page()
        searcher=Mock(return_value=[{'url':URL}])
        research=SerperResearch(searcher=searcher,reader_factory=Mock(return_value=reader),
            extractor=Mock(side_effect=UsageExhausted),now=NOW)
        _,status=research.run(PLACE,{},['cozy'],sufficient=lambda _:False)
        self.assertEqual(status,'limited')
        self.assertEqual(research.models,0)
        _,status=research.run(dict(PLACE,placeId='456'),{},['cozy'],sufficient=lambda _:False)
        self.assertEqual(status,'limited')
        self.assertEqual(searcher.call_count,1)

    def test_only_unresolved_conditions_are_sent_to_research(self):
        req = spec(reviews={'all_of': [{'aspect': 'quietness', 'priority': 'required'}]})
        session = KnowledgeVerification(req, memory=memory([fact(origin='stored')]), web_enabled=True, now=NOW)
        session.allow_web = True
        with patch.object(session.research, 'run', return_value=([review(1), review(2)], 'ready')) as run:
            result = KnowledgeFoodVerifier(session).verify(PLACE, req.stops[0].food)
        self.assertEqual(result['status'], 'pass')
        self.assertEqual(run.call_args.args[1], {})
        self.assertEqual(run.call_args.args[2], ['quietness'])

    def test_direct_luna_reserves_and_records_actual_usage_without_web_tools(self):
        from llm.service import usage
        meter = usage.Meter(100_000)
        token = usage._meter.set(meter)
        self.addCleanup(usage._meter.reset, token)
        fake = Mock()
        fake.responses.parse.return_value = SimpleNamespace(status='completed', output_parsed=extraction(),
            output=[], usage=SimpleNamespace(input_tokens=100, output_tokens=20))
        with patch('openai.OpenAI') as client, patch.dict('os.environ', {'LLM_MODEL': 'gpt-6-luna'}):
            client.return_value.__enter__.return_value = fake
            extract_pages(PLACE, [page()], {}, [], timeout=2, policy=ResearchPolicy())
        self.assertEqual(meter.totals(), (100, 20, 1, 0))
        self.assertEqual(meter._inflight, {})

    def test_direct_luna_budget_exhaustion_prevents_provider_call(self):
        from llm.service import usage
        meter = usage.Meter(10)
        token = usage._meter.set(meter)
        self.addCleanup(usage._meter.reset, token)
        with patch('openai.OpenAI') as client, self.assertRaises(usage.UsageExhausted):
            extract_pages(PLACE, [page()], {}, [], timeout=2, policy=ResearchPolicy())
        client.assert_not_called()
        self.assertEqual(meter.totals(), (0, 0, 0, 0))

    def test_direct_luna_error_records_unknown_once(self):
        from llm.service import usage
        meter = usage.Meter(100_000)
        token = usage._meter.set(meter)
        self.addCleanup(usage._meter.reset, token)
        with patch('openai.OpenAI', side_effect=ValueError('synthetic')), self.assertRaises(ValueError):
            extract_pages(PLACE, [page()], {}, [], timeout=2, policy=ResearchPolicy())
        self.assertEqual(meter.totals(), (0, 0, 1, 1))
        self.assertEqual(meter._inflight, {})

    def test_gpt6_token_estimate_is_conservative_not_a_fabricated_tokenizer(self):
        from langchain_core.messages import HumanMessage
        from llm.service import usage
        with patch('tiktoken.encoding_for_model', side_effect=KeyError):
            bound = usage._call_cost([[HumanMessage('안녕하세요')]], {'model': 'gpt-6-luna', 'max_tokens': 100})
            self.assertGreater(bound, len('안녕하세요'.encode('utf-8')) + 100)
            with self.assertRaises(usage.UsageExhausted):
                usage._call_cost([[HumanMessage('hello')]], {'model': 'unknown-model', 'max_tokens': 100})


class PublicReaderTests(SimpleTestCase):
    def reader(self, handler, **kwargs):
        client = httpx.Client(transport=httpx.MockTransport(handler))
        self.addCleanup(client.close)
        return PublicReader(client=client, dns_check=lambda _: True, gap=0, **kwargs)

    def test_html_visible_text_no_script_and_no_persistent_payload(self):
        reader = self.reader(lambda r: httpx.Response(404) if r.url.path == '/robots.txt' else
            httpx.Response(200, text='<html><script>secret-instruction</script><p>' + '매장 메뉴 정보 ' * 60 +
                           '</p></html>', headers={'Content-Type': 'text/html'}))
        result = reader.read(URL, ['매장'])
        self.assertTrue(result['body_read'])
        self.assertNotIn('secret-instruction', result['body_text'])
        self.assertFalse(any('매장 메뉴' in str(v) for v in vars(reader).values()))
        self.assertEqual(reader.read(URL, [])['status'], 'already_attempted_no_retry')

    def test_robots_and_403_block_further_requests(self):
        for robots in (True, False):
            def handler(r):
                if r.url.path == '/robots.txt':
                    return httpx.Response(200, text='User-agent: *\nDisallow: /') if robots else httpx.Response(404)
                return httpx.Response(403)
            reader = self.reader(handler)
            result = reader.read(URL, [])
            self.assertFalse(result['body_read'])
            calls = reader.http_requests
            reader.read(URL + '2', [])
            self.assertEqual(reader.http_requests, calls)

    def test_local_private_and_redirect_targets_rejected(self):
        for url in ('http://127.0.0.1/', 'https://127.0.0.1/', 'https://www.diningcode.com@localhost/',
                    'https://www.diningcode.com.evil.invalid/', 'file:///etc/passwd'):
            self.assertFalse(allowed_url(url))
        reader = self.reader(lambda r: httpx.Response(404) if r.url.path == '/robots.txt' else
            httpx.Response(302, headers={'location': 'https://127.0.0.1/'}))
        self.assertEqual(reader.read(URL, [])['status'], 'redirect_not_followed')
        private = self.reader(lambda r: self.fail('Nonpublic address was fetched'))
        private.dns_check = lambda _: False
        self.assertFalse(private.read(URL, [])['body_read'])

    def test_deadline_and_request_budget_stop_without_network(self):
        for limits in ({'deadline': 1}, {'max_requests': 0}):
            reader = self.reader(lambda r: self.fail('Budget exhausted'), **limits)
            self.assertFalse(reader.read(URL, [])['body_read'])

    def test_challenge_is_not_parsed_as_evidence(self):
        reader = self.reader(lambda r: httpx.Response(404) if r.url.path == '/robots.txt' else
            httpx.Response(200, text='Verify you are human ' + 'x'*400, headers={'Content-Type': 'text/html'}))
        self.assertEqual(reader.read(URL, [])['status'], 'challenge_or_access_denied')
        self.assertEqual(reader.read(URL+'2', [])['status'], 'host_blocked')


class EvidenceStorageTests(TestCase):
    def setUp(self):
        clock = patch('django.utils.timezone.now', return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)
        self.memory = EvidenceMemory(NOW)

    def approve(self):
        return PlaceKnowledgeSource.objects.create(source_key='fixture:policy', provider='fixture', url=URL,
            kind='menu_listing', access_method='web', fetched_at=NOW-timedelta(seconds=1),
            storage_policy='allowed', allowed_attributes=['menu'], policy_reference='직접 작성한 테스트 자료',
            policy_checked_at=NOW)

    def test_write_read_replay_and_id_only_catalogue(self):
        self.approve()
        self.assertEqual(self.memory.save(PLACE, [fact()])['stored'], 1)
        self.assertEqual(self.memory.save(PLACE, [fact()])['stored'], 0)
        rows, status = self.memory.load(PLACE)
        self.assertEqual((len(rows), status), (1, 'ok'))
        row = PlaceKnowledgeObservation.objects.get()
        self.assertEqual(row.place_id, 'kakao:123')
        self.assertEqual(row.place.name, 'kakao:123')
        self.assertEqual(row.place.address, '')
        self.assertIsNone(row.place.lat)
        self.assertEqual(row.summary, 'menu:돈까스 [positive]')
        self.assertNotIn('body_text', rows[0])
        self.assertEqual(rows[0]['checked_at'], NOW)

    def test_unapproved_source_is_not_auto_approved_or_saved_as_fact(self):
        result = self.memory.save(PLACE, [fact()])
        self.assertEqual(result['policy_skipped'], 1)
        self.assertEqual(PlaceKnowledgeObservation.objects.count(), 0)
        self.assertEqual(PlaceKnowledgeSource.objects.get().storage_policy, 'unreviewed')

    def test_expired_and_revoked_facts_are_not_reused(self):
        self.approve()
        self.memory.save(PLACE, [fact()])
        self.assertEqual(EvidenceMemory(NOW+timedelta(days=31)).load(PLACE)[0], [])
        source = PlaceKnowledgeObservation.objects.get().source
        source.storage_policy = 'blocked'
        source.save()
        self.assertEqual(self.memory.load(PLACE)[0], [])
        self.assertEqual(self.memory.save(PLACE, [fact(body_hash='new-version')])['policy_skipped'], 1)
        self.assertEqual(PlaceKnowledgeObservation.objects.count(), 1)

    def test_no_name_based_join(self):
        self.approve()
        self.memory.save(PLACE, [fact()])
        self.assertEqual(self.memory.load({**PLACE, 'placeId': '456'})[0], [])

    def test_missing_attempt_is_cooldown_not_a_negative_fact(self):
        self.memory.record_attempt(PLACE, spec().stops[0].food, None, searches=2, models=1, status='missing')
        self.assertTrue(self.memory.cooling_down(PLACE, spec().stops[0].food, None))
        self.assertEqual(self.memory.load(PLACE)[0], [])

    def test_cafe_and_walk_generic_review_atoms_round_trip(self):
        for number, kind, aspect in ((1, 'cafe', 'cozy'), (2, 'walk', 'walking_comfort')):
            candidate = {**PLACE, 'placeId': str(900+number), 'kind': kind}
            atom = dict(review(number), attribute=aspect)
            PlaceKnowledgeSource.objects.create(source_key=f'policy-{number}', provider='fixture',
                url=atom['url'], kind='customer_review', access_method='web', fetched_at=NOW,
                storage_policy='allowed', allowed_attributes=['review_feature'],
                policy_reference='직접 작성한 테스트 자료', policy_checked_at=NOW)
            self.assertEqual(self.memory.save(candidate, [atom])['stored'], 1)
            rows, status = self.memory.load(candidate)
            self.assertEqual((status, rows[0]['attribute']), ('ok', aspect))
            saved = PlaceKnowledgeObservation.objects.get(place_id=place_key(candidate))
            self.assertEqual(saved.attribute, 'review_feature')
            self.assertEqual(saved.place.kind, kind)


@override_settings(COURSE_WEB_VERIFICATION_ENABLED=True)
class LocalFirstServiceTests(SimpleTestCase):
    def run_course(self, mem, research, pool):
        req = spec()
        session = KnowledgeVerification(req, memory=mem, research=research, web_enabled=True, now=NOW)
        inputs = {'question': '돈까스 먹고 직관', 'course_anchor': {'stadium_code': 'JAMSIL',
            'stadium_name': '잠실', 'starts_at': GAME.isoformat()}, 'course_state': {}}
        with patch('llm.v2.course.itinerary_service.KnowledgeVerification', return_value=session), \
                patch('llm.v2.course.itinerary_service.extract_itinerary', return_value=req), \
                patch('llm.v2.course.itinerary_service.load_planning_data', return_value=(
                    {'places': pool, 'coverage': COVERAGE, 'snapshotId': 'fixture'}, STADIUM)), \
                patch('llm.v2.course.itinerary_service.timezone.now', return_value=NOW), \
                patch('travel.directions_provider.fetch_directions', side_effect=route):
            text = ''.join(generate_itinerary(inputs))
        return inputs['course_state']['itinerary_result'], text

    def test_farther_stored_match_is_used_before_searching_near_miss(self):
        mem = memory()
        mem.load.side_effect = lambda p: ([fact(origin='stored')] if p['placeId'] == 'far' else [], 'ok')
        research = SerperResearch(searcher=Mock(side_effect=AssertionError('No search')), now=NOW)
        result, text = self.run_course(mem, research, [PLACE, {**PLACE, 'placeId': 'far', 'lat': 37.5+650/111195}])
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['stops'][0]['placeId'], 'far')
        self.assertFalse(result['verification_policy']['web_fallback_used'])
        self.assertIn('저장된 근거', text)
        research.searcher.assert_not_called()

    def test_missing_memory_runs_serper_body_luna_and_keeps_citations(self):
        reader = Mock()
        reader.read.return_value = page()
        research = SerperResearch(searcher=Mock(return_value=[{'url': URL}]),
            reader_factory=Mock(return_value=reader), extractor=Mock(side_effect=lambda p, pages, *a, **k:
                extraction(source_id=pages[0]['source_id'])), now=NOW)
        mem = memory()
        result, text = self.run_course(mem, research, [PLACE])
        self.assertEqual(result['status'], 'ok')
        self.assertTrue(result['verification_policy']['web_fallback_used'])
        self.assertEqual(result['verification_policy']['evidence_budget']['searches'], 1)
        self.assertIn(URL, text)
        mem.save.assert_called_once()
        self.assertNotIn('body_text', str(mem.save.call_args))
        self.assertNotIn('quote', str(mem.save.call_args))
