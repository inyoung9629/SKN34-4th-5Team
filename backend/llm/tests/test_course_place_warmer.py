import time
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch, Mock

from django.core.cache.backends.filebased import FileBasedCache
from django.test import SimpleTestCase

from travel import course_place_warmer as warmer
from llm.service import chat_runs
from llm.v1.rag.course import place_quality as quality, quality_research, venue_policy, evidence_memory


PLACE = {"placeId": "9001", "name": "시험중식당", "lat": 35.166, "lng": 126.902,
         "detail": "음식점 > 중식", "address": "광주 북구 중흥로 1", "kind": "FOOD", "stadium": "GWANGJU"}


def proof(**values):
    return {**PLACE, "status": "approved", "checkedAt": date.today().isoformat(), "rating": 4.5, "ratingCount": 10,
            "reviewUrl": "https://www.diningcode.com/profile.php?rid=fixture",
            "interior": {"status": "acceptable", "sourceUrl": "https://www.diningcode.com/profile.php?rid=fixture",
                         "note": "정돈된 좌석"}, **values}


class PlaceWarmerTests(SimpleTestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        settings = self.settings(COURSE_PLACE_WARM_ENABLED=True, COURSE_PLACE_WARM_DIR=self.root / "warm",
                                 PLACE_EVIDENCE_STORAGE_POLICIES=[])
        settings.enable()
        self.addCleanup(settings.disable)
        self.cache = FileBasedCache(str(self.root / "quality"), {"TIMEOUT": 1000})
        for name, value in (("verdict_cache", self.cache), ("catalogue", {})):
            patcher = patch.object(quality, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.scope = quality.request_scope("GWANGJU")
        self.scope.__enter__()
        self.addCleanup(self.scope.__exit__, None, None, None)

    def test_queue_only_keeps_branch_metadata_and_supported_menu_terms(self):
        warmer.remember([PLACE], "GWANGJU", "자장면")
        warmer.remember([PLACE], "GWANGJU", "나의 집에서 출발해서 조용한 카페")
        warmer.remember([PLACE], "GWANGJU", "돈카츠")
        rows = list(warmer.pending(["GWANGJU"]))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1]["menus"], ["짜장면", "돈까스"])
        self.assertNotIn("나의 집", rows[0][0].read_text(encoding="utf-8"))
        self.assertEqual(set(rows[0][1]), {"place", "menus", "seen"})

    def test_foreground_defers_worker_and_cleans_marker_after_error(self):
        with patch.object(warmer, "seeds") as seeds:
            with self.assertRaises(ValueError), warmer.foreground():
                self.assertEqual(warmer.warm(["GWANGJU"])["status"], "deferred")
                raise ValueError("foreground failed")
            seeds.assert_not_called()
        self.assertFalse(warmer.foreground_active())
        self.assertFalse((warmer.root() / "worker.json").exists())

    def test_another_worker_lock_prevents_duplicate_provider_work(self):
        warmer.write(warmer.root() / "worker.json", {"owner": "other", "started": time.time()})
        with patch.object(warmer, "seeds") as seeds:
            self.assertEqual(warmer.warm(["GWANGJU"])["status"], "deferred")
        seeds.assert_not_called()
        self.assertEqual(warmer.read(warmer.root() / "worker.json")["owner"], "other")

    def test_fresh_quality_skips_research_and_missing_menu_policy_never_stores(self):
        warmer.remember([PLACE], "GWANGJU", "자장면")
        self.cache.set(quality.cache_key(PLACE), {"row": proof()})
        with patch.object(warmer, "seeds"), patch.object(quality, "verify_candidates") as verify, \
                patch.object(evidence_memory, "search") as search, patch.object(evidence_memory, "store") as store:
            result = warmer.warm(["GWANGJU"])
        self.assertEqual(result["fresh"], 1)
        self.assertFalse(result["menu_storage_enabled"])
        verify.assert_not_called()
        search.assert_not_called()
        store.assert_not_called()

    def test_stale_quality_refreshes_but_queue_is_not_an_approval(self):
        warmer.remember([PLACE], "GWANGJU")
        self.cache.set(quality.cache_key(PLACE), {"row": proof(checkedAt=(date.today() - timedelta(days=5)).isoformat())})
        with patch.object(warmer, "seeds"), patch.object(quality, "verify_candidates", return_value=[]) as verify:
            result = warmer.warm(["GWANGJU"], limit=1)
        self.assertEqual(len(result["items"]), 1)
        self.assertTrue(verify.call_args.kwargs["refresh"])
        self.assertTrue(list(warmer.pending(["GWANGJU"]))[0][1]["attempted"])

    def test_interrupted_refresh_does_not_create_a_retry_cooldown(self):
        warmer.remember([PLACE], "GWANGJU")
        def interrupt(*args, **kwargs):
            warmer.write(warmer.root() / "active" / "test.json", {"started": time.time()})
            chat_runs.check_cancelled()
        with patch.object(warmer, "seeds"), patch.object(quality, "verify_candidates", side_effect=interrupt):
            result = warmer.warm(["GWANGJU"])
        self.assertEqual(result["status"], "deferred")
        self.assertNotIn("attempted", list(warmer.pending(["GWANGJU"]))[0][1])
        self.assertFalse(warmer.BACKGROUND.get())

    def test_rejected_verdict_is_refreshed_after_the_same_interval(self):
        warmer.remember([PLACE], "GWANGJU")
        row = proof(status="rejected", checkedAt=(date.today() - timedelta(days=5)).isoformat(),
                    interior={**proof()["interior"], "status": "poor"})
        self.cache.set(quality.cache_key(PLACE), {"row": row})
        with patch.object(warmer, "seeds"), patch.object(quality, "verify_candidates", return_value=[]) as verify:
            result = warmer.warm(["GWANGJU"])
        self.assertTrue(verify.call_args.kwargs["refresh"])
        self.assertFalse(result["items"][0]["quality"])

    def test_failed_refresh_preserves_proof_without_extending_cache_expiry(self):
        row = proof(checkedAt=(date.today() - timedelta(days=5)).isoformat())
        self.cache.set(quality.cache_key(PLACE), {"row": row})
        with patch.object(quality_research, "discover", return_value=[]), \
                patch.object(self.cache, "set", wraps=self.cache.set) as save:
            quality.verify_candidates([PLACE], "FOOD", refresh=True)
        save.assert_not_called()
        self.assertEqual(quality.resolve(PLACE), row)

    def test_newer_proven_rejection_overrides_old_static_approval(self):
        old = proof(checkedAt=(date.today() - timedelta(days=5)).isoformat())
        new = proof(status="rejected", interior={**proof()["interior"], "status": "poor"})
        self.cache.set(quality.cache_key(PLACE), {"row": new})
        with patch.object(quality, "catalogue", return_value={PLACE["placeId"]: old}):
            self.assertIsNone(quality.resolve(PLACE))
        self.cache.set(quality.cache_key(PLACE), {"row": proof(name="다른 지점")})
        self.assertIsNone(quality.resolve(PLACE))

    def test_queue_never_bypasses_internal_source_boundary(self):
        inside = {**PLACE, "lat": 35.16811385, "lng": 126.88943548}
        warmer.remember([inside], "GWANGJU")
        with patch.object(warmer, "seeds"), patch.object(quality, "verify_candidates") as verify:
            result = warmer.warm(["GWANGJU"])
        verify.assert_not_called()
        self.assertEqual(result["items"], [])

    def test_stadium_and_category_queues_are_interleaved_and_bounded(self):
        for i in range(8):
            warmer.remember([{**PLACE, "placeId": str(9000 + i), "detail": "카페" if i % 2 else PLACE["detail"]}],
                            "JAMSIL" if i > 3 else "GWANGJU")
        with patch.object(warmer, "MAX_QUEUE", 6):
            rows = list(warmer.pending(["GWANGJU", "JAMSIL"]))
        self.assertEqual(len(rows), 6)
        self.assertEqual(len({(r["place"]["stadium"], r["place"]["kind"]) for _, r in rows[:4]}), 4)
