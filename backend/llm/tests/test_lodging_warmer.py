import os
import time
from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase

from llm.tests.test_lodging_chrome import page
from llm.tests.test_lodging_conditions import CANDIDATE, PLACE
from travel import chrome_lodging as chrome, lodging_warmer as warmer


class WarmerTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        for p in (patch.object(chrome, "_page_cache", return_value=cache), patch.dict(os.environ, {"LODGING_CHROME_TRANSPORT": "local"})):
            p.start()
            self.addCleanup(p.stop)

    def test_refresh_preserves_old_source_on_failure_and_only_new_read_extends_expiry(self):
        now = time.time()
        key = chrome._cache_key(CANDIDATE)
        cache.set(key, {"expires_at": now + 60, "page": page()}, 60)
        with patch.object(chrome, "_local", return_value={"status": "blocked"}) as collect:
            failed = chrome.collect(CANDIDATE, deadline=time.monotonic()+45, refresh_before=1800, background=True)
        self.assertEqual(failed["status"], "blocked")
        self.assertEqual(cache.get(key)["expires_at"], now + 60)
        self.assertTrue(chrome.cached(CANDIDATE)["cache_hit"])
        with patch.object(chrome, "_local", return_value=page()):
            refreshed = chrome.collect(CANDIDATE, deadline=time.monotonic()+45, refresh_before=1800, background=True)
        self.assertFalse(refreshed["cache_hit"])
        self.assertGreater(cache.get(key)["expires_at"], now + 21500)

    def test_fresh_cache_skips_browser_and_warming_never_increases_popularity(self):
        with patch.object(chrome, "_local", return_value=page()) as collect:
            chrome.collect(CANDIDATE, deadline=time.monotonic()+45)
            key = chrome._cache_key(CANDIDATE)
            original = cache.get("lodging:warm:recent")[key]
            warmed = chrome.collect(CANDIDATE, deadline=time.monotonic()+45, refresh_before=1800, background=True)
        collect.assert_called_once()
        self.assertTrue(warmed["cache_hit"])
        self.assertEqual(cache.get("lodging:warm:recent")[key], original)

    def test_prepared_page_reaches_normal_request_without_browser_and_expired_page_does_not(self):
        with patch.object(chrome, "_local", return_value=page()) as collect:
            chrome.collect(CANDIDATE, deadline=time.monotonic()+45, background=True)
            self.assertTrue(chrome.collect(CANDIDATE, deadline=time.monotonic()+45)["cache_hit"])
        collect.assert_called_once()
        with patch.object(chrome.time, "time", return_value=time.time()+21601):
            self.assertIsNone(chrome.cached(CANDIDATE))

    def test_demand_order_deduplicates_bootstrap_and_omits_private_fields(self):
        chrome.remember({**CANDIDATE, "question": "private request", "user_id": "private user"})
        stored = cache.get("lodging:warm:recent")[chrome._cache_key(CANDIDATE)]["candidate"]
        self.assertEqual(set(stored), {"id", "name", "address"})
        with patch.object(warmer.kakao, "nearby", return_value=[PLACE, {**PLACE, "placeId": "new"}]):
            self.assertEqual([p["id"] for p in warmer.candidates(["JAMSIL"])], [CANDIDATE["id"], "new"])

    def test_limits_and_busy_stop_background_work_without_model_assessment(self):
        values = [{**CANDIDATE, "id": str(i)} for i in range(4)]
        with patch.dict(os.environ, {"LODGING_CHROME_TRANSPORT": "mcp"}), \
                patch.object(warmer, "candidates", return_value=values), patch.object(chrome, "cached", return_value=None), \
                patch.object(chrome, "collect", side_effect=[{"status": "read"}, {"status": "busy"}]) as collect:
            result = warmer.warm([], limit=3)
        self.assertEqual(result["status"], "deferred")
        self.assertEqual(collect.call_count, 2)
        self.assertTrue(all(c.kwargs["background"] for c in collect.call_args_list))
        with patch.dict(os.environ, {"LODGING_CHROME_TRANSPORT": "mcp"}), \
                patch.object(warmer, "candidates", return_value=values), patch.object(chrome, "cached", return_value=None), \
                patch.object(chrome, "collect", return_value={"status": "blocked"}) as collect:
            self.assertEqual(len(warmer.warm([], limit=1)["items"]), 1)
            collect.assert_called_once()

    def test_local_background_disabled_and_stale_failures_never_populate_cache(self):
        self.assertEqual(warmer.warm([])["status"], "disabled")
        with patch.object(chrome, "_local", return_value={"status": "partial"}):
            chrome.collect(CANDIDATE, deadline=time.monotonic()+45, background=True)
        self.assertIsNone(chrome.cached(CANDIDATE))
        self.assertIsNone(cache.get("lodging:warm:recent"))
