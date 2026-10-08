import uuid
from urllib.parse import urlsplit

from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import models
from django.db.models import F, Q


def validate_ad_url(value):
    URLValidator(schemes=["https"])(value)
    parsed = urlsplit(value)
    if parsed.username or parsed.password:
        raise ValidationError("인증 정보가 포함된 URL은 사용할 수 없습니다.")


class AdPlacement(models.TextChoices):
    CLUB = "home-club-banner", "홈 구단 광고"
    PARTNER = "home-route-partner-banner", "홈 음식점·시설 광고"


class AdCreative(models.Model):
    advertiser = models.CharField("광고주", max_length=120)
    title = models.CharField("제목", max_length=120)
    description = models.CharField("설명", max_length=250, blank=True)
    image_url = models.URLField("이미지 URL", max_length=1000, validators=[validate_ad_url])
    image_alt = models.CharField("이미지 설명", max_length=250, blank=True)
    destination_url = models.URLField("이동 URL", max_length=1000, validators=[validate_ad_url])
    button_label = models.CharField("버튼 문구", max_length=40, default="자세히 보기")
    context_label = models.CharField("지역·분류", max_length=120, blank=True)
    placement = models.CharField("위치", max_length=40, choices=AdPlacement.choices)
    starts_at = models.DateTimeField("시작 일시")
    ends_at = models.DateTimeField("종료 일시")
    active = models.BooleanField("활성", default=False)
    is_test = models.BooleanField("테스트 광고", default=False)
    priority = models.PositiveSmallIntegerField("우선순위", default=0)
    team_code = models.CharField("구단 코드", max_length=10, blank=True)
    place = models.ForeignKey("travel.Place", verbose_name="제휴 장소", null=True, blank=True, on_delete=models.SET_NULL)
    courses = models.ManyToManyField("travel.Course", verbose_name="노출 대상 코스", blank=True, related_name="advertisements")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-priority", "-id")
        constraints = [models.CheckConstraint(condition=Q(ends_at__gt=F("starts_at")), name="ads_valid_period")]
        indexes = [models.Index(fields=["placement", "active", "starts_at", "ends_at"], name="ads_delivery_lookup")]

    def clean(self):
        super().clean()
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValidationError({"ends_at": "종료 일시는 시작 일시보다 뒤여야 합니다."})
        if self.placement == AdPlacement.PARTNER and not self.place_id:
            raise ValidationError({"place": "음식점·시설 광고에는 제휴 장소가 필요합니다."})

    def __str__(self):
        return self.title


class AdEvent(models.Model):
    class Kind(models.TextChoices):
        IMPRESSION = "impression", "노출"
        CLICK = "click", "클릭"

    event_id = models.UUIDField(default=uuid.uuid4, unique=True)
    exposure_id = models.UUIDField()
    ad = models.ForeignKey(AdCreative, on_delete=models.PROTECT, related_name="events")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    placement = models.CharField(max_length=40, choices=AdPlacement.choices)
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["exposure_id", "kind"], name="ads_unique_exposure_kind")]
        indexes = [models.Index(fields=["ad", "kind", "received_at"], name="ads_event_report")]
