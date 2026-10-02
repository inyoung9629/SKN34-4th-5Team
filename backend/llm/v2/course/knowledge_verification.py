"""Local-first adapters for the planner's existing food/review contracts."""
from datetime import datetime
from types import SimpleNamespace

from .evidence_memory import EvidenceMemory, digest, place_key
from .food_requirements import condition_label
from .food_verification import public_url
from .itinerary_request import KST
from .review_verification import evaluate_reviews
from .review_requirements import REVIEW_LABELS
from .serper_evidence import SerperResearch


def external(place):
    scope = (place.get('stadiumAffiliation') or {}).get('scope')
    return (scope not in ('internal', 'excluded_complex', 'stadium_unknown', 'unknown', 'stadium_exterior')
            and not str(place['placeId']).startswith('stadium-facility:'))


def food_report(facts, requirements, now):
    details, used, verdicts = [], {}, {}
    for key, term in requirements.conditions().items():
        rows = [f for f in facts if f['attribute'] == term.kind and f['term'] == term.name
                and set(f['qualifiers']) == set(term.qualifiers) and f['context']['scope'] == 'general'
                and f['kind'] in ('official', 'menu_listing', 'public_dataset')
                and (term.kind != 'menu' or f['kind'] != 'public_dataset')]
        positive = [f for f in rows if f['polarity'] == 'positive']
        negative = [f for f in rows if f['polarity'] == 'negative' and f['basis'] == 'explicit_non_sale']
        verdict = 'unknown' if bool(positive) == bool(negative) else 'pass' if positive else 'fail'
        if term.exclude and verdict != 'unknown':
            verdict = 'fail' if verdict == 'pass' else 'pass'
        verdicts[key] = verdict
        sources = sorted({f['url'] for f in rows if public_url(f['url'])})
        details.append({'id': key, 'label': condition_label(term), 'status': verdict, 'source_urls': sources})
        for f in rows:
            if public_url(f['url']):
                used[f['url']] = {'url': f['url'], 'kind': f['kind'], 'summary': f['summary'],
                                  'checked_at': f['checked_at'].isoformat()}
    status = requirements.evaluate(verdicts)
    checks = [f['checked_at'] for f in facts if f['url'] in used]
    return {'status': status, 'reason': 'food_' + {'pass': 'matched', 'fail': 'mismatch', 'unknown': 'unverified'}[status],
            'conditions': details, 'sources': list(used.values()),
            'checked_at': min(checks).isoformat() if checks else now.isoformat(),
            'method': 'stored_and_serper_evidence' if any(f.get('origin') == 'live' for f in facts) else 'stored_evidence'}


def review_report(facts, now):
    observations, sources, checks = [], {}, []
    for f in facts:
        if f['attribute'] not in REVIEW_LABELS or not public_url(f['url']):
            continue
        context = f['context']
        observations.append({'url': f['url'], 'aspect': f['attribute'], 'polarity': f['polarity'],
            'experience_key': f['experience_key'], 'summary': f['summary'],
            'context': context['scope'], 'weekdays': context['weekdays'],
            'start_minute': context['start_minute'], 'end_minute': context['end_minute']})
        sources[f['url']] = {'url': f['url'], 'body_read': True, 'summary': f['summary'],
                              'checked_at': f['checked_at'].isoformat()}
        checks.append(f['checked_at'])
    return {'identity_verified': bool(observations), 'reason': 'review_evidence_ready' if observations else 'review_unverified',
            'observations': observations, 'sources': list(sources.values()),
            'checked_at': min(checks).isoformat() if checks else now.isoformat(),
            'method': 'stored_and_serper_evidence' if any(f.get('origin') == 'live' for f in facts) else
                      'stored_evidence' if observations else 'local_knowledge_unverified'}


