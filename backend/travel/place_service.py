import json
import math
from collections import deque
from datetime import timedelta
from threading import BoundedSemaphore, Lock
from time import monotonic
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from .models import Place
from .place_serializers import PlaceSearchSerializer, PlaceSerializer, PlaceWriteSerializer
from .stadium_scope import filter_provider_places
from .collected_places import CatalogueUnavailable, distance


KAKAO_ENDPOINTS = {
    "keyword": "https://dapi.kakao.com/v2/local/search/keyword.json",
    "category": "https://dapi.kakao.com/v2/local/search/category.json",
}
SEARCH_FIELDS = {"method", "keyword", "category", "lat", "lng", "radius", "page", "size", "sort"}
CREATE_FIELDS = set(PlaceWriteSerializer.Meta.fields)
UPDATE_FIELDS = CREATE_FIELDS - {"kakao_place_id"}
FILTER_FIELDS = {"name", "category", "kakao_place_id"}
_SEARCH_SLOTS = BoundedSemaphore(8)
_SEARCH_RATE_LOCK = Lock()
_SEARCH_STARTED = deque()


class PlaceError(Exception):
    message = "요청을 처리하지 못했습니다."


class PlaceValidationError(PlaceError):
    message = "입력값을 확인해 주세요."


class PlaceAuthorizationError(PlaceError):
    message = "관리자 권한이 필요합니다."


class PlaceNotFoundError(PlaceError):
    message = "장소를 찾을 수 없습니다."


class PlaceConflictError(PlaceError):
    message = "같은 카카오 장소 ID가 이미 존재합니다."


class PlaceUpstreamError(PlaceError):
    message = "일부 장소를 불러오지 못했어요."


class PlaceRateLimitError(PlaceError):
    message = "장소 검색 요청이 많아요. 잠시 후 다시 시도해 주세요."


class PlaceConfigurationError(PlaceError):
    message = "지도 데이터 연결 설정이 필요해요. 서버의 카카오 REST API 키를 확인해 주세요."


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _require_staff(actor):
    if not getattr(actor, "is_authenticated", False) or not getattr(actor, "is_active", False) or not getattr(actor, "is_staff", False):
        raise PlaceAuthorizationError


def _strict_payload(data, allowed, *, required=True):
    if not isinstance(data, dict) or (required and not data) or set(data) - allowed:
        raise PlaceValidationError


def _validated(serializer_class, data, allowed):
    _strict_payload(data, allowed)
    serializer = serializer_class(data=data)
    if not serializer.is_valid():
        raise PlaceValidationError
    return serializer


def _place_data(place):
    return PlaceSerializer(place).data


def create_place(data, *, actor):
    _require_staff(actor)
    serializer = _validated(PlaceWriteSerializer, data, CREATE_FIELDS)
    try:
        with transaction.atomic():
            place = serializer.save()
    except IntegrityError as error:
        raise PlaceConflictError from error
    return _place_data(place)


def list_places(filters=None, *, page=1, page_size=20):
    if filters is None:
        filters = {}
    _strict_payload(filters, FILTER_FIELDS, required=False)
    if isinstance(page, bool) or isinstance(page_size, bool) or not isinstance(page, int) or not isinstance(page_size, int) or not 1 <= page <= 10000 or not 1 <= page_size <= 100:
        raise PlaceValidationError
    if not all(isinstance(value, str) and 0 < len(value.strip()) <= 100 for value in filters.values()):
        raise PlaceValidationError
    queryset = Place.objects.all()
    if name := filters.get("name"):
        queryset = queryset.filter(name__icontains=name.strip())
    if category := filters.get("category"):
        category = category.strip()
        queryset = queryset.filter(Q(category_group_code=category) | Q(category_name__icontains=category))
    if kakao_id := filters.get("kakao_place_id"):
        queryset = queryset.filter(kakao_place_id=kakao_id.strip())
    count = queryset.count()
    start = (page - 1) * page_size
    return {"count": count, "page": page, "page_size": page_size, "results": PlaceSerializer(queryset[start:start + page_size], many=True).data}


def get_place(place_id):
    return _place_data(_get_place(place_id))


