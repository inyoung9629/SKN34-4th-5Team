"""Internal storage/read boundary only. No crawlers, model calls or RAG writes."""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .place_knowledge_models import KST, REVIEW_ATTRIBUTES, PlaceKnowledgeObservation, aware


def record_observation(**values):
    """Idempotent insertion; changed content needs a new ingestion key.

    A trusted ingestion worker supplies verified flags. This is deliberately
    not an HTTP/LLM tool and does not blindly import model-generated JSON.
    """
    proposed = PlaceKnowledgeObservation(**values)
    with transaction.atomic():
        # Serialize writers for the same place, including empty observation sets.
        from .place_knowledge_models import PlaceKnowledge, PlaceKnowledgeSource
        PlaceKnowledge.objects.select_for_update().get(pk=proposed.place_id)
        proposed.source = PlaceKnowledgeSource.objects.select_for_update().get(pk=proposed.source_id)
        existing = PlaceKnowledgeObservation.objects.filter(ingest_key=proposed.ingest_key).first()
        if existing:
            fields = [f.attname for f in proposed._meta.concrete_fields if f.name not in ("id", "created_at")]
            if any(getattr(existing, field) != getattr(proposed, field) for field in fields):
                raise ValidationError("같은 수집 키로 다른 내용을 덮어쓸 수 없습니다. 새 버전 키를 사용하세요.")
            return existing, False
        proposed.save()
        return proposed, True


def record_review_observations(*, place, source, observations):
    """Store ALL supplied features from one reviewed experience, not a query subset.

    A trusted upstream worker must extract, normalize and verify each feature.
    This function neither reads review prose nor calls a model. The shared source
    and experience key prevent multiple features from becoming independent reviews.
    Idempotency keys belong to individual observations; a batch is atomic.
    """
    if not isinstance(observations, list) or any(not isinstance(v, dict) for v in observations):
        raise ValidationError("후기 관찰은 객체 목록으로 전달하세요.")
    experience_keys = set()
    for values in observations:
        if values.get("attribute") not in REVIEW_ATTRIBUTES:
            raise ValidationError("후기 묶음에는 후기 관찰만 넣을 수 있습니다.")
        if any(key in values for key in ("place", "place_id", "source", "source_id")):
            raise ValidationError("한 후기의 장소와 출처는 공통 인자로만 전달하세요.")
        experience = values.get("experience_key")
        if not isinstance(experience, str) or not experience.strip():
            raise ValidationError("한 후기의 모든 관찰에 동일한 경험 식별 키가 필요합니다.")
        experience_keys.add(experience)
    if len(experience_keys) > 1:
        raise ValidationError("서로 다른 방문 경험은 별도 후기 묶음으로 저장하세요.")
    with transaction.atomic():
        return [record_observation(place=place, source=source, **values) for values in observations]


def context_applies(context, arrival=None, departure=None):
    if (arrival is None) != (departure is None):
        raise ValidationError("방문 시작과 종료 시각을 함께 전달하세요.")
    if arrival is not None:
        aware(arrival, "arrival")
        aware(departure, "departure")
        if arrival >= departure:
            return False
    if context["scope"] == "general":
        return True
    if context["scope"] != "limited" or arrival is None or departure is None:
        return False
    arrival, departure = arrival.astimezone(KST), departure.astimezone(KST)
    if arrival >= departure or arrival.date() != departure.date():
        return False
    if context["weekdays"] and arrival.weekday() not in context["weekdays"]:
        return False
    if context["start_minute"] is None:
        return True
    start = arrival.hour * 60 + arrival.minute + arrival.second / 60 + arrival.microsecond / 60000000
    end = departure.hour * 60 + departure.minute + departure.second / 60 + departure.microsecond / 60000000
    return context["start_minute"] <= start and end <= context["end_minute"]


def reusable_observations(place_id, *, attribute, term="", now=None, arrival=None, departure=None,
                          review_conditions=()):
    """Return evidence rows, NOT a final matched/quiet/clean verdict.

    Consumers must deduplicate experiences, evaluate positive/negative conflicts,
    check food qualifiers, and enforce the required number of independent reviews.
    A lone positive review never establishes a confirmed trait in this module.
    """
    from .place_knowledge_models import ATTRIBUTES
    if attribute not in ATTRIBUTES:
        raise ValidationError("지원하지 않는 속성입니다.")
    # None explicitly asks for every review feature. Never silently broaden menu queries.
    if term is None and attribute != "review_feature":
        raise ValidationError("전체 키워드 조회는 후기 키워드 속성에서만 지원합니다.")
    if term is not None and not isinstance(term, str):
        raise ValidationError("조회 명칭은 문자열이어야 합니다.")
    if attribute == "review_feature" and term is not None and not term.strip():
        raise ValidationError("후기 키워드를 지정하거나 전체 조회에는 term=None을 사용하세요.")
    if (not isinstance(review_conditions, (list, tuple))
            or any(not isinstance(v, str) or not v.strip() or v != v.strip() or len(v) > 80
                   for v in review_conditions)):
        raise ValidationError("확인된 후기 적용 조건을 짧은 문자열 목록으로 전달하세요.")
    now = now or timezone.now()
    aware(now, "now")
    rows = PlaceKnowledgeObservation.objects.filter(
        place_id=place_id, attribute=attribute, review_status="accepted",
        same_place_verified=True, evidence_verified=True, checked_at__lte=now,
        source__fetched_at__lte=now, valid_until__gt=now,
        polarity__in=("positive", "negative"),
    ).select_related("source").order_by("checked_at", "id")
    if term is not None:
        rows = rows.filter(term=term.strip())
    result = []
    for row in rows:
        if not row.source.allows(attribute, now) or not context_applies(row.context, arrival, departure):
            continue
        if attribute in REVIEW_ATTRIBUTES:
            if (row.promotion != "not_disclosed" or row.observed_on is None
                    or not 0 <= (now.astimezone(KST).date() - row.observed_on).days <= 180):
                continue
            if attribute == "review_feature" and not set(row.qualifiers).issubset(review_conditions):
                continue
        result.append(row)
    return result