class KnowledgeVerification:
    """One shared memory/research scope per itinerary, never one budget per verifier."""
    def __init__(self, request, *, web_enabled=False, memory=None, research=None, now=None):
        self.now = now or datetime.now(KST)
        self.memory = memory or EvidenceMemory(self.now)
        self.research = research or SerperResearch(now=self.now)
        self.web_enabled = web_enabled
        self.allow_web = False  # Service must first exhaust a stored-evidence planning pass.
        self.cache, self.attempted = {}, set()
        self.memory_hits = 0
        self.writes = []
        self.trace = []
        self.request = request
        self.reuse_only_ids = set()
        self.policy = SimpleNamespace(wall_seconds=self.research.policy.wall_seconds if web_enabled else 0)

    def load(self, place):
        key = place_key(place)
        if key not in self.cache:
            self.cache[key] = self.memory.load(place)
        return self.cache[key]

    def ensure(self, place, food=None, reviews=None):
        if not external(place):
            return [], 'internal_restaurant'
        facts, memory_status = self.load(place)
        if food and food_report(facts, food, self.now)['status'] == 'fail':
            return facts, 'ready'
        def sufficient(extra):
            combined = facts + extra
            return ((not food or food_report(combined, food, self.now)['status'] != 'unknown')
                    and (not reviews or evaluate_reviews(review_report(combined, self.now), reviews)['status'] != 'unknown'))
        if sufficient([]):
            self.memory_hits += 1
            return facts, 'ready'
        if not self.allow_web or not self.web_enabled or str(place['placeId']) in self.reuse_only_ids:
            return facts, 'missing'
        if memory_status != 'ok':
            return facts, 'unavailable'  # Failed cache is not a reason to buy searches.
        key = digest([place_key(place), food.model_dump() if food else None, reviews.model_dump() if reviews else None])
        if key in self.attempted or self.memory.cooling_down(place, food, reviews):
            return facts, 'missing'
        self.attempted.add(key)
        conditions = food.conditions() if food else {}
        if food:
            missing_ids = {c['id'] for c in food_report(facts, food, self.now)['conditions'] if c['status'] == 'unknown'}
            conditions = {k: v for k, v in conditions.items() if k in missing_ids}
        aspects = []
        if reviews:
            aspects = [c['aspect'] for c in evaluate_reviews(review_report(facts, self.now), reviews)['conditions']
                       if c['status'] in ('unknown', 'mixed')]
        before_searches, before_models = self.research.searches, self.research.models
        fresh, status = self.research.run(place, conditions, aspects, sufficient=sufficient)
        unique = {digest([f['url'], f['attribute'], f['term'], f['qualifiers'], f['polarity'], f['context'], f['summary']]): f
                  for f in [*facts, *fresh]}
        combined = list(unique.values())
        self.cache[place_key(place)] = combined, memory_status
        if fresh:
            self.writes.append(self.memory.save(place, fresh))
        searches = self.research.searches-before_searches
        models = self.research.models-before_models
        if searches:
            self.memory.record_attempt(place, food, reviews, searches=searches, models=models, status=status)
        self.trace.append({'place_id': str(place['placeId']), 'status': status})
        return combined, status

    def audit(self):
        return {**self.research.audit(), 'approved_stored_observations_connected': True,
                'memory_hits': self.memory_hits, 'memory_writes': self.writes,
                'lookups': self.research.searches, 'completed_tool_calls': 0,
                'max_lookups': self.research.policy.max_searches, 'shared_turn_budget': True}


class KnowledgeFoodVerifier:
    def __init__(self, session):
        self.session, self.policy = session, session.policy

    def verify(self, place, requirements):
        # Combine food and review research for the same stop to avoid duplicate searches.
        reviews = next((s.reviews for s in self.session.request.stops
                        if s.kind == place.get('kind') and s.food == requirements and s.reviews), None)
        facts, reason = self.session.ensure(place, food=requirements, reviews=reviews)
        report = food_report(facts, requirements, self.session.now)
        if reason == 'internal_restaurant':
            report.update(status='fail', reason=reason)
        elif report['status'] == 'unknown' and reason in ('limited', 'unavailable'):
            report['reason'] = 'food_search_' + reason
        return report

    def audit(self):
        return self.session.audit()


class KnowledgeReviewVerifier:
    def __init__(self, session):
        self.session = session
        # Research shares the food budget, not an additional 45 seconds.
        self.policy = SimpleNamespace(wall_seconds=0 if any(s.food for s in session.request.stops) else session.policy.wall_seconds)

    def verify(self, place, requirements):
        facts, reason = self.session.ensure(place, reviews=requirements)
        report = review_report(facts, self.session.now)
        if self.session.research.places.get(str(place['placeId']), {}).get('searches'):
            report['method'] = 'stored_and_serper_evidence'
        if reason == 'internal_restaurant':
            report['reason'] = reason
        elif reason in ('limited', 'unavailable'):
            report['reason'] = 'review_search_' + reason
        return report

    def audit(self):
        return self.session.audit()
