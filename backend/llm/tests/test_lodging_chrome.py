"""Chrome lodging integration contracts; no network or paid model calls."""
import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from django.core.cache import cache
from django.test import SimpleTestCase

from llm.service.chat_runs import Stopped
from llm.tests.test_lodging_conditions import CANDIDATE, PLACE, URL, report
from llm.v1.rag.nearby import lodging
from llm.v1.rag.course import agent
from llm.v2.agent import browser_research
from travel import chrome_lodging


def page():
    return {"status": "read", "url": URL, "source_kind": "rendered_dom_snapshot", "body_read": True,
            "name": PLACE["name"], "address": PLACE["address"],
            "title": PLACE["name"] + " 호텔/리조트 예약 | NOL",
            "body_text": PLACE["name"] + "\n4.7\n(200)\n위치/교통\n" + PLACE["address"] +
                         "\n주소복사\n주차가능\n금연객실\n2026.09.25\n침구와 욕실이 깨끗했습니다"}


class ChromeCollectionTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        pages = patch.object(chrome_lodging, "_page_cache", return_value=cache)
        pages.start()
        self.addCleanup(pages.stop)
        env = patch.dict(os.environ, {"LODGING_CHROME_TRANSPORT": "local"})
        env.start()
        self.addCleanup(env.stop)

    def test_standard_macos_installations_and_explicit_override(self):
        for install in (Path("/Applications"), Path("/home/test/Applications")):
            expected = install / "Google Chrome.app/Contents/MacOS/Google Chrome"
            with patch.dict(os.environ, {"LODGING_CHROME_PATH": ""}), \
                    patch.object(chrome_lodging.shutil, "which", return_value=None), \
                    patch.object(Path, "home", return_value=Path("/home/test")), \
                    patch.object(Path, "is_file", autospec=True, side_effect=lambda p: p == expected):
                self.assertEqual(chrome_lodging.chrome_path(), str(expected))
        with patch.dict(os.environ, {"LODGING_CHROME_PATH": "/custom/chrome"}), \
                patch.object(chrome_lodging.shutil, "which", return_value=None), \
                patch.object(Path, "is_file", return_value=True):
            self.assertEqual(chrome_lodging.chrome_path(), "/custom/chrome")
        with patch.dict(os.environ, {"LODGING_CHROME_PATH": "/missing/chrome"}), \
                patch.object(chrome_lodging.shutil, "which", return_value=None), \
                patch.object(Path, "is_file", return_value=False):
            self.assertIsNone(chrome_lodging.chrome_path())

    def collect(self):
        return chrome_lodging.collect(CANDIDATE, deadline=time.monotonic() + 45)

    def test_identity_and_observed_body_required_before_accepting_page(self):
        for change in ({"address": "서울 송파구 백제고분로7길 23-5"}, {"name": "잠실 포레스타 2 호텔"},
                       {"url": "https://evil.test/stay/domestic/123"}, {"source_kind": "model_summary"},
                       {"body_read": False}, {"body_text": "model claimed success"}):
            with self.subTest(change=change), patch.object(chrome_lodging, "_local", return_value={**page(), **change}):
                self.assertEqual(self.collect()["status"], "unverified")
        with patch.object(chrome_lodging, "_local", return_value=page()):
            self.assertEqual(self.collect()["url"], URL)

    def test_reordered_hotel_name_still_requires_exact_address_and_branch(self):
        candidate = {"name": "호텔상주", "address": "서울 송파구 올림픽로8길 19"}
        found = {"name": "잠실 상주 호텔", "address": "서울특별시 송파구 올림픽로8길 19"}
        self.assertTrue(lodging.same_property(candidate, found))
        self.assertFalse(lodging.same_property(candidate, {**found, "address": "서울 송파구 올림픽로8길 20"}))
        self.assertFalse(lodging.same_property(candidate, {**found, "name": "잠실 상주 2 호텔"}))
        self.assertFalse(lodging.same_property(candidate, {**found, "name": "잠실 다른 호텔"}))

    def test_busy_and_expired_budget_do_not_start_a_browser(self):
        with patch.object(chrome_lodging, "_local") as local:
            with chrome_lodging._admission:
                self.assertEqual(self.collect()["status"], "busy")
            self.assertEqual(chrome_lodging.collect(CANDIDATE, deadline=time.monotonic()-1)["status"], "timeout")
            local.assert_not_called()

    def test_cancellation_propagates_and_releases_admission(self):
        with patch.object(chrome_lodging, "_local", side_effect=Stopped):
            with self.assertRaises(Stopped):
                self.collect()
        self.assertFalse(chrome_lodging._admission.locked())

    def test_property_snapshot_cache_reuses_only_valid_unexpired_identity(self):
        with patch.object(chrome_lodging, "_local", return_value=page()) as local:
            first = self.collect()
            with chrome_lodging._admission:
                second = self.collect()
            self.assertFalse(first["cache_hit"])
            self.assertTrue(second["cache_hit"])
            self.assertEqual(second["body_text"], first["body_text"])
            local.assert_called_once()
            with patch.object(chrome_lodging.time, "time", return_value=time.time()+chrome_lodging.CACHE_SECONDS+1):
                self.assertFalse(self.collect()["cache_hit"])
            self.assertEqual(local.call_count, 2)

    def test_cached_page_is_revalidated_and_changed_identity_does_not_reuse_it(self):
        with patch.object(chrome_lodging, "_local", return_value=page()) as local:
            self.collect()
            changed = {**CANDIDATE, "address": "서울 송파구 백제고분로7길 23-5"}
            self.assertEqual(chrome_lodging.collect(changed, deadline=time.monotonic()+45)["status"], "unverified")
            self.assertEqual(local.call_count, 2)
        corrupted = {"expires_at": time.time()+60, "page": {**page(), "body_read": False}}
        with patch.object(chrome_lodging._page_cache(), "get", return_value=corrupted), \
                patch.object(chrome_lodging, "_local", return_value={"status": "blocked"}):
            self.assertEqual(self.collect(), {"status": "blocked"})

    def test_failures_never_return_partial_body_or_fall_back_to_search(self):
        with patch.object(browser_research, "structured_search") as search, patch.object(browser_research, "browse") as jev:
            for status in ("blocked", "busy", "timeout", "partial", "not_found"):
                with patch.object(chrome_lodging, "_local", return_value={**page(), "status": status}):
                    self.assertEqual(self.collect(), {"status": status})
            search.assert_not_called()
            jev.assert_not_called()

    def test_unreadable_cache_does_not_prevent_fresh_collection(self):
        with patch.object(chrome_lodging._page_cache(), "get", side_effect=EOFError), \
                patch.object(chrome_lodging, "_local", return_value=page()) as local:
            self.assertEqual(self.collect()["status"], "read")
        local.assert_called_once()

    def test_failure_stage_is_bounded_and_does_not_expose_source_body(self):
        for stage, expected in (("home_navigation", {"stage": "home_navigation"}), ("untrusted source text", {})):
            with self.subTest(stage=stage), patch.object(chrome_lodging, "_local", return_value={
                    **page(), "status": "timeout", "stage": stage}):
                self.assertEqual(self.collect(), {"status": "timeout", **expected})

    def test_mcp_calls_only_deterministic_lodging_tool(self):
        invoke = AsyncMock(return_value=[{"type": "text", "text": json.dumps(page())}])
        with patch.object(browser_research, "discover_tools", AsyncMock(return_value=[SimpleNamespace(ainvoke=invoke)])) as discover:
            self.assertEqual(asyncio.run(chrome_lodging._remote(PLACE["name"], 40))["body_text"], page()["body_text"])
        discover.assert_awaited_once_with({"chrome_lodging"})
        invoke.assert_awaited_once_with({"name": PLACE["name"], "timeout_seconds": 40})

    def test_local_cancellation_and_timeout_reap_owned_process_before_removing_profile(self):
        for cancelled in (False, True):
            process = Mock(pid=987654)
            process.poll.return_value = None
            def reap(proc):
                self.assertIs(proc, process)
                self.assertTrue(Path(spawn.call_args.args[0][2]).is_dir())
            with patch.object(chrome_lodging, "chrome_path", return_value="chrome"), \
                    patch.object(chrome_lodging.shutil, "which", return_value="node"), \
                    patch.object(Path, "is_file", return_value=True), \
                    patch.object(chrome_lodging.subprocess, "Popen", return_value=process) as spawn, \
                    patch.object(chrome_lodging, "_terminate", side_effect=reap) as terminate, \
                    patch.object(chrome_lodging, "check_cancelled", side_effect=Stopped if cancelled else None):
                if cancelled:
                    with self.assertRaises(Stopped):
                        chrome_lodging._local(PLACE["name"], time.monotonic()+45)
                else:
                    self.assertEqual(chrome_lodging._local(PLACE["name"], time.monotonic()-1)["status"], "timeout")
                profile = Path(spawn.call_args.args[0][2])
                terminate.assert_called_once()
            self.assertFalse(profile.exists())


class ChromeLodgingIntegrationTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        pages = patch.object(chrome_lodging, "_page_cache", return_value=cache)
        pages.start()
        self.addCleanup(pages.stop)
        token = lodging._CONTEXT.set({"deadline": time.monotonic()+90, "requirements": report()["requirements"]})
        self.addCleanup(lodging._CONTEXT.reset, token)

    def test_chrome_body_is_used_once_and_only_grounded_checks_survive(self):
        model = Mock()
        def assess(messages, **kwargs):
            lines = json.loads(messages[1].content)["pages"][0]["details"]
            parking = next(int(i) for i, line in lines.items() if line == "주차가능")
            return {"properties": [{"id": CANDIDATE["id"], "reviews": [], "checks": [
                {"requirement_id": "parking", "status": "match", "evidence_start": parking, "evidence_end": parking},
                {"requirement_id": "smoke", "status": "match", "evidence_start": 99999, "evidence_end": 99999}]}]}
        model.with_structured_output.return_value.invoke.side_effect = assess
        with patch.object(lodging, "collect_chrome", return_value=page()) as collect, \
                patch.object(agent, "llm", return_value=model), \
                patch.object(browser_research, "structured_search") as search, \
                patch("travel.public_page_reader.PublicReader") as http_reader:
            result, sources = lodging.search([CANDIDATE], "주차와 금연 숙소", [])
        collect.assert_called_once()
        search.assert_not_called()
        http_reader.assert_not_called()
        checked = lodging._normalize([CANDIDATE], result, sources)
        self.assertEqual([c["status"] for c in checked["items"][0]["checks"]], ["match", "unknown"])
        self.assertEqual(checked["items"][0]["rating"], 4.7)
        self.assertNotIn("tools", model.with_structured_output.return_value.invoke.call_args.kwargs)

    def test_invalid_or_oversized_source_ranges_cannot_verify_a_condition(self):
        model = Mock()
        for start, end in ((0, 0), (3, 2), (1, 99999), (1, 1)):
            source = page()
            source["body_text"] = "긴 설명" * 100 + "\n" + source["body_text"]
            model.with_structured_output.return_value.invoke.return_value = {"properties": [
                {"id": CANDIDATE["id"], "reviews": [], "checks": [
                    {"requirement_id": "parking", "status": "match", "evidence_start": start, "evidence_end": end}]}]}
            with self.subTest(start=start, end=end), patch.object(agent, "llm", return_value=model):
                props = lodging._interpret_pages([CANDIDATE], {URL: source}, report()["requirements"], lodging._CONTEXT.get())
                result = lodging._normalize([CANDIDATE], {"requirements": report()["requirements"], "properties": props}, {URL})
            self.assertEqual(result["items"][0]["checks"][0]["status"], "unknown")

    def test_parser_failure_retains_observed_rating_but_no_verified_conditions(self):
        with patch.object(lodging, "collect_chrome", return_value=page()), \
                patch.object(lodging, "_interpret_pages", side_effect=TimeoutError):
            result, sources = lodging.search([CANDIDATE], "주차 숙소", [])
        item = lodging._normalize([CANDIDATE], result, sources)["items"][0]
        self.assertEqual(item["status"], "unknown")
        self.assertEqual(item["rating"], 4.7)
        self.assertEqual(item["sourceUrl"], URL)

    def test_no_conditions_needs_no_interpretation_call(self):
        lodging._CONTEXT.get()["requirements"] = []
        with patch.object(lodging, "collect_chrome", return_value=page()), patch.object(agent, "llm") as llm:
            result, sources = lodging.search([CANDIDATE], "숙소", [])
        llm.assert_not_called()
        self.assertEqual(lodging._normalize([CANDIDATE], result, sources)["items"][0]["status"], "match")

    def test_blocked_chrome_leaves_no_source_or_rating_and_does_not_cache_failure(self):
        with patch.object(lodging, "collect_chrome", return_value={"status": "blocked"}) as collect, \
                patch.object(lodging, "requirements", return_value=report()["requirements"]), \
                patch.object(lodging, "_interpret_pages") as interpret:
            for _ in range(2):
                item = lodging.verify([PLACE], "주차 숙소")["items"][0]
                self.assertEqual(item["status"], "unknown")
                self.assertFalse(item["sourceUrl"])
                self.assertNotIn("rating", item)
        self.assertEqual(collect.call_count, 2)
        interpret.assert_not_called()

    def test_matching_first_candidate_stops_before_second_browser(self):
        with patch.object(lodging, "collect_chrome", return_value=page()) as collect, \
                patch.object(lodging, "requirements", return_value=[]):
            result = lodging.verify([PLACE, {**PLACE, "placeId": "456"}], "숙소")
        self.assertEqual(result["checkedCount"], 1)
        collect.assert_called_once()

    def test_condition_verification_cannot_outlive_the_source_cache(self):
        clock = [time.monotonic()]
        def interpret(*args):
            clock[0] += 20
            return [{**p, "reviews": []} for p in report()["properties"]]
        with patch.object(lodging, "collect_chrome", return_value={**page(), "cache_remaining_seconds": 31}), \
                patch.object(lodging, "requirements", return_value=report()["requirements"]), \
                patch.object(lodging, "_interpret_pages", side_effect=interpret), \
                patch.object(lodging.time, "monotonic", side_effect=lambda: clock[0]), patch.object(cache, "set") as save:
            result = lodging.verify([PLACE], "숙소")
        self.assertEqual(result["items"][0]["status"], "match")
        self.assertEqual(save.call_args.args[2], 11)

    def test_changed_condition_reuses_source_but_requires_new_assessment(self):
        model = Mock()
        def assess(messages, **kwargs):
            payload = json.loads(messages[1].content)
            req = payload["fixed_requirements"][0]["id"]
            line = next(int(i) for i, s in payload["pages"][0]["details"].items() if s == "주차가능") if req == "parking" else 0
            return {"properties": [{"id": CANDIDATE["id"], "reviews": [], "checks": [
                {"requirement_id": req, "status": "match" if line else "unknown", "evidence_start": line, "evidence_end": line}]}]}
        model.with_structured_output.return_value.invoke.side_effect = assess
        with patch.dict(os.environ, {"LODGING_CHROME_TRANSPORT": "local"}), \
                patch.object(chrome_lodging, "_local", return_value=page()) as local, \
                patch.object(agent, "llm", return_value=model), \
                patch.object(lodging, "requirements", side_effect=[
                    [{"id": "parking", "label": "주차 가능"}], [{"id": "pets", "label": "반려동물 허용"}]]):
            first = lodging.verify([PLACE], "주차 숙소")
            second = lodging.verify([PLACE], "반려동물 숙소")
        local.assert_called_once()
        self.assertEqual(model.with_structured_output.return_value.invoke.call_count, 2)
        self.assertEqual(first["items"][0]["status"], "match")
        self.assertEqual(second["items"][0]["status"], "unknown")
        self.assertEqual(second["items"][0]["checks"][0]["condition"], "반려동물 허용")
        self.assertTrue(second["timing"]["pages"][0]["cache_hit"])

    def test_type_priority_precedes_limit_but_never_proves_a_condition(self):
        motels = [{**PLACE, "placeId": str(i), "name": f"호텔{i}", "detail": "여행 > 숙박 > 여관,모텔"}
                  for i in range(12)]
        hotel = {**PLACE, "placeId": "hotel", "detail": "여행 > 숙박 > 호텔 > 체인"}
        with patch.object(lodging, "requirements", return_value=[
                {"id": "type", "label": "호텔", "basis": "detail", "intent": "required"}]), \
                patch.object(lodging, "collect_chrome", return_value={"status": "not_found"}) as collect:
            result = lodging.verify([*motels, hotel], "호텔 숙박")
        self.assertEqual(collect.call_args_list[0].args[0]["id"], "hotel")
        self.assertEqual(result["checkedCount"], 12)
        self.assertTrue(all(p["status"] == "unknown" and not p["sourceUrl"] for p in result["items"]))

    def test_budget_reserves_condition_call_and_cancellation_is_not_cached(self):
        lodging._CONTEXT.get()["deadline"] = time.monotonic()+29
        with patch.object(lodging, "collect_chrome") as collect:
            result, sources = lodging.search([CANDIDATE], "숙소", [])
        collect.assert_not_called()
        self.assertEqual(result["properties"], [])
        self.assertFalse(sources)
        with patch.object(lodging, "requirements", return_value=[]), \
                patch.object(lodging, "collect_chrome", side_effect=Stopped), patch.object(cache, "set") as save:
            with self.assertRaises(Stopped):
                lodging.verify([PLACE], "숙소")
        save.assert_not_called()