def _get_place(place_id):
    if isinstance(place_id, bool) or not isinstance(place_id, int) or place_id < 1:
        raise PlaceValidationError
    try:
        return Place.objects.get(pk=place_id)
    except Place.DoesNotExist as error:
        raise PlaceNotFoundError from error


def update_place(place_id, changes, *, actor):
    _require_staff(actor)
    _strict_payload(changes, UPDATE_FIELDS)
    with transaction.atomic():
        if isinstance(place_id, bool) or not isinstance(place_id, int) or place_id < 1:
            raise PlaceValidationError
        try:
            place = Place.objects.select_for_update().get(pk=place_id)
        except Place.DoesNotExist as error:
            raise PlaceNotFoundError from error
        serializer = PlaceWriteSerializer(place, data=changes, partial=True)
        if not serializer.is_valid():
            raise PlaceValidationError
        try:
            place = serializer.save()
        except IntegrityError as error:
            raise PlaceConflictError from error
    return _place_data(place)


def delete_place(place_id, *, actor):
    _require_staff(actor)
    with transaction.atomic():
        if isinstance(place_id, bool) or not isinstance(place_id, int) or place_id < 1:
            raise PlaceValidationError
        try:
            place = Place.objects.select_for_update().get(pk=place_id)
        except Place.DoesNotExist as error:
            raise PlaceNotFoundError from error
        place.delete()


def _string(item, name, max_length, *, required=False):
    value = item.get(name, "")
    if not isinstance(value, str) or len(value) > max_length or (required and not value):
        raise PlaceUpstreamError
    return value


def _normalize_document(value):
    if not isinstance(value, dict):
        raise PlaceUpstreamError
    place_id = _string(value, "id", 100, required=True)
    name = _string(value, "place_name", 255, required=True)
    x = _string(value, "x", 64, required=True)
    y = _string(value, "y", 64, required=True)
    try:
        lng, lat = float(x), float(y)
    except ValueError as error:
        raise PlaceUpstreamError from error
    if not math.isfinite(lat) or not math.isfinite(lng) or not -90 <= lat <= 90 or not -180 <= lng <= 180:
        raise PlaceUpstreamError
    document = {
        "id": place_id,
        "place_name": name,
        "road_address_name": _string(value, "road_address_name", 500),
        "address_name": _string(value, "address_name", 500),
        "category_group_name": _string(value, "category_group_name", 100),
        "category_group_code": _string(value, "category_group_code", 20),
        "category_name": _string(value, "category_name", 255),
        "phone": _string(value, "phone", 50),
        "x": x,
        "y": y,
    }
    place_url = _string(value, "place_url", 500)
    try:
        if place_url and (urlsplit(place_url).scheme not in {"http", "https"} or urlsplit(place_url).hostname != "place.map.kakao.com"):
            raise PlaceUpstreamError
    except ValueError as error:
        raise PlaceUpstreamError from error
    document["place_url"] = place_url
    return document, {
        "name": name,
        "road_address": document["road_address_name"],
        "address": document["address_name"],
        "category_group_name": document["category_group_name"],
        "category_group_code": document["category_group_code"],
        "category_name": document["category_name"],
        "phone": document["phone"],
        "lng": lng,
        "lat": lat,
        "url": place_url,
    }


def _request_kakao(query, *, timeout=8):
    key = getattr(settings, "KAKAO_REST_API_KEY", "").strip()
    if not key:
        raise PlaceConfigurationError
    params = {
        "x": query["lng"], "y": query["lat"], "page": query["page"],
        "size": query["size"], "sort": query["sort"],
    }
    if query.get("radius"):
        params["radius"] = query["radius"]
    if query.get("rect"):
        params["rect"] = query["rect"]
    if query["method"] == "keyword":
        params["query"] = query["keyword"]
        if query.get("category"):
            params["category_group_code"] = query["category"]
    else:
        params["category_group_code"] = query["category"]
    request = Request(f"{KAKAO_ENDPOINTS[query['method']]}?{urlencode(params)}", headers={"Authorization": f"KakaoAK {key}"})
    try:
        with build_opener(_NoRedirect()).open(request, timeout=timeout) as response:
            content_length = response.headers.get("Content-Length")
            if content_length and (not content_length.isdecimal() or int(content_length) > 262144):
                raise PlaceUpstreamError
            raw = response.read(262145)
    except (HTTPError, URLError, TimeoutError, OSError) as error:
        raise PlaceUpstreamError from error
    if len(raw) > 262144:
        raise PlaceUpstreamError
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PlaceUpstreamError from error


