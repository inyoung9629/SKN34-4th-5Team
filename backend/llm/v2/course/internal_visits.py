"""Explicit stadium visits only. Never manufacture coordinates or assume unknown access."""


def choose_internal(stops, records):
    chosen, used = [], set()
    for stop in stops:
        kind = "facility" if stop.kind == "facility" else "food"
        candidates, unknown_scope = [], False
        if stop.cuisine or stop.food or stop.reviews:
            return [], "internal_details_unverified"
        for row in records:
            if row["id"] in used or row["kind"] != kind:
                continue
            text = " ".join(str(row.get(k) or "") for k in ("name", "floor", "zone")).casefold()
            if any(term.casefold() not in text for term in stop.required_keywords):
                continue
            if any(term.casefold() in text for term in stop.excluded_keywords):
                continue
            if stop.kind == "cafe" and not any(word in text for word in ("카페", "커피", "cafe", "coffee")):
                continue
            if stop.kind == "store" and not any(word in text for word in ("편의점", "gs25", "cu", "세븐일레븐", "이마트24")):
                continue
            if row["scope"] != "internal":
                unknown_scope |= row["scope"] == "unknown"
                continue
            score = sum(word.casefold() in text for word in stop.preferred_keywords)
            candidates.append((score, row))
        if not candidates:
            return [], "internal_scope_unverified" if unknown_scope else "internal_no_match"
        candidates.sort(key=lambda pair: (-pair[0], pair[1]["id"]))
        row = candidates[0][1]
        used.add(row["id"])
        pin = row.get("pin") or {}
        selected = {"placeId": f"stadium-facility:{row['id']}", "name": row["name"],
                    "kind": stop.kind, "lat": pin.get("lat"), "lng": pin.get("lng"),
                    "floor": row.get("floor"), "zone": row.get("zone"), "scope": "internal",
                    "source": "MYSEATCHECK", "sourceUrl": row.get("sourceUrl"),
                    "collectedAt": row.get("sourceCheckedAt"), "locationStatus": row.get("locationStatus"),
                    "stadiumAffiliation": {"stadium": row["stadium"], "scope": "internal"}}
        chosen.append({"place": selected, "request": stop, "radius_m": None})
    return chosen, None
