import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from preprocessing import read_myseatcheck_menus as menus


def response(text):
    return SimpleNamespace(output_text=text, usage=SimpleNamespace(model_dump=lambda: {"total_tokens": 123}))


def reading(store="a"):
    return {"index": 0, "storeId": store, "status": "MENU_READABLE", "note": "",
            "items": [{"name": "새우", "option": "", "priceWon": 4000, "priceText": "4000"}]}


class MenuReadTests(unittest.TestCase):
    def test_manifest_and_checkpoint_distinguish_stores_sharing_one_photo(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            url = "https://myseatcheck.com/wp-content/uploads/shared.webp"
            (root / "census.json").write_text(json.dumps({"pages": [
                {"url": f"https://myseatcheck.com/{s}", "links": [{"url": url}]} for s in ("a", "b")]}))
            (root / "stores.csv").write_text("record_id,source_url,store_facility,stadium_code,source_location\n"
                                              "a,https://myseatcheck.com/a,A,TEST,1\nb,https://myseatcheck.com/b,B,TEST,2\n")
            _, photos = menus.load_manifest(root / "census.json", root / "stores.csv")
            self.assertEqual([p["storeId"] for p in photos], ["a", "b"])
            self.assertNotEqual(menus.read_key(photos[0]), menus.read_key(photos[1]))
            checkpoint = root / "reads.jsonl"
            checkpoint.write_text("".join(json.dumps({**p, "version": menus.VERSION, "status": "READABLE"}) + "\n" for p in photos))
            self.assertEqual(len(menus.load_reads(checkpoint)), 2)

    def test_published_catalogue_has_no_ambiguous_observations_and_keeps_original_evidence(self):
        root = Path(__file__).resolve().parents[2] / "data" / "preprocessed"
        audit = json.loads((root / "myseatcheck_menu_audit.json").read_text())
        owners = {}
        for record in audit["records"]:
            for image in record["images"]:
                owners.setdefault(image["imageUrl"], set()).add(record["facilityId"])
        shared = {url for url, stores in owners.items() if len(stores) > 1}
        catalogue = json.loads((root / "myseatcheck_menus.json").read_text())
        for record in catalogue["records"]:
            for item in record["items"]:
                self.assertTrue(item["observations"])
                self.assertTrue(all(o["imageUrl"] not in shared for o in item["observations"]))
        quarantine = json.loads((root / "myseatcheck_menu_quarantine.json").read_text())
        honam_original = next(r for r in quarantine["records"] if r["facilityId"] == "SC_FOOD_CHANGWON_021")
        honam = next(r for r in catalogue["records"] if r["facilityId"] == "SC_FOOD_CHANGWON_021")
        self.assertIn("크림새우", [i["name"] for i in honam_original["items"]])
        self.assertNotIn("크림새우", [i["name"] for i in honam["items"]])
        station = next(r for r in catalogue["records"] if r["facilityId"] == "SC_FOOD_CHANGWON_019")
        self.assertIn("크림새우", [i["name"] for i in station["items"]])

    def test_shared_legacy_photo_is_quarantined_but_scoped_and_unique_reads_survive(self):
        stores = [{"record_id": s, "stadium_code": "TEST", "store_facility": s, "source_location": "",
                   "source_url": s, "imageUrls": ["shared", s]} for s in ("a", "b")]
        raw = reading()
        legacy = {"imageUrl": "shared", "firstRead": raw, "secondRead": raw}
        reads = {"shared": legacy, "a": {**legacy, "imageUrl": "a"}}
        catalogue, audit = menus.compile_catalogue(stores, reads)
        self.assertEqual([r["facilityId"] for r in catalogue["records"]], ["a"])
        self.assertEqual(catalogue["records"][0]["imageUrls"], ["a"])
        self.assertEqual(audit["records"][1]["images"][0]["status"], "QUARANTINED_SHARED_PHOTO")
        scoped = {**legacy, "storeId": "a"}
        reads[menus.read_key(scoped)] = scoped
        catalogue, _ = menus.compile_catalogue(stores, reads)
        self.assertEqual(catalogue["records"][0]["imageUrls"], ["shared", "a"])
        self.assertEqual(legacy["firstRead"], raw, "original evidence is not modified")

    def test_usage_is_saved_before_malformed_json_or_wrong_store_validation(self):
        for text in ("{", json.dumps({"photos": [reading("wrong")]}), json.dumps({"photos": []})):
            client = Mock()
            client.responses.create.return_value = response(text)
            saved = []
            with self.assertRaises(ValueError):
                menus.read_batch(client, "mock", [{"imageUrl": "photo", "storeId": "a", "stores": []}], save_usage=saved.append)
            self.assertEqual(saved, [{"total_tokens": 123}])

    def test_invalid_sibling_keeps_valid_second_stage_and_retries_only_incomplete_photo(self):
        photos = [{"imageUrl": store, "storeId": store, "stores": [{"id": store}]} for store in ("a", "b")]
        a, b = reading(), {**reading("b"), "index": 1}
        client = Mock()
        client.responses.create.side_effect = [response(json.dumps({"photos": [a, b]})),
                                               response(json.dumps({"photos": [a, {**b, "status": "BROKEN"}]}))]
        with tempfile.TemporaryDirectory() as directory, patch.object(menus, "OpenAI", return_value=client):
            work = Path(directory)
            with self.assertRaises(ValueError):
                menus.inspect_batch(photos, "mock", work)
            stages = [json.loads(path.read_text()) for path in (work / "stages").glob("*.json")]
            self.assertEqual(sum("second" in stage for stage in stages), 1)
            self.assertEqual(len((work / "usage.jsonl").read_text().splitlines()), 2)
            client.responses.create.side_effect = [response(json.dumps({"photos": [reading("b")]}))]
            rows, _ = menus.inspect_batch(photos, "mock", work)
            self.assertEqual([row["storeId"] for row in rows], ["a", "b"])
            usage = json.loads((work / "usage.jsonl").read_text().splitlines()[-1])
            self.assertEqual(usage["photos"], [menus.read_key(photos[1])])

    def test_duplicate_index_never_checkpoints_even_when_one_duplicate_is_invalid(self):
        client = Mock()
        client.responses.create.return_value = response(json.dumps({"photos": [reading(), {**reading(), "status": "BROKEN"}]}))
        saved, usage = [], []
        with self.assertRaises(ValueError):
            menus.read_batch(client, "mock", [{"imageUrl": "a", "storeId": "a", "stores": []}],
                             save_usage=usage.append, save_read=lambda i, row: saved.append(row))
        self.assertEqual(saved, [])
        self.assertEqual(len(usage), 1)

    def test_completed_reads_are_filtered_by_requested_model_before_overwriting_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reads.jsonl"
            rows = [{"imageUrl": "a", "storeId": "a", "version": menus.VERSION, "status": "READABLE", "model": model}
                    for model in ("new-model", "old-model", None)]
            path.write_text("".join(json.dumps(row) + "\n" for row in rows))
            self.assertEqual(list(menus.load_reads(path, "new-model").values()), [rows[0]])
            self.assertEqual(menus.load_reads(path, "other-model"), {})

    def test_second_failure_keeps_first_stage_and_resumes_only_second(self):
        client = Mock()
        client.responses.create.side_effect = [response(json.dumps({"photos": [reading()]})), response("{")]
        photos = [{"imageUrl": "photo", "storeId": "a", "stores": [{"id": "a"}]}]
        with tempfile.TemporaryDirectory() as directory, patch.object(menus, "OpenAI", return_value=client):
            work = Path(directory)
            with self.assertRaises(ValueError):
                menus.inspect_batch(photos, "mock", work)
            stage = json.loads(next((work / "stages").glob("*.json")).read_text())
            self.assertEqual(stage["first"], reading())
            self.assertNotIn("second", stage)
            self.assertEqual(len((work / "usage.jsonl").read_text().splitlines()), 2)
            client.responses.create.side_effect = [response(json.dumps({"photos": [reading()]}))]
            rows, _ = menus.inspect_batch(photos, "mock", work)
            self.assertEqual(rows[0]["items"][0]["name"], "새우")
            self.assertEqual(client.responses.create.call_count, 3)
            usage = [json.loads(line) for line in (work / "usage.jsonl").read_text().splitlines()]
            self.assertEqual([r["stage"] for r in usage], ["first", "second", "second"])
            menus.inspect_batch(photos, "mock", work)
            self.assertEqual(client.responses.create.call_count, 3)


if __name__ == "__main__":
    unittest.main()
