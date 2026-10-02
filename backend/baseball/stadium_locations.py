"""Reviewed first-team venue points, independent of address geocoding."""
import json
from pathlib import Path


def reviewed_locations(root):
    path = Path(root) / "preprocessed/stadium_locations.json"
    if not path.exists():
        return {}  # Older/offline data bundles retain their original coordinates.
    data = json.loads(path.read_text(encoding="utf-8"))
    locations = data["stadiums"]
    for code, point in locations.items():
        if not (point["south"] < point["lat"] < point["north"] and point["west"] < point["lng"] < point["east"]):
            raise ValueError(f"Invalid reviewed stadium point: {code}")
    return locations


def reviewed_venue(code):
    return reviewed_locations(Path(__file__).resolve().parents[2] / "data").get(code)
