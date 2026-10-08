"""Structured place knowledge, independent of crawling, chat and vector indexes.

An accepted observation means its evidence was checked, NOT that one review
establishes a place-wide trait. Writes must use validated saves, not bulk writes.
"""
import ipaddress
import re
import uuid
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q
from django.utils import timezone


KST = ZoneInfo("Asia/Seoul")
REVIEW_ATTRIBUTES = ("quietness", "cleanliness", "review_feature")
ATTRIBUTES = ("cuisine", "menu", *REVIEW_ATTRIBUTES)
ATTRIBUTE_CHOICES = [(value, value) for value in ATTRIBUTES]
REVIEW_CATEGORIES = (
    "atmosphere", "cleanliness", "space", "service", "value", "food",
    "facilities", "access", "suitability", "other",
)
REVIEW_CATEGORY_CHOICES = [(value, value) for value in REVIEW_CATEGORIES]


def unknown_context():
    return {"scope": "unknown", "weekdays": [], "start_minute": None, "end_minute": None}


def validate_context(value):
    if not isinstance(value, dict) or set(value) != set(unknown_context()):
        raise ValidationError("적용 맥락은 scope, weekdays, start_minute, end_minute만 사용합니다.")
    scope, days = value["scope"], value["weekdays"]
    start, end = value["start_minute"], value["end_minute"]
    if scope not in ("general", "limited", "unknown"):
        raise ValidationError("적용 맥락 상태를 확인하세요.")
    if (not isinstance(days, list) or len(days) > 7
            or any(type(day) is not int or day not in range(7) for day in days)
            or len(set(days)) != len(days)):
        raise ValidationError("요일은 중복 없이 월요일 0부터 일요일 6까지 기록합니다.")
    if (start is None) != (end is None):
        raise ValidationError("적용 시간은 시작과 끝을 함께 기록합니다.")
    if start is not None and (type(start) is not int or type(end) is not int or not 0 <= start < end <= 1440):
        raise ValidationError("시간 구간은 같은 날의 분 단위입니다. 자정 넘김은 아직 지원하지 않습니다.")
    if scope == "general" and (days or start is not None):
        raise ValidationError("제한된 맥락을 일반적인 특징으로 저장할 수 없습니다.")
    if scope == "limited" and not days and start is None:
        raise ValidationError("제한된 맥락에는 요일 또는 시간 구간이 필요합니다.")


def aware(value, label):
    if value is not None and timezone.is_naive(value):
        raise ValidationError({label: "시간대가 포함된 시각을 사용하세요."})


def validate_reference_url(value):
    """A citation validator only. It does not authorize fetching or perform DNS."""
    if not value:
        return
    try:
        p = urlsplit(value)
        host = (p.hostname or "").lower().rstrip(".")
        if (p.scheme not in ("http", "https") or not host or p.username or p.password
                or any(c.isspace() or ord(c) < 32 for c in value)
                or host == "localhost" or host.endswith((".localhost", ".local", ".internal"))):
            raise ValueError()
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if "." not in host or re.fullmatch(r"[0-9.:]+", host):
                raise ValueError()
        else:
            if not address.is_global:
                raise ValueError()
    except ValueError as exc:
        raise ValidationError("공개 출처 URL만 기록합니다. 인증값과 내부 주소는 넣지 마세요.") from exc


class ValidatedKnowledgeModel(models.Model):
    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        self.full_clean()
        fields = getattr(self, "immutable_fields", ())
        if not self._state.adding and fields:
            original = type(self).objects.values(*fields).get(pk=self.pk)
            if any(original[field] != getattr(self, field) for field in fields):
                raise ValidationError("저장된 근거 내용을 덮어쓸 수 없습니다. 새 버전으로 기록하세요.")
        return super().save(*args, **kwargs)


