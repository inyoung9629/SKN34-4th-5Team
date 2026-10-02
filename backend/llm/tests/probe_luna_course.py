"""Opt-in ONE Luna call on synthetic text; no search, fetch, or DB writes.

python manage.py shell -c "from llm.tests.probe_luna_course import run; run(live=True)"
Prints aggregate usage and semantic checks, never API keys or raw page bodies.
"""
import json
import os
from datetime import datetime

from llm.service import usage
from llm.v2.course.food_requirements import FoodRequirements
from llm.v2.course.itinerary_request import KST
from llm.v2.course.knowledge_verification import food_report
from llm.v2.course.serper_evidence import ResearchPolicy, extract_pages, grounded_facts


def run(*, live=False):
    if not live:
        raise ValueError('Explicit live=True required: one paid model call, zero search calls')
    if os.getenv('LLM_MODEL') != 'gpt-6-luna':
        raise ValueError('Run only after the GPT-6 Luna environment is applied')
    now = datetime.now(KST)
    place = {'placeId': 'synthetic-luna-probe', 'name': '테스트식당 잠실점',
             'address': '서울특별시 송파구 올림픽로 10', 'source': 'SYNTHETIC'}
    body = (f'가상의 공식 매장 안내: 테스트식당 잠실점. 서울특별시 송파구 올림픽로 10. '
            f'{now.date().isoformat()} 현재 이 지점에서 판매하는 메뉴: 돈카츠, 우동. '
            '서울의 다른 매장이 아니라 위 주소 지점의 메뉴입니다.')
    pages = [{'source_id': 'synthetic-1', 'url': 'https://example.com/synthetic-menu',
              'body_text': body, 'body_read': True, 'visible_text_complete': True,
              'text_sha256': 'synthetic-menu-no-persistence'}]
    requirements = FoodRequirements.model_validate({'all_of': [{'any_of': [
        {'kind': 'menu', 'name': '돈까스'}]}]})
    meter = usage.Meter(20_000)
    token = usage._meter.set(meter)
    result = {'model': os.getenv('LLM_MODEL'), 'synthetic': True,
              'search_calls': 0, 'page_fetches': 0, 'db_writes': 0}
    try:
        parsed = extract_pages(place, pages, requirements.conditions(), [],
                               timeout=20, policy=ResearchPolicy())
        facts = grounded_facts(parsed, place, pages, requirements.conditions(), [], now)
        result['status'] = food_report(facts, requirements, now)['status']
        result['verified_facts'] = len(facts)
        result['normalized_menu'] = [f['term'] for f in facts]
    except Exception as exc:
        result['error_type'] = type(exc).__name__
        result['status_code'] = getattr(exc, 'status_code', None)
    finally:
        usage._meter.reset(token)
    inputs, outputs, calls, unknown = meter.totals()
    result.update(input_tokens=inputs, output_tokens=outputs, model_calls=calls,
                  unknown_calls=unknown)
    if calls and not unknown:
        # Standard short-context list rate, before caching/discounts; not an invoice.
        result['estimated_usd_before_discounts'] = round(inputs * 0.10 / 1_000_000 + outputs * 0.50 / 1_000_000, 8)
    print(json.dumps(result, ensure_ascii=False))
    return result
