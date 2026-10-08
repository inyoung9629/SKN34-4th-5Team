import json

from .data_loader import nullable, number


PARKING_FIELDS = (
    "parking_map_url", "parking_map_source_url", "parking_map_credit", "parking_map_title",
    "parking_map_summary", "parking_map_kind", "parking_map_visual_notes", "parking_map_captured_at",
    "parking_map_width", "parking_map_height",
)


def parking_values(row, model):
    fields = {field.name for field in model._meta.fields}
    values = {}
    for name in PARKING_FIELDS:
        if name not in fields or name not in row:
            continue
        value = nullable(row[name])
        if name == "parking_map_visual_notes":
            value = json.loads(value) if value else None
            if value is not None and (not isinstance(value, list) or any(not isinstance(note, str) for note in value)):
                raise ValueError("parking_map_visual_notes must be a list of strings")
        elif name in ("parking_map_width", "parking_map_height"):
            value = number(value)
        values[name] = value
    return values


def parking_map(stadium):
    if stadium is None or not stadium.parking_map_url:
        return None
    names = {"url": "imageUrl", "source_url": "sourceUrl", "credit": "credit", "title": "title",
             "summary": "summary", "kind": "kind", "visual_notes": "visualNotes", "captured_at": "capturedAt",
             "width": "width", "height": "height"}
    return {key: value.isoformat() if hasattr(value, "isoformat") else value
            for name, key in names.items() if (value := getattr(stadium, f"parking_map_{name}")) is not None}


def seating_map(stadium, season=None):
    if stadium is None:
        return None
    # Only an actual supported context with a local diagram can be selected; never substitute a price sheet/VR.
    contexts = list(stadium.home_contexts.all())
    for context in sorted((c for c in contexts if season is None or c.season == season),
                          key=lambda c: (-c.season, c.team.team_code, c.pk)):
        for seat in sorted(context.seat_maps.all(), key=lambda s: s.pk):
            for asset in sorted(seat.assets.all(), key=lambda a: (a.asset_no, a.pk)):
                if asset.asset_role == "LOCAL_DIAGRAM" and asset.asset_url.startswith("/images/stadiums/seating-maps/"):
                    return {"imageUrl": asset.asset_url, "sourceUrl": asset.source_url or seat.page_url,
                            "title": seat.map_title, "home_context_id": context.pk, "season": context.season,
                            "team_code": context.team.team_code}
    return None


GUIDE_PREFETCH = ("home_contexts__team", "home_contexts__seat_maps__assets")