class PlaceKnowledge(ValidatedKnowledgeModel):
    # Reuse collected:SBIZ:..., stadium-facility:..., etc. No name-based auto-merge.
    place_id = models.CharField(primary_key=True, max_length=255)
    name = models.CharField(max_length=255)
    branch_name = models.CharField(max_length=120, blank=True)
    address = models.CharField(max_length=500, blank=True)
    lat = models.FloatField(null=True, blank=True)
    lng = models.FloatField(null=True, blank=True)
    kind = models.CharField(max_length=16, choices=[(v, v) for v in (
        "food", "cafe", "lodging", "walk", "indoor", "store", "facility", "unknown")], default="unknown")
    base_source = models.CharField(max_length=120)
    base_version = models.CharField(max_length=120, blank=True)
    base_checked_at = models.DateTimeField()
    stadium_scope = models.CharField(max_length=24, choices=[(v, v) for v in (
        "unknown", "external", "stadium_internal", "stadium_external")], default="unknown")
    stadium_code = models.CharField(max_length=30, blank=True)
    floor = models.CharField(max_length=40, blank=True)
    zone = models.CharField(max_length=100, blank=True)
    requires_ticket = models.BooleanField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=(Q(lat__isnull=True) & Q(lng__isnull=True)) | (Q(lat__isnull=False) & Q(lng__isnull=False)), name="pk_coordinates_paired"),
            models.CheckConstraint(condition=Q(lat__isnull=True) | Q(lat__range=(-90, 90)), name="pk_lat_bounds"),
            models.CheckConstraint(condition=Q(lng__isnull=True) | Q(lng__range=(-180, 180)), name="pk_lng_bounds"),
        ]
        indexes = [models.Index(fields=["kind", "stadium_scope"], name="pk_kind_scope_idx")]

    def clean(self):
        if not self.place_id or self.place_id.strip() != self.place_id or any(c.isspace() for c in self.place_id):
            raise ValidationError({"place_id": "기존 장소 ID를 변경 없이 사용하세요."})
        aware(self.base_checked_at, "base_checked_at")
        if self.stadium_scope.startswith("stadium_") and not self.stadium_code:
            raise ValidationError({"stadium_code": "구장 소속 시설은 구장 코드가 필요합니다."})


class PlaceKnowledgeSource(ValidatedKnowledgeModel):
    immutable_fields = ("source_key", "provider", "url", "kind", "access_method", "fetched_at", "published_on")
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Producer supplies a versioned opaque key, e.g. provider/document/revision.
    source_key = models.CharField(max_length=255, unique=True)
    provider = models.CharField(max_length=80)
    url = models.URLField(max_length=2000, blank=True, validators=[validate_reference_url])
    kind = models.CharField(max_length=24, choices=[(v, v) for v in (
        "public_dataset", "official", "menu_listing", "customer_review", "platform_summary", "other")])
    access_method = models.CharField(max_length=10, choices=[(v, v) for v in ("api", "web", "manual")])
    fetched_at = models.DateTimeField()
    published_on = models.DateField(null=True, blank=True)
    storage_policy = models.CharField(max_length=12, choices=[(v, v) for v in (
        "unreviewed", "allowed", "temporary", "blocked")], default="unreviewed")
    allowed_attributes = models.JSONField(default=list, blank=True)
    policy_reference = models.CharField(max_length=500, blank=True)
    policy_checked_at = models.DateTimeField(null=True, blank=True)
    retention_until = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.CheckConstraint(
            condition=~Q(storage_policy="temporary") | Q(retention_until__isnull=False),
            name="pks_temporary_expiry")]

    def clean(self):
        for name in ("fetched_at", "policy_checked_at", "retention_until"):
            aware(getattr(self, name), name)
        values = self.allowed_attributes
        if (not isinstance(values, list) or any(v not in ATTRIBUTES for v in values)
                or len(values) != len(set(values))):
            raise ValidationError({"allowed_attributes": "1차 지원 속성을 중복 없이 지정하세요."})
        if self.access_method == "web" and not self.url:
            raise ValidationError({"url": "웹 출처는 실제 출처 URL이 필요합니다."})
        if self.storage_policy in ("allowed", "temporary"):
            if not self.policy_reference.strip() or not self.policy_checked_at or not values:
                raise ValidationError("저장 조건의 검토 근거, 확인 시각, 허용 속성이 필요합니다.")
        if self.retention_until and self.fetched_at and self.retention_until <= self.fetched_at:
            raise ValidationError({"retention_until": "보관 만료는 수집 시각 이후여야 합니다."})

    def allows(self, attribute, at):
        return (self.storage_policy in ("allowed", "temporary") and attribute in self.allowed_attributes
                and bool(self.policy_reference) and self.policy_checked_at is not None
                and self.policy_checked_at <= at
                and (self.storage_policy != "temporary" or self.retention_until is not None)
                and (self.retention_until is None or at < self.retention_until))