def _enter_search():
    if not _SEARCH_SLOTS.acquire(blocking=False):
        raise PlaceRateLimitError
    now = monotonic()
    with _SEARCH_RATE_LOCK:
        while _SEARCH_STARTED and _SEARCH_STARTED[0] <= now - 60:
            _SEARCH_STARTED.popleft()
        if len(_SEARCH_STARTED) >= 240:
            _SEARCH_SLOTS.release()
            raise PlaceRateLimitError
        _SEARCH_STARTED.append(now)


def search_and_sync_places(query):
    serializer = _validated(PlaceSearchSerializer, query, SEARCH_FIELDS)
    query = serializer.validated_data
    # ponytail: process-local cap; use a shared limiter only when multi-worker traffic requires it.
    _enter_search()
    try:
        payload = _request_kakao(query)
        if not isinstance(payload, dict) or not isinstance(payload.get("meta"), dict) or not isinstance(payload["meta"].get("is_end"), bool) or not isinstance(payload.get("documents"), list) or len(payload["documents"]) > query["size"]:
            raise PlaceUpstreamError
        normalized = [_normalize_document(item) for item in payload["documents"]]
        if len({document["id"] for document, _ in normalized}) != len(normalized):
            raise PlaceUpstreamError
        try:
            visible = {doc["id"]: doc for doc in filter_provider_places([doc for doc, _ in normalized])}
        except CatalogueUnavailable as exc:
            raise PlaceUpstreamError from exc
        normalized = [(visible[doc["id"]], defaults) for doc, defaults in normalized if doc["id"] in visible]
        synced_at = timezone.now()
        sync_interval = timedelta(seconds=settings.EXTERNAL_DATA_SYNC_INTERVAL_SECONDS)
        with transaction.atomic():
            for document, defaults in normalized:
                place, created = Place.objects.select_for_update().get_or_create(
                    kakao_place_id=document["id"],
                    defaults={**defaults, "last_synced_at": synced_at},
                )
                if created or place.last_synced_at is not None and synced_at - place.last_synced_at <= sync_interval:
                    continue
                for field, value in defaults.items():
                    setattr(place, field, value)
                place.last_synced_at = synced_at
                place.save(update_fields=(*defaults, "last_synced_at", "updated_at"))
        return {"places": [document for document, _ in normalized], "hasNextPage": not payload["meta"]["is_end"], "syncedAt": synced_at.isoformat()}
    finally:
        _SEARCH_SLOTS.release()


def search_live_places(query):
    """Read-only nearby lookup. Provider content stays in this response, not Place."""
    serializer = _validated(PlaceSearchSerializer, query, SEARCH_FIELDS)
    return _search_live(serializer.validated_data)


def _near_cell(document, rect):
    # Live Kakao results can straddle the requested rectangle by sub-meter
    # coordinate/index rounding (observed 0.4m). This is NOT a radius expansion:
    # the course source still applies the exact circle and stadium exclusions.
    lng, lat = float(document["x"]), float(document["y"])
    nearest_lng = min(max(lng, rect[0]), rect[2])
    nearest_lat = min(max(lat, rect[1]), rect[3])
    return distance(lat, lng, nearest_lat, nearest_lng) <= 2


