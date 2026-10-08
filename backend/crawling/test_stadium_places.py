import io
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

import collect_stadium_places as c
import google_lodging as g


class StadiumPlacesTests(unittest.TestCase):
    def test_capped_google_search_refines_deduplicates_and_checks_radius(self):
        calls = []
        def place(i, lat=37.5):
            return {"id": str(i), "displayName": {"text": "hotel"},
                    "location": {"latitude": lat, "longitude": 127}, "primaryType": "hotel"}
        def search(key, lat, lng, radius):
            calls.append((lat, lng, radius))
            if len(calls) == 1:
                return [place(i) for i in range(20)]
            return [place(0), place(20), place(21, 38), {"id": "bad"}]
        live, meta = g.collect_lodging("fixture", (37.5, 127), search=search)
        self.assertEqual(len(calls), 5)
        self.assertEqual(len(live), 21)
        self.assertEqual(meta["unresolved_search_cells"], 0)
        self.assertEqual(meta["outside_radius_or_invalid_location"], 2)
        self.assertEqual(meta["coverage"], "search_completed_not_exhaustive")
        refs = g.id_references(live)
        self.assertTrue(all(set(r) == {"source", "place_id"} for r in refs))
        self.assertNotIn("hotel", json.dumps(refs))

    def test_budget_exhaustion_cannot_be_reported_as_complete(self):
        rows = [{"id": str(i), "location": {"latitude": 37.5, "longitude": 127}} for i in range(20)]
        live, meta = g.collect_lodging("fixture", (37.5, 127), max_queries=1,
                                      search=lambda *args: rows)
        self.assertEqual(len(live), 20)
        self.assertEqual(meta["unresolved_search_cells"], 4)
        self.assertEqual(meta["coverage"], "partial_capped")

    def test_google_errors_do_not_leak_secrets_or_look_empty(self):
        err = HTTPError("https://example.invalid?key=SECRET", 403, "SECRET", {}, io.BytesIO(b"SECRET"))
        with patch.object(g, "urlopen", side_effect=err):
            with self.assertRaises(RuntimeError) as raised:
                g.nearby("SECRET", 37.5, 127, 2500)
        self.assertIn("403", str(raised.exception))
        self.assertNotIn("SECRET", str(raised.exception))

    def test_google_search_or_transport_failure_propagates(self):
        with self.assertRaisesRegex(RuntimeError, "fixture failure"):
            g.collect_lodging("fixture", (37.5, 127), search=lambda *a: (_ for _ in ()).throw(RuntimeError("fixture failure")))

    def test_retired_sbiz_collector_never_calls_the_network(self):
        self.assertNotIn("SBIZ", c.SOURCES)
        with patch.object(c.pilot, "all_pages", side_effect=AssertionError("network")):
            with self.assertRaisesRegex(RuntimeError, "SBIZ collection was removed"):
                c.pilot.collect_sbiz("fixture", (37.5, 127))

    def test_tour_never_requests_or_accepts_lodging(self):
        calls = []
        def pages(url, params, **kwargs):
            calls.append(params["contentTypeId"])
            return [{"contenttypeid": "32", "title": "공원 호텔", "contentid": "hotel",
                     "mapy": 37.5, "mapx": 127}]
        with patch.object(c.pilot, "all_pages", side_effect=pages):
            places, meta = c.tour_walks("fixture", (37.5, 127))
        self.assertEqual(calls, [12, 28])
        self.assertEqual(places, [])


if __name__ == "__main__":
    unittest.main()