class PlaceKnowledgeObservation(ValidatedKnowledgeModel):
    immutable_fields = ("ingest_key", "place_id", "source_id", "attribute", "term", "observed_label", "qualifiers", "review_category",
                        "polarity", "basis", "summary", "same_place_verified", "evidence_verified", "body_read",
                        "experience_key", "promotion", "observed_on", "observation_date_kind", "context", "checked_at", "valid_until",
                        "extractor_version")
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ingest_key = models.CharField(max_length=120, unique=True)
    place = models.ForeignKey(PlaceKnowledge, on_delete=models.CASCADE, related_name="observations")
    source = models.ForeignKey(PlaceKnowledgeSource, on_delete=models.PROTECT, related_name="observations")
    attribute = models.CharField(max_length=16, choices=ATTRIBUTE_CHOICES)
    term = models.CharField(max_length=100, blank=True)
    observed_label = models.CharField(max_length=100, blank=True)
    # Open vocabulary in term; adding a grounded keyword needs no schema change.
    review_category = models.CharField(max_length=16, choices=REVIEW_CATEGORY_CHOICES, blank=True)
    qualifiers = models.JSONField(default=list, blank=True)
    polarity = models.CharField(max_length=10, choices=[(v, v) for v in ("positive", "negative", "neutral", "unknown")], default="unknown")
    basis = models.CharField(max_length=24, choices=[(v, v) for v in (
        "menu_listing", "official_statement", "public_classification", "customer_experience",
        "explicit_non_sale", "inference", "insufficient")], default="insufficient")
    summary = models.CharField(max_length=400, blank=True)
    review_status = models.CharField(max_length=12, choices=[(v, v) for v in (
        "pending", "accepted", "rejected", "retracted")], default="pending")
    same_place_verified = models.BooleanField(default=False)
    evidence_verified = models.BooleanField(default=False)
    body_read = models.BooleanField(default=False)
    experience_key = models.CharField(max_length=120, blank=True)
    promotion = models.CharField(max_length=16, choices=[(v, v) for v in (
        "disclosed", "not_disclosed", "unknown")], default="unknown")
    observed_on = models.DateField(null=True, blank=True)
    observation_date_kind = models.CharField(max_length=10, choices=[(v, v) for v in (
        "visited", "published", "unknown")], default="unknown")
    context = models.JSONField(default=unknown_context, validators=[validate_context])
    checked_at = models.DateTimeField()
    valid_until = models.DateTimeField(null=True, blank=True)
    extractor_version = models.CharField(max_length=120)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["place", "attribute", "term", "review_status"], name="pko_lookup_idx")]
        constraints = [
            models.CheckConstraint(condition=Q(valid_until__isnull=True) | Q(valid_until__gt=F("checked_at")), name="pko_validity_order"),
            models.CheckConstraint(condition=~Q(review_status="accepted") | (Q(same_place_verified=True) & Q(evidence_verified=True) & Q(valid_until__isnull=False)), name="pko_accepted_evidence"),
        ]

    def clean(self):
        aware(self.checked_at, "checked_at")
        aware(self.valid_until, "valid_until")
        self.term = self.term.strip()
        if self.attribute in ("menu", "cuisine") and not self.term:
            raise ValidationError({"term": "메뉴와 음식 분류에는 정규화된 명칭이 필요합니다."})
        if self.attribute in ("quietness", "cleanliness") and (self.term or self.qualifiers):
            raise ValidationError("후기 속성의 세부 상황은 term 대신 context에 기록합니다.")
        if self.attribute == "review_feature":
            if not self.term or not self.review_category:
                raise ValidationError("후기 키워드는 정규화 명칭과 분류가 필요합니다.")
        elif self.review_category:
            raise ValidationError({"review_category": "후기 키워드 속성에만 분류를 지정합니다."})
        if (not isinstance(self.qualifiers, list) or len(self.qualifiers) > 8
                or any(not isinstance(v, str) or not v.strip() or len(v) > 80 for v in self.qualifiers)):
            raise ValidationError({"qualifiers": "메뉴 세부 조건 또는 후기 적용 조건은 짧은 문자열 목록으로 기록합니다."})
        if self.attribute == "review_feature" and (
                any(v != v.strip() for v in self.qualifiers) or len(set(self.qualifiers)) != len(self.qualifiers)):
            raise ValidationError({"qualifiers": "후기 적용 조건은 앞뒤 공백과 중복 없이 기록합니다."})
        if not self.source_id or not self.checked_at:
            return
        try:
            # Read current policy, not a caller's potentially stale related object.
            source = PlaceKnowledgeSource.objects.get(pk=self.source_id)
        except PlaceKnowledgeSource.DoesNotExist as exc:
            raise ValidationError({"source": "등록된 출처가 필요합니다."}) from exc
        # Retraction/rejection remains possible after a source's policy is revoked.
        status_only_removal = not self._state.adding and self.review_status in ("rejected", "retracted")
        if not status_only_removal and not source.allows(self.attribute, timezone.now()):
            raise ValidationError({"source": "해당 속성의 저장 조건이 확인되지 않았거나 보관 기간이 끝났습니다."})
        if source.fetched_at > self.checked_at:
            raise ValidationError({"checked_at": "근거 확인 시각은 출처 수집 시각보다 빠를 수 없습니다."})
        if self.observed_on and self.observed_on > self.checked_at.astimezone(KST).date():
            raise ValidationError({"observed_on": "관찰 날짜를 미래 날짜로 기록할 수 없습니다."})
        if self.review_status != "accepted":
            return
        if not self.summary.strip() or self.basis in ("inference", "insufficient"):
            raise ValidationError("추측·근거 부족은 accepted 근거로 저장할 수 없습니다.")
        if source.access_method == "web" and not self.body_read:
            raise ValidationError("검색 요약만으로 웹 근거를 검증 완료 처리할 수 없습니다.")
        if self.attribute == "menu":
            if source.kind not in ("official", "menu_listing"):
                raise ValidationError("메뉴 판매 근거는 해당 지점의 공식 정보 또는 메뉴 목록이어야 합니다.")
            if self.polarity == "negative" and self.basis != "explicit_non_sale":
                raise ValidationError("목록에서 못 찾았다는 이유로 미판매를 확정할 수 없습니다.")
            if self.polarity == "positive" and self.basis not in ("menu_listing", "official_statement"):
                raise ValidationError("판매 메뉴를 직접 뒷받침하는 근거가 필요합니다.")
        elif self.attribute == "cuisine":
            if source.kind not in ("public_dataset", "official", "menu_listing") or self.basis not in (
                    "public_classification", "official_statement", "menu_listing"):
                raise ValidationError("음식 분류를 뒷받침하는 자료가 필요합니다.")
        else:
            if source.kind != "customer_review" or self.basis != "customer_experience":
                raise ValidationError("플랫폼 태그·업주 홍보를 실제 이용 후기로 확정할 수 없습니다.")
            if not self.experience_key or self.observed_on is None or self.observation_date_kind == "unknown":
                raise ValidationError("후기에는 독립 경험 식별 키와 실제 날짜 및 날짜 종류가 필요합니다.")


