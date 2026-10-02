"""Live, approved evidence only. Never reuse an old recommendation as a fact.

Kakao identity is stored as an ID reference, not a copy of the provider's POI.
Fetched text and quotes never enter this module. Source approval is not inferred
from robots.txt, a search result, or model output.
"""
from datetime import timedelta
from hashlib import sha256
import json

from django.core.exceptions import ValidationError
from django.db import DatabaseError, transaction
from django.utils import timezone

from travel.place_knowledge import record_observation
from travel.place_knowledge_models import (
    KST, PlaceEnrichmentAttempt, PlaceKnowledge, PlaceKnowledgeObservation, PlaceKnowledgeSource,
)
from .review_requirements import REVIEW_LABELS, REVIEW_CATEGORIES


def digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def place_key(place):
    value = str(place['placeId'])
    return 'kakao:' + value if place.get('source') == 'KAKAO' and value.isdigit() else value


def criteria_key(place, food, reviews):
    return 'course-v1:' + digest([place_key(place), food.model_dump() if food else None,
                                 reviews.model_dump() if reviews else None])


class EvidenceMemory:
    def __init__(self, now=None):
        self.now = now or timezone.now()

    def load(self, place):
        try:
            ids = [place_key(place)]
            # Legacy numeric IDs may be used only with explicit Kakao provenance.
            if place.get('source') == 'KAKAO':
                ids += list(PlaceKnowledge.objects.filter(pk=str(place['placeId']),
                             base_source__icontains='kakao').values_list('pk', flat=True))
            rows = list(PlaceKnowledgeObservation.objects.filter(
                place_id__in=ids, review_status='accepted', same_place_verified=True,
                evidence_verified=True, checked_at__lte=self.now, valid_until__gt=self.now,
                source__fetched_at__lte=self.now, polarity__in=('positive', 'negative'),
            ).select_related('source').order_by('-checked_at', 'id')[:501])
            if len(rows) > 500:
                return [], 'memory_scan_limited'  # Never drop a possible contradiction.
            facts = []
            for row in rows:
                if not row.source.allows(row.attribute, self.now):
                    continue
                if row.source.access_method == 'web' and not row.body_read:
                    continue
                if row.source.published_on and not 0 <= (self.now.astimezone(KST).date() - row.source.published_on).days <= 180:
                    continue
                attribute, term = row.attribute, row.term
                if attribute == 'review_feature':
                    attribute = {**{v: k for k, v in REVIEW_LABELS.items()},
                                 '매장 청결': 'cleanliness', '매장 청결함': 'cleanliness'}.get(term)
                    if not attribute or row.qualifiers:
                        continue
                    term = ''
                if attribute not in ('menu', 'cuisine', *REVIEW_LABELS):
                    continue
                if attribute in REVIEW_LABELS and (
                    row.promotion != 'not_disclosed' or not row.observed_on
                    or not 0 <= (self.now.astimezone(KST).date() - row.observed_on).days <= 180
                ):
                    continue
                facts.append(dict(attribute=attribute, term=term, qualifiers=row.qualifiers,
                    polarity=row.polarity, basis=row.basis, url=row.source.url, kind=row.source.kind,
                    context=row.context, experience_key=row.experience_key, promotion=row.promotion,
                    observed_on=row.observed_on, observation_date_kind=row.observation_date_kind,
                    checked_at=row.checked_at, valid_until=row.valid_until,
                    summary=(row.summary + ':' + row.experience_key) if row.attribute == 'review_feature' else row.summary,
                    origin='stored', published_on=row.source.published_on))
            return facts, 'ok'
        except DatabaseError:
            return [], 'memory_unavailable'

    def cooling_down(self, place, food, reviews):
        try:
            return PlaceEnrichmentAttempt.objects.filter(
                attempt_key__startswith=criteria_key(place, food, reviews) + ':',
                next_retry_at__gt=self.now).exists()
        except DatabaseError:
            return True  # A DB failure must not buy repeated searches.

    def _place(self, place):
        key = place_key(place)
        row = PlaceKnowledge.objects.filter(pk=key).first()
        if row:
            return row
        # Deliberately no API name/address/coordinates in persistent storage.
        return PlaceKnowledge.objects.create(place_id=key, name=key, kind=place.get('kind', 'unknown'),
            base_source='kakao_reference' if place.get('source') == 'KAKAO' else 'course_reference',
            base_checked_at=self.now, stadium_scope='external')

    def save(self, place, facts):
        stored, skipped = 0, 0
        try:
            with transaction.atomic():
                target = self._place(place)
                for fact in facts:
                    if fact.get('origin') != 'live':
                        continue
                    # Existing generic review_feature storage; never store a
                    # subjective walk observation as an official access fact.
                    fact = dict(fact)
                    category = REVIEW_CATEGORIES.get(fact['attribute'])
                    if category:
                        fact.update(term=REVIEW_LABELS[fact['attribute']], attribute='review_feature')
                    # Exact source URL approval; never blanket-approve an entire platform.
                    policy = PlaceKnowledgeSource.objects.filter(url=fact['url'],
                        policy_checked_at__isnull=False).order_by('-policy_checked_at', '-fetched_at', '-id').first()
                    if policy and not policy.allows(fact['attribute'], self.now):
                        policy = None  # A newer denial must not revive an older approval.
                    version = 'course-source-v1:' + digest([fact['url'], fact['kind'], fact['body_hash'],
                                                           self.now.date().isoformat()])
                    source = PlaceKnowledgeSource.objects.filter(source_key=version).first()
                    if source is None:
                        source = PlaceKnowledgeSource.objects.create(
                            source_key=version, provider='serper_public_page', url=fact['url'],
                            kind=fact['kind'], access_method='web', fetched_at=self.now,
                            published_on=fact.get('published_on'),
                            storage_policy=policy.storage_policy if policy else 'unreviewed',
                            allowed_attributes=policy.allowed_attributes if policy else [],
                            policy_reference=policy.policy_reference if policy else '',
                            policy_checked_at=policy.policy_checked_at if policy else None,
                            retention_until=policy.retention_until if policy else None)
                    if not policy or not source.allows(fact['attribute'], self.now):
                        skipped += 1
                        continue
                    key = 'course-fact-v1:' + digest([target.pk, str(source.pk), fact['attribute'],
                        fact['term'], fact['qualifiers'], fact['polarity'], fact['context'], fact['experience_key']])
                    if PlaceKnowledgeObservation.objects.filter(ingest_key=key).exists():
                        continue  # Replays never renew freshness.
                    fields = {k: fact[k] for k in ('attribute', 'term', 'qualifiers', 'polarity', 'basis',
                        'context', 'experience_key', 'promotion', 'observed_on', 'observation_date_kind',
                        'checked_at', 'valid_until', 'summary')}
                    if category:
                        fields['review_category'] = category
                    record_observation(place=target, source=source, ingest_key=key,
                        same_place_verified=True, evidence_verified=True, body_read=True,
                        review_status='accepted', extractor_version='course-serper-luna-v1', **fields)
                    stored += 1
            return {'stored': stored, 'policy_skipped': skipped, 'raw_content_stored': False}
        except (DatabaseError, ValidationError):
            return {'stored': 0, 'policy_skipped': skipped, 'status': 'memory_write_unavailable',
                    'raw_content_stored': False}

    def record_attempt(self, place, food, reviews, *, searches, models, status):
        try:
            with transaction.atomic():
                target = self._place(place)
                finished = timezone.now()
                PlaceEnrichmentAttempt.objects.create(
                    attempt_key=criteria_key(place, food, reviews) + ':' + digest(finished.isoformat())[:20],
                    place=target, attribute='menu' if food else ('review_feature' if
                        reviews.all_of[0].aspect in REVIEW_CATEGORIES else reviews.all_of[0].aspect),
                    term=next(iter(food.conditions().values())).name if food else REVIEW_LABELS[reviews.all_of[0].aspect],
                    status='api_error' if status == 'unavailable' else 'completed' if status == 'ready' else 'no_evidence',
                    reason_code='course_evidence_' + status, search_calls=searches, model_calls=models,
                    started_at=self.now, finished_at=max(finished, self.now),
                    next_retry_at=max(finished, self.now) + timedelta(minutes=10 if status == 'unavailable' else 360))
        except (DatabaseError, ValidationError):
            return False
        return True
