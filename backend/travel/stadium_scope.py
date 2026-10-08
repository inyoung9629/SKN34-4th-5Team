"""Server equivalent of the reviewed map frame; not a surveyed ticket boundary."""
import json
import math
from functools import lru_cache
from pathlib import Path

from .collected_places import CatalogueUnavailable
from .stadium_facilities import _root


def convex_frame(points):
    points = sorted(set(map(tuple, points)), key=lambda p: (p[1], p[0]))
    def cross(a, b, c):
        return (b[1] - a[1]) * (c[0] - a[0]) - (b[0] - a[0]) * (c[1] - a[1])
    def half(values):
        result = []
        for p in values:
            while len(result) >= 2 and cross(result[-2], result[-1], p) <= 0:
                result.pop()
            result.append(p)
        return result[:-1]
    return half(points) + half(reversed(points))


def boundary_frame(location, rings, padding):
    vertices = [p for ring in rings for p in ring]
    hull = convex_frame(vertices)
    south = min(location["south"], *(p[0] for p in vertices))
    north = max(location["north"], *(p[0] for p in vertices))
    west = min(location["west"], *(p[1] for p in vertices))
    east = max(location["east"], *(p[1] for p in vertices))
    area = abs(sum((p[1] - west) * (q[0] - south) - (q[1] - west) * (p[0] - south)
                   for p, q in zip(hull, hull[1:] + hull[:1]))) / 2
    dy = padding / 111320
    dx = dy / math.cos(math.radians(location["lat"]))
    neighbor = any(south - dy <= p["lat"] <= north + dy and west - dx <= p["lng"] <= east + dx
                   for p in location["excluded"])
    if area / ((north - south) * (east - west)) >= .85 and not neighbor:
        return [(south - dy, west - dx), (north + dy, west - dx),
                (north + dy, east + dx), (south - dy, east + dx)]
    return convex_frame((lat + y, lng + x) for lat, lng in hull for y in (-dy, dy) for x in (-dx, dx))


def contains(path, lat, lng):
    inside = False
    for (y1, x1), (y2, x2) in zip(path, path[1:] + path[:1]):
        cross = (lng - x1) * (y2 - y1) - (lat - y1) * (x2 - x1)
        if abs(cross) < 1e-14 and min(x1, x2) <= lng <= max(x1, x2) and min(y1, y2) <= lat <= max(y1, y2):
            return True
        if (y1 > lat) != (y2 > lat) and lng < (x2 - x1) * (lat - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def reviewed_frames(code):
    try:
        locations = json.loads((_root() / "stadium_locations.json").read_text(encoding="utf-8"))
        boundaries = json.loads((_root() / "stadium_boundaries.json").read_text(encoding="utf-8"))
        if code not in locations["stadiums"]:
            raise KeyError(code)
        # Test against every reviewed stadium, including another park in a large search radius.
        return [boundary_frame(location, boundaries["stadiums"][key]["rings"], locations["boundaryPaddingM"])
                for key, location in locations["stadiums"].items()]
    except (OSError, ValueError, KeyError, TypeError, ZeroDivisionError) as exc:
        raise CatalogueUnavailable from exc


def outside_stadium(place, frames):
    return not any(contains(path, place["lat"], place["lng"]) for path in frames)


@lru_cache(maxsize=2)
def _load_zones(folder, versions):
    root = Path(folder)
    locations = json.loads((root / "stadium_locations.json").read_text(encoding="utf-8"))
    boundaries = json.loads((root / "stadium_boundaries.json").read_text(encoding="utf-8"))
    complexes = json.loads((root / "stadium_complexes.json").read_text(encoding="utf-8"))
    if complexes["schemaVersion"] != 1 or set(complexes["stadiums"]) != set(locations["stadiums"]):
        raise ValueError("Incomplete stadium complex policy")
    zones = {}
    for code, location in locations["stadiums"].items():
        review = complexes["stadiums"][code]
        if review["applyToSearch"] is not True:
            raise ValueError("Inactive stadium complex policy")
        outers = [review["outer"], *review.get("additionalOuters", [])]
        for ring in outers:
            if len(ring) < 4 or ring[0] != ring[-1]:
                raise ValueError("Unclosed complex ring")
            for lat, lng in ring:
                if (type(lat) not in (int, float) or type(lng) not in (int, float)
                        or not math.isfinite(lat) or not math.isfinite(lng)
                        or abs(lat - location["lat"]) > .02 or abs(lng - location["lng"]) > .025):
                    raise ValueError("Invalid complex coordinate")
        zones[code] = {
            "main": boundary_frame(location, boundaries["stadiums"][code]["rings"], locations["boundaryPaddingM"]),
            "outers": outers,
        }
    return zones


def reviewed_zones():
    """Shared map geometry, refreshed when source files change; fail closed on errors."""
    root = _root()
    try:
        files = ("stadium_locations.json", "stadium_boundaries.json", "stadium_complexes.json")
        versions = tuple((p.stat().st_mtime_ns, p.stat().st_size) for p in (root / f for f in files))
        return _load_zones(str(root), versions)
    except (OSError, ValueError, KeyError, TypeError, ZeroDivisionError) as exc:
        raise CatalogueUnavailable from exc


def classify_stadium_point(place, zones=None):
    """Green (including its edge) wins over red; red minus green is excluded.

    This is application classification, NOT a claim about ticket access or an
    official property boundary. Check all venues when the search radius grows.
    """
    lat, lng = place.get("lat"), place.get("lng")
    if (type(lat) not in (int, float) or type(lng) not in (int, float)
            or not math.isfinite(lat) or not math.isfinite(lng)):
        return {"scope": "unknown", "stadium": None}
    zones = reviewed_zones() if zones is None else zones
    for code, zone in zones.items():
        if contains(zone["main"], lat, lng):
            return {"scope": "internal", "stadium": code}
    for code, zone in zones.items():
        if any(contains(ring, lat, lng) for ring in zone["outers"]):
            return {"scope": "excluded_complex", "stadium": code}
    return {"scope": "external", "stadium": None}


def filter_provider_places(documents):
    """Filter after pagination accounting; never alter provider completeness counts."""
    zones = reviewed_zones()
    result = []
    for doc in documents:
        area = classify_stadium_point({"lat": float(doc["y"]), "lng": float(doc["x"])}, zones)
        if area["scope"] != "external":
            continue
        result.append({**doc, "stadiumArea": area})
    return result


def scoped_document(document, zones):
    """Recheck old RAG scope at read time without rewriting stored observations."""
    if document.get("document_type") == "facility":
        from .stadium_facilities import facility_catalogue, facility_document
        # Rehydrate source facts, rather than trusting old payloads or source labels.
        code = document.get("stadium")
        if code not in zones:
            return None
        row = next((p for p in facility_catalogue(code)["records"]
                    if p["id"] == document.get("place_id") and p["name"] == document.get("name")
                    and p["sourceUrl"] == document.get("source_url")), None)
        return facility_document(row) if row else None
    area = classify_stadium_point(document, zones)
    if (area["scope"] in ("internal", "excluded_complex") or document.get("scope") == "internal"
            or (document.get("stadiumArea") or {}).get("scope") in ("internal", "excluded_complex")):
        return None
    return document