class PlaceEnrichmentAttempt(ValidatedKnowledgeModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    attempt_key = models.CharField(max_length=120, unique=True)
    place = models.ForeignKey(PlaceKnowledge, on_delete=models.CASCADE, related_name="enrichment_attempts")
    attribute = models.CharField(max_length=16, choices=ATTRIBUTE_CHOICES)
    term = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=16, choices=[(v, v) for v in (
        "completed", "no_evidence", "blocked", "api_error")])
    reason_code = models.CharField(max_length=64)
    search_calls = models.PositiveIntegerField(default=0)
    model_calls = models.PositiveIntegerField(default=0)
    search_credits = models.PositiveIntegerField(null=True, blank=True)
    model_cost_usd = models.DecimalField(max_digits=12, decimal_places=8, null=True, blank=True)
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField()
    next_retry_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["place", "attribute", "term", "finished_at"], name="pka_lookup_idx")]
        constraints = [
            models.CheckConstraint(condition=Q(finished_at__gte=F("started_at")), name="pka_time_order"),
            models.CheckConstraint(condition=Q(model_cost_usd__isnull=True) | Q(model_cost_usd__gte=0), name="pka_cost_nonnegative"),
            models.CheckConstraint(condition=Q(next_retry_at__isnull=True) | Q(next_retry_at__gte=F("finished_at")), name="pka_retry_order"),
        ]

    def clean(self):
        for name in ("started_at", "finished_at", "next_retry_at"):
            aware(getattr(self, name), name)
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", self.reason_code):
            raise ValidationError({"reason_code": "오류 원문 대신 짧은 고정 사유 코드를 사용하세요."})
        if self.attribute in ("menu", "cuisine") and not self.term.strip():
            raise ValidationError({"term": "메뉴·분류 조사 이력에는 대상 명칭이 필요합니다."})
