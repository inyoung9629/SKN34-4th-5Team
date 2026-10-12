"""Match a displayed place choice without guessing a name or an ordinal."""
import re


def compact(value):
    return re.sub(r"\s+", "", str(value)).casefold()


def selected_place(choice, places):
    text = compact(choice)
    matches = []
    for place in places:
        name, label = compact(place.get("name", "")), compact(place.get("label", ""))
        if not name:
            continue
        aliases = {name}
        phase = {"BEFORE": "경기전", "AFTER": "경기후", "GAME": "경기관람"}.get(place.get("phase"))
        if phase:
            aliases.add(phase + name)
        if label.isdecimal():
            aliases.update({label + "번", label + "번" + name, label + "." + name})
        if text in aliases:
            matches.append(place)
    return matches[0] if len(matches) == 1 else None
