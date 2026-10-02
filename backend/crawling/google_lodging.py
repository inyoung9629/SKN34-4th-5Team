"""Google Places lodging lookup. Full responses stay in memory; persist IDs only."""
import json
import math
import re
import time
from collections import Counter
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from collect_stadium_pilot import distance

NEARBY_URL = "https://places.googleapis.com/v1/places:searchNearby"
LODGING_TYPES = (
    "bed_and_breakfast", "budget_japanese_inn", "campground", "camping_cabin",
    "cottage", "extended_stay_hotel", "farmstay", "guest_house", "hostel",
    "hotel", "inn", "japanese_inn", "lodging", "mobile_home_park", "motel",
    "private_guest_room", "resort_hotel", "rv_park",
)
FIELDS = (
    "id", "displayName", "formattedAddress", "location", "primaryType",
    "primaryTypeDisplayName", "types", "businessStatus", "googleMapsUri", "attributions",
)


def request_google(key, url, fields, body=None):
    """Bounded retries; never include a credential, URL, or response body in errors."""
    headers = {"X-Goog-Api-Key": key, "X-Goog-FieldMask": fields,
               "Content-Type": "application/json"}
    request = Request(url, data=json.dumps(body).encode() if body is not None else None,
                      headers=headers)
    for attempt in range(3):
        try:
            with urlopen(request, timeout=30) as response:
                raw = response.read(5_000_001)
            if len(raw) > 5_000_000:
                raise RuntimeError("Google response exceeds size limit")
            result = json.loads(raw)
            if not isinstance(result, dict) or "error" in result:
                raise RuntimeError("Google returned an invalid or error response")
            return result
        except HTTPError as error:
            if error.code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise RuntimeError(f"Google HTTP {error.code}; response and key withheld") from None
        except (OSError, ValueError):
            if attempt == 2:
                raise RuntimeError("Google network or JSON failure; key withheld") from None
        time.sleep(2 ** attempt)


def nearby(key, lat, lng, radius):
    payload = request_google(key, NEARBY_URL, ",".join("places." + f for f in FIELDS), {
        "includedTypes": list(LODGING_TYPES), "maxResultCount": 20,
        "languageCode": "ko", "regionCode": "KR", "rankPreference": "DISTANCE",
        "locationRestriction": {"circle": {
            "center": {"latitude": lat, "longitude": lng}, "radius": radius}},
    })
    places = payload.get("places", [])
    if not isinstance(places, list) or len(places) > 20:
        raise RuntimeError("Unexpected Google Nearby response format")
    return places


def normalize(place, center):
    """Live representation, never a public-data/RAG record or persisted snapshot."""
    ident = place.get("id")
    location = place.get("location") or {}
    try:
        lat, lng = float(location["latitude"]), float(location["longitude"])
    except (KeyError, TypeError, ValueError):
        return None
    if not ident or not all(map(math.isfinite, (lat, lng))) or abs(lat) > 90 or abs(lng) > 180:
        return None
    meters = distance(*center, lat, lng)
    if meters > 2500:
        return None
    return {"source": "GOOGLE_PLACES", "source_id": ident, "kind": "lodging",
            "name": (place.get("displayName") or {}).get("text"),
            "address": place.get("formattedAddress"), "lat": lat, "lng": lng,
            "distance_m": round(meters, 1), "primary_type": place.get("primaryType"),
            "primary_type_label": (place.get("primaryTypeDisplayName") or {}).get("text"),
            "types": place.get("types", []), "business_status": place.get("businessStatus"),
            "google_maps_uri": place.get("googleMapsUri"),
            "attributions": place.get("attributions", []), "attribution": "Google Maps",
            "storage_policy": "live_only"}


def collect_lodging(key, center, max_queries=512, max_depth=6, search=None, progress=None):
    """Refine capped searches, deduplicate IDs, and recheck the original 2.5 km disk.

    A response below the cap still does not prove coverage of every real business.
    Child circles cover intersecting squares with padding for spherical projection.
    """
    search = search or nearby
    found, queries, capped, unresolved, malformed = {}, 0, 0, 0, 0
    depth_capped, unqueried = 0, 0
    # (east offset m, north offset m, square half-width m, depth), root is the disk.
    pending = [(0.0, 0.0, 2500.0, 0)]
    while pending:
        if queries >= max_queries:
            unqueried = len(pending)
            unresolved += unqueried
            break
        east, north, half, depth = pending.pop()
        lat = center[0] + math.degrees(north / 6371000)
        lng = center[1] + math.degrees(east / (6371000 * math.cos(math.radians(center[0]))))
        radius = 2500 if depth == 0 else math.sqrt(2) * half * 1.01 + 2
        rows = search(key, lat, lng, radius)
        queries += 1
        for place in rows:
            if place.get("id"):
                found[place["id"]] = place
            else:
                malformed += 1
        if len(rows) >= 20:
            capped += 1
            if depth >= max_depth:
                unresolved += 1
                depth_capped += 1
            else:
                child_half = half / 2
                for dx in (-child_half, child_half):
                    for dy in (-child_half, child_half):
                        x, y = east + dx, north + dy
                        if math.hypot(max(abs(x)-child_half, 0), max(abs(y)-child_half, 0)) <= 2505:
                            pending.append((x, y, child_half, depth+1))
        if progress and queries % 20 == 0:
            progress(queries, len(found))
    live = [p for raw in found.values() if (p := normalize(raw, center)) is not None]
    live.sort(key=lambda p: (p["distance_m"], p["source_id"]))
    meta = {"query_count": queries, "capped_queries": capped,
            "unresolved_search_cells": unresolved, "max_queries": max_queries,
            "unqueried_cells_at_request_limit": unqueried, "capped_cells_at_depth_limit": depth_capped,
            "max_depth": max_depth, "unique_returned_ids": len(found),
            "within_radius_ids": len(live), "missing_id_rows": malformed,
            "outside_radius_or_invalid_location": len(found)-len(live),
            "coverage": "partial_capped" if unresolved else "search_completed_not_exhaustive",
            "business_status_counts": dict(Counter(p["business_status"] or "UNKNOWN" for p in live)),
            "primary_type_counts": dict(Counter(p["primary_type"] or "unknown" for p in live)),
            "broad_lodging_type": sum(p["primary_type"] == "lodging" for p in live),
            "other_or_missing_primary_type": sum(p["primary_type"] not in LODGING_TYPES for p in live),
            "field_present_counts": {f: sum(p.get(f) is not None and p.get(f) != "" for p in live)
                                     for f in ("name", "address", "lat", "lng", "primary_type", "business_status")},
            "storage_policy": "place_ids_only; details retrieved live",
            "note": "Google search results, not a census or confirmation of current operation"}
    return live, meta


def id_references(live):
    """Explicit allowlist prevents accidental persistence of Google content."""
    return [{"source": "GOOGLE_PLACES", "place_id": p["source_id"]}
            for p in sorted(live, key=lambda p: p["source_id"])]


def fetch_detail(key, place_id):
    """Call for a user request; return data with attribution, do not cache/embed it."""
    if not re.fullmatch(r"[A-Za-z0-9_-]+", place_id):
        raise ValueError("Invalid place ID")
    payload = request_google(key, "https://places.googleapis.com/v1/places/" + place_id
                             + "?languageCode=ko&regionCode=KR", ",".join(FIELDS))
    return {"attribution": "Google Maps", "storage_policy": "live_only", "place": payload}
