import math
import uuid
from datetime import timedelta

from django.core import signing
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from travel.models import Course
from .models import AdCreative, AdEvent, AdPlacement

TOKEN_SALT = "ads.delivery.v1"
TOKEN_SECONDS = 3600


def resolve_courses(route_ids):
    ids = []
    for value in route_ids:
        try:
            ids.append(uuid.UUID(value))
        except (ValueError, TypeError, AttributeError):
            pass
    return Course.objects.filter(Q(pk__in=ids) | Q(source_id__in=route_ids))


def select_ad(placement, route_ids):
    now = timezone.now()
    queryset = AdCreative.objects.filter(placement=placement, active=True, is_test=False, starts_at__lte=now, ends_at__gt=now)
    if placement == AdPlacement.PARTNER:
        if not route_ids:
            return None
        queryset = queryset.filter(place__isnull=False, courses__in=resolve_courses(route_ids)).distinct()
    return queryset.order_by("-priority", "-id").first()


def make_delivery(ad):
    now = timezone.now()
    if ad is None:
        return {"ad": None, "token": "", "exposure_id": None, "server_now": now, "valid_until": None}
    exposure_id = uuid.uuid4()
    valid_until = min(ad.ends_at, now + timedelta(seconds=TOKEN_SECONDS))
    payload = {"ad_id": ad.pk, "placement": ad.placement, "exposure_id": str(exposure_id), "expires": valid_until.timestamp()}
    return {"ad": ad, "token": signing.dumps(payload, salt=TOKEN_SALT), "exposure_id": exposure_id, "server_now": now, "valid_until": valid_until}


def record_event(event_id, token, kind):
    try:
        payload = signing.loads(token, salt=TOKEN_SALT, max_age=TOKEN_SECONDS)
        exposure_id = uuid.UUID(payload["exposure_id"])
        expires = float(payload["expires"])
        ad_id = int(payload["ad_id"])
        placement = payload["placement"]
        if not math.isfinite(expires):
            raise ValueError("invalid expiry")
    except (signing.BadSignature, KeyError, ValueError, TypeError, AttributeError, OverflowError) as exc:
        raise ValidationError("광고 토큰이 올바르지 않습니다.") from exc

    now = timezone.now()
    if now.timestamp() >= expires:
        raise ValidationError("광고 토큰이 만료되었습니다.")

    with transaction.atomic():
        # 관리자에 의한 비활성화와 집계를 순서대로 처리한다.
        ad = AdCreative.objects.select_for_update().filter(pk=ad_id, placement=placement, active=True, is_test=False, starts_at__lte=now, ends_at__gt=now).first()
        if ad is None:
            raise ValidationError("집계 가능한 광고가 아닙니다.")
        previous = AdEvent.objects.filter(event_id=event_id).first()
        if previous and (previous.exposure_id != exposure_id or previous.kind != kind or previous.ad_id != ad.pk or previous.placement != placement):
            raise ValidationError("이미 다른 이벤트에 사용된 event_id입니다.")
        try:
            with transaction.atomic():
                event, created = AdEvent.objects.get_or_create(exposure_id=exposure_id, kind=kind, defaults={"event_id": event_id, "ad": ad, "placement": placement})
        except IntegrityError as exc:
            raise ValidationError("이벤트 식별자가 중복되었습니다.") from exc
        if event.ad_id != ad.pk or event.placement != placement:
            raise ValidationError("노출 정보가 일치하지 않습니다.")
        return {"accepted": True, "duplicate": not created}