def search_candidate_cell(*, rect, category="", keyword="", page=1, timeout=8):
    """Internal read-only rectangle lookup with completeness metadata.

    Not exposed as a new public endpoint; existing map contracts stay unchanged.
    Raw provider results are request-local and never synchronized into Place.
    """
    if (not isinstance(rect, (tuple, list)) or len(rect) != 4
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in rect)
            or not -180 <= rect[0] < rect[2] <= 180 or not -90 <= rect[1] < rect[3] <= 90
            or category not in {"", "FD6", "CE7", "AT4", "CT1", "CS2", "AD5"}
            or not isinstance(keyword, str) or len(keyword) > 100 or not (category or keyword.strip())
            or type(page) is not int or not 1 <= page <= 3
            or type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 8):
        raise PlaceValidationError
    west, south, east, north = rect
    query = {"method": "keyword" if keyword else "category", "keyword": keyword, "category": category,
             "lat": (south + north) / 2, "lng": (west + east) / 2,
             "rect": ",".join(str(v) for v in rect), "page": page, "size": 15, "sort": "accuracy"}
    _enter_search()
    try:
        try:
            payload = _request_kakao(query, timeout=timeout)
        except PlaceUpstreamError as error:
            # Preserve existing public search error contracts; the course
            # provider separately records quota/rate failures as incomplete.
            if isinstance(error.__cause__, HTTPError) and error.__cause__.code == 429:
                raise PlaceRateLimitError from error
            raise
        meta = payload.get("meta") if isinstance(payload, dict) else None
        if (not isinstance(meta, dict) or type(meta.get("is_end")) is not bool
                or type(meta.get("total_count")) is not int or type(meta.get("pageable_count")) is not int
                or not 0 <= meta["pageable_count"] <= min(45, meta["total_count"])
                or not isinstance(payload.get("documents"), list) or len(payload["documents"]) > 15):
            raise PlaceUpstreamError
        documents = [_normalize_document(item)[0] for item in payload["documents"]]
        if len({p["id"] for p in documents}) != len(documents) or any(
            not p["id"].isascii() or not p["id"].isdecimal()
            or (category and p["category_group_code"] != category)
            or not _near_cell(p, rect) for p in documents
        ):
            raise PlaceUpstreamError
        return {"places": documents, "total_count": meta["total_count"],
                "pageable_count": meta["pageable_count"], "is_end": meta["is_end"]}
    finally:
        _SEARCH_SLOTS.release()


def _search_live(query):
    _enter_search()
    try:
        payload = _request_kakao(query)
        if not isinstance(payload, dict) or not isinstance(payload.get("meta"), dict) or not isinstance(payload["meta"].get("is_end"), bool) or not isinstance(payload.get("documents"), list) or len(payload["documents"]) > query["size"]:
            raise PlaceUpstreamError
        counts = {}
        meta = payload["meta"]
        if "total_count" in meta or "pageable_count" in meta:
            total, pageable = meta.get("total_count"), meta.get("pageable_count")
            if type(total) is not int or type(pageable) is not int or not 0 <= pageable <= min(total, 45):
                raise PlaceUpstreamError
            counts = {"totalCount": total, "pageableCount": pageable}
        documents = [_normalize_document(item)[0] for item in payload["documents"]]
        if len({item["id"] for item in documents}) != len(documents) or any(
            not item["id"].isdecimal() or not item["id"].isascii()
            or (query.get("category") and item["category_group_code"] != query["category"])
            for item in documents
        ):
            raise PlaceUpstreamError
        try:
            documents = filter_provider_places(documents)
        except CatalogueUnavailable as exc:
            raise PlaceUpstreamError from exc
        return {"places": documents, "hasNextPage": not payload["meta"]["is_end"], "syncedAt": timezone.now().isoformat(), **counts}
    finally:
        _SEARCH_SLOTS.release()


def search_live_lodging(query):
    """Lodging-only live lookup; never copy provider responses to Place rows."""
    serializer = _validated(PlaceSearchSerializer, query, SEARCH_FIELDS)
    query = serializer.validated_data
    if query["category"] != "AD5" or (
        query["method"] == "keyword" and query["keyword"] not in {"호텔", "모텔", "여관", "여인숙"}
    ) or (query["method"] == "category" and query["keyword"]):
        raise PlaceValidationError
    return _search_live(query)


class PlaceService:
    create = staticmethod(create_place)
    list = staticmethod(list_places)
    get = staticmethod(get_place)
    update = staticmethod(update_place)
    delete = staticmethod(delete_place)
    search_and_sync_places = staticmethod(search_and_sync_places)
