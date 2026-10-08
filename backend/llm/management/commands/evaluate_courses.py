"""Live, local course evaluation through the public v2 views and real providers.

Explicit --live is required. No mocked recommendations, authentication bypass, or
LangSmith dataset upload. Reports contain only this run's synthetic conversations.
Timing includes views/checkpoints/providers/settlement, not a browser or HTTP proxy.
"""
import json
import math
import os
import statistics
import time
from contextlib import ExitStack
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from django.core.management.base import BaseCommand, CommandError
from rest_framework.test import APIClient

from llm.models import UsageCharge
from llm.service import usage


PRICES = {
    "gpt-6-luna": {"input": .10, "cached": .01, "write": .125, "output": .50},
    "jev-1.13": {"input": .042, "output": 0},
    "text-embedding-3-small": {"input": .02, "output": 0},
}


def cost(call):
    rate = next((v for k, v in PRICES.items() if call.get("model", "").startswith(k)), None)
    if not rate or call.get("input") is None or call.get("output") is None:
        return None
    incoming, outgoing = call["input"], call["output"]
    cached = call.get("cached", 0)
    writes = call.get("cache_write", 0)
    return ((incoming - cached - writes) * rate["input"] + cached * rate.get("cached", rate["input"])
            + writes * rate.get("write", rate["input"]) + outgoing * rate["output"]) / 1_000_000


class Capture:
    def __init__(self):
        self.calls = []
        self.starts = {}
        self.evidence = []

    def hooks(self, stack):
        capture = self
        original_meter = usage.Meter

        class ObservedMeter(original_meter):
            def on_chat_model_start(self, serialized, messages, *, run_id=None, **kwargs):
                params = kwargs.get("invocation_params") or {}
                capture.starts[run_id] = (time.perf_counter(), params.get("model_name") or params.get("model"))
                return super().on_chat_model_start(serialized, messages, run_id=run_id, **kwargs)

            def on_llm_end(self, response, *, run_id, **kwargs):
                started, model = capture.starts.pop(run_id, (time.perf_counter(), "unknown"))
                for batch in response.generations or []:
                    if not batch:
                        continue
                    message = getattr(batch[0], "message", None)
                    meta = getattr(message, "usage_metadata", None) or {}
                    details = meta.get("input_token_details") or {}
                    response_meta = getattr(message, "response_metadata", None) or {}
                    capture.calls.append({"kind": "chat", "model": response_meta.get("model_name") or model,
                        "seconds": round(time.perf_counter() - started, 3),
                        "input": meta.get("input_tokens"), "output": meta.get("output_tokens"),
                        "cached": details.get("cache_read", 0), "cache_write": details.get("cache_creation", 0),
                        "usage": meta, "tier": response_meta.get("service_tier")})
                return super().on_llm_end(response, run_id=run_id, **kwargs)

            def on_llm_error(self, error, *, run_id, **kwargs):
                started, model = capture.starts.pop(run_id, (time.perf_counter(), "unknown"))
                capture.calls.append({"kind": "chat", "model": model, "seconds": round(time.perf_counter() - started, 3),
                                      "input": None, "output": None, "error": type(error).__name__})
                return super().on_llm_error(error, run_id=run_id, **kwargs)

        stack.enter_context(patch.object(usage, "Meter", ObservedMeter))
        from langchain_typesafe import TypeSafeClassifier
        invoke = TypeSafeClassifier.invoke

        def classify(client, *args, **kwargs):
            started = time.perf_counter()
            item = {"kind": "classifier", "model": "jev-1.13", "input": None, "output": None}
            try:
                result = invoke(client, *args, **kwargs)
                reported = getattr(result, "usage", None)
                item.update(input=getattr(reported, "input_tokens", None), output=getattr(reported, "output_tokens", None))
                return result
            finally:
                item["seconds"] = round(time.perf_counter() - started, 3)
                capture.calls.append(item)

        stack.enter_context(patch.object(TypeSafeClassifier, "invoke", classify))
        from openai.resources.embeddings import Embeddings
        embed = Embeddings.create

        def embeddings(client, *args, **kwargs):
            started = time.perf_counter()
            item = {"kind": "embedding", "model": kwargs.get("model", "unknown"), "input": None, "output": 0}
            try:
                result = embed(client, *args, **kwargs)
                item["input"] = result.usage.prompt_tokens
                return result
            finally:
                item["seconds"] = round(time.perf_counter() - started, 3)
                capture.calls.append(item)

        stack.enter_context(patch.object(Embeddings, "create", embeddings))
        from openai.resources.responses import Responses
        respond = Responses.create

        def responses(client, *args, **kwargs):
            # The lodging verifier uses Responses directly, outside LangChain callbacks.
            # Streaming LangChain calls are already observed by Meter; do not consume or duplicate them.
            if kwargs.get("stream") or not any(t.get("type") == "web_search" for t in kwargs.get("tools", [])):
                return respond(client, *args, **kwargs)
            started = time.perf_counter()
            item = {"kind": "responses", "model": kwargs.get("model", "unknown"), "input": None, "output": None}
            try:
                result = respond(client, *args, **kwargs)
                reported = result.usage.model_dump() if result.usage else {}
                details = reported.get("input_tokens_details") or {}
                item.update(model=result.model, input=reported.get("input_tokens"), output=reported.get("output_tokens"),
                            cached=details.get("cached_tokens", 0), cache_write=details.get("cache_write_tokens", details.get("cache_creation_tokens", 0)),
                            usage=reported, tier=getattr(result, "service_tier", None),
                            response_status=result.status,
                            web_actions=[part.model_dump() for part in result.output if getattr(part, "type", "") == "web_search_call"],
                            web_search_calls=sum(getattr(part, "type", "") == "web_search_call" and
                                (part.model_dump().get("action") or {}).get("type") == "search" for part in result.output))
                return result
            except Exception as exc:
                item["error"] = type(exc).__name__
                code = getattr(exc, "code", None)
                if isinstance(code, str) and code.replace("_", "").isalpha():
                    item["error_code"] = code[:80]
                raise
            finally:
                item["seconds"] = round(time.perf_counter() - started, 3)
                capture.calls.append(item)

        stack.enter_context(patch.object(Responses, "create", responses))
        from llm.v1.rag.course import evidence_memory
        search, enrich = evidence_memory.search, evidence_memory.enrich

        def observed_search(candidates, requirements):
            item = {"kind": "search", "candidates": deepcopy(candidates),
                    "requirements": [r.model_dump() for r in requirements]}
            capture.evidence.append(item)
            try:
                findings, opened, calls = search(candidates, requirements)
                item.update(findings=[f.model_dump() for f in findings], opened=sorted(opened), calls=calls)
                budget = evidence_memory._BUDGET.get() or {}
                item["grounding"] = deepcopy(budget.get("grounding", []))
                return findings, opened, calls
            except Exception as exc:
                item["error"] = type(exc).__name__
                raise

        def observed_enrich(candidates, conditions):
            result = enrich(candidates, conditions)
            capture.evidence.append({"kind": "filter", "conditions": list(conditions),
                                     "candidates": deepcopy(candidates), "accepted": deepcopy(result)})
            return result

        stack.enter_context(patch.object(evidence_memory, "search", observed_search))
        stack.enter_context(patch.object(evidence_memory, "enrich", observed_enrich))
        from llm.v1.rag.nearby import lodging
        from llm.v1.rag.course import editing
        lodging_verify, lodging_search, interpret = lodging.verify, lodging.search, editing.interpret

        def observed_lodging_search(*args, **kwargs):
            report, opened = lodging_search(*args, **kwargs)
            capture.evidence.append({"kind": "lodging_search", "opened": sorted(opened),
                                     "properties": [{**{k: p.get(k) for k in ("id", "name", "address", "url", "rating", "review_count", "lodging_type", "type_evidence")},
                                                     "reviews": [{"date": r.get("observed_on"), "condition": r.get("requirement_id"),
                                                                  "polarity": r.get("polarity"), "quote_length": len(r.get("quote", ""))}
                                                                 for r in p.get("reviews", [])]}
                                                    for p in report["properties"]]})
            return report, opened

        def observed_lodging(places, *args, **kwargs):
            result = lodging_verify(places, *args, **kwargs)
            capture.evidence.append({"kind": "lodging", "result": deepcopy(result),
                                     "recommendations": lodging.recommendations(result),
                                     "candidates": [{k: p.get(k) for k in ("placeId", "name", "address", "distance")} for p in places]})
            return result

        def observed_interpret(*args, **kwargs):
            result = interpret(*args, **kwargs)
            capture.evidence.append({"kind": "activities", "visits": result.get("requested_visits"),
                                     "request_preferences": result.get("request_preferences")})
            return result

        stack.enter_context(patch.object(lodging, "verify", observed_lodging))
        stack.enter_context(patch.object(lodging, "search", observed_lodging_search))
        stack.enter_context(patch.object(editing, "interpret", observed_interpret))


def current(course):
    value = deepcopy(course)
    value["travelMode"] = course.get("travel", {}).get("mode", "walk")
    value.setdefault("legModes", {})
    for i, place in enumerate(value["places"]):
        place.setdefault("visitId", f"evaluation-visit-{i}")
        place["label"] = str(i + 1)
    return value


def identities(course):
    return [(p.get("placeId"), p["name"], p["lat"], p["lng"]) for p in (course or {}).get("places", [])]


def distance(a, b):
    lat1, lat2 = map(math.radians, [a["lat"], b["lat"]])
    dl = math.radians(b["lng"] - a["lng"])
    return 6371000 * 2 * math.asin(min(1, math.sqrt(math.sin((lat2-lat1)/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dl/2)**2)))


def checks(case, before, after, done, history):
    result = {"completed": bool(done and not done.get("error")), "course_present": bool(after)}
    if case.get("expect_no_course"):
        answer = (done or {}).get("assistant_message", "")
        return {"completed": bool(done), "no_unverified_places": not after,
                "explains_unverified_conditions": "확인" in answer and ("없" in answer or "못" in answer)}
    if case.get("allow_no_course") and not after:
        return {"completed": bool(done and not done.get("error")), "honest_unverified": "확인하지 못" in (done or {}).get("assistant_message", "")}
    if not after:
        return result
    places = after["places"]
    stadium = next((p for p in places if p["category"] == "STADIUM"), None)
    result.update(single_stadium=sum(p["category"] == "STADIUM" for p in places) == 1,
                  radius_2500=bool(stadium and all(distance(stadium, p) <= 2501 for p in places)),
                  unique_places=len(identities(after)) == len(set(identities(after))),
                  sources=all(p.get("placeUrl", "").startswith("https://") for p in places if p["category"] != "STADIUM"),
                  timeline=all(p.get("time") and p.get("stayMin") is not None for p in places),
                  saved_course=any(m.get("course") == after for m in history if m.get("role") == "assistant"))
    if case.get("stadium"):
        result["stadium"] = after.get("stadiumCode") == case["stadium"]
    if "categories" in case:
        result["requested_only"] = [p["category"] for p in places] == case["categories"]
    if case.get("walk"):
        result["walk_default"] = after.get("travel", {}).get("mode") == "walk"
    if case.get("late"):
        result["time_warning"] = bool(after.get("timeWarning"))
    action = case.get("action")
    old = identities(before)
    new = identities(after)
    if action in ("replace_cafe", "replace_food"):
        category = "CAFE" if action == "replace_cafe" else "FOOD"
        targets = [i for i, p in enumerate(before["places"]) if p["category"] == category]
        result["target_only_replaced"] = len(old) == len(new) and bool(targets) and all(
            (old[i] != new[i]) if i in targets else (old[i] == new[i]) for i in range(min(len(old), len(new))))
        if case.get("brand"):
            result["cafe_brand"] = all(case["brand"] in places[i]["name"] for i in targets)
    elif action == "swap":
        result["exact_swap"] = len(old) >= 2 and new == [old[1], old[0], *old[2:]]
    elif action == "transport":
        from llm.v1.rag.course.editing import leg_key
        cafe = next(p for p in before["places"] if p["category"] == "CAFE")
        stadium = next(p for p in before["places"] if p["category"] == "STADIUM")
        result["only_requested_leg"] = after.get("legModes") == {leg_key(cafe, stadium): "transit"}
        result["places_preserved"] = new == old
        result["walk_default_preserved"] = after.get("travel", {}).get("mode") == "walk"
    elif action == "compound":
        result["cafe_first_20"] = places[0]["category"] == "CAFE" and places[0].get("stayMin") == 20
        result["starbucks"] = "스타벅스" in places[0]["name"]
        result["others_preserved"] = [p.get("placeId") for p in places if p["category"] != "CAFE"] == [p.get("placeId") for p in before["places"] if p["category"] != "CAFE"]
        result["no_implicit_exclusion"] = not done.get("coursePreferences", {}).get("rejectedPlaces")
    elif action == "origin_only":
        result["places_preserved"] = new == old
        result["origin_changed"] = bool(after.get("origin") and "삼성역" in after["origin"].get("name", ""))
    elif action in ("delay", "complete", "game_delay", "duration", "lock"):
        result["places_preserved"] = new == old
        result["game_preserved"] = after.get("game") == before.get("game")
        if action == "complete":
            result["completed_first"] = places[0].get("completed") is True and not any(p.get("completed") for p in places[1:])
        if action == "delay":
            result["time_changed"] = places[0]["time"] != before["places"][0]["time"]
        if action == "game_delay":
            result["before_game_fixed"] = all(p["time"] == q["time"] for p, q in zip(before["places"], places) if p["phase"] != "AFTER")
            result["after_game_changed"] = any(p["time"] != q["time"] for p, q in zip(before["places"], places) if p["phase"] == "AFTER")
        if action == "duration":
            result["cafe_20_minutes"] = all(p["stayMin"] == 20 for p in places if p["category"] == "CAFE")
        if action == "lock":
            result["lock_recorded"] = bool(done.get("coursePreferences", {}).get("lockedPlaces"))
    elif action == "indoor":
        result["only_walk_replaced"] = len(old) == len(new) and all(
            new[i] != old[i] and places[i]["category"] == "INDOOR" if p["category"] in ("WALK", "SPOT") else new[i] == old[i]
            for i, p in enumerate(before["places"]))
        result["completed_preserved"] = all(p == places[i] for i, p in enumerate(before["places"]) if p.get("completed"))
    return result


def evidence_checks(case, course, traces):
    if not case.get("evidence_terms") or not course:
        return {}
    from llm.v1.rag.course.evidence_memory import keyword_text
    relevant = [p for p in course["places"] if p["category"] == case.get("evidence_category", "FOOD")]
    required = {keyword_text(term) for term in case["evidence_terms"]}
    def verified(place):
        for trace in traces:
            for accepted in trace.get("accepted", []):
                if accepted.get("placeId") != place.get("placeId"):
                    continue
                matched = {keyword_text(c["term"]) for c in accepted.get("conditionChecks", []) if c["status"] == "match"}
                if required.issubset(matched):
                    return True
        return False
    return {"selected_places_have_requested_evidence": bool(relevant) and all(map(verified, relevant))}


def scenarios():
    base = "경기 시작 전에 든든한 밥 먹고 저가 카페에서 커피 마시고 야구 본 다음 산책만 하고 싶어. 코스 짜줘."
    cats = ["FOOD", "CAFE", "STADIUM", "WALK"]
    return [
        ("lodging_munhak", [{"id": "lodging", "text": "문학 경기 보고 나서 깔끔하고 조용한 숙소 하나 들르는 코스 짜줘.",
                              "stadium": "MUNHAK", "categories": ["STADIUM", "STAY"], "manual_review": True}]),
        ("lodging_clean_hotel", [{"id": "hotel", "text": "문학 경기 보고 나서 깔끔한 호텔 하나 가는 코스 짜줘.",
                                  "stadium": "MUNHAK", "categories": ["STADIUM", "STAY"], "manual_review": True}]),
        ("semantic_steak", [{"id": "steak", "text": "여자친구랑 스테이크 썰고 카페 갔다가 문학 구장 가고 싶어. 이후에 깔끔한 호텔 하나 추천해줘.",
                              "stadium": "MUNHAK", "categories": ["FOOD", "CAFE", "STADIUM", "STAY"],
                              "evidence_terms": ["스테이크"], "manual_review": True}]),
        ("evidence_menu", [{"id": "menu", "text": "잠실 경기 전에 돈까스를 파는 식당 한 곳에 들렀다가 경기장으로 가는 코스를 짜줘. 실제 메뉴 근거가 확인되는 곳으로 부탁해.",
                            "stadium": "JAMSIL", "categories": ["FOOD", "STADIUM"], "evidence_terms": ["돈까스"], "manual_review": True}]),
        ("evidence_quiet", [{"id": "quiet", "text": "잠실 경기 전에 조용한 카페 한 곳만 들르는 코스 짜줘. 실제 이용 후기에서 조용하다는 근거를 확인해줘. 확인되지 않으면 조건 미확인이라고 알려줘.",
                             "stadium": "JAMSIL", "allow_no_course": True, "evidence_terms": ["조용함"], "evidence_category": "CAFE", "manual_review": True}]),
        ("evidence_quiet_gocheok", [{"id": "quiet", "text": "고척 경기 전에 조용한 카페 한 곳만 들르는 코스 짜줘. 실제 이용 후기 근거가 있어야 해. 확인되지 않으면 조건 미확인이라고 알려줘.",
                                     "stadium": "GOCHEOK", "allow_no_course": True, "evidence_terms": ["조용함"], "evidence_category": "CAFE", "manual_review": True}]),
        ("evidence_menu_and", [{"id": "menu_and", "text": "잠실 경기 전에 돈까스와 냉모밀을 둘 다 파는 식당 한 곳에 들르는 코스 짜줘. 두 메뉴 모두 실제 메뉴 근거를 확인해줘.",
                                "stadium": "JAMSIL", "categories": ["FOOD", "STADIUM"], "evidence_terms": ["돈까스", "냉모밀"], "manual_review": True}]),
        ("evidence_menu_suwon", [{"id": "suwon_menu", "text": "수원 KT 홈경기 전에 짬뽕을 파는 식당 한 곳에 들르는 코스 짜줘. 실제 메뉴 목록에서 판매 근거를 확인해줘.",
                                  "stadium": "SUWON", "categories": ["FOOD", "STADIUM"], "evidence_terms": ["짬뽕"], "manual_review": True}]),
        ("evidence_dialogue", [
            {"id": "base", "text": base, "stadium": "JAMSIL", "categories": cats},
            {"id": "replace_menu", "text": "식당만 돈까스를 파는 곳으로 교체해줘. 실제 메뉴 근거가 있어야 해. 다른 장소는 그대로 둬.",
             "action": "replace_food", "evidence_terms": ["돈까스"], "manual_review": True},
            {"id": "replace_unverified", "text": "카페만 수영장이 있고 반려동물과 입장 가능하고 24시간 운영하는 곳으로 바꿔줘. 모두 확인되어야 해. 다른 장소는 그대로 둬.",
             "expect_no_course": True, "manual_review": True},
        ]),
        ("evidence_optional", [{"id": "optional", "text": "잠실 경기 전에 카페 한 곳만 들르는 코스 짜줘. 가능하면 조용한 곳이면 좋겠지만 확인이 안 되면 일반 카페도 괜찮아. 조용함을 확인했다고 단정하지 마.",
                                "stadium": "JAMSIL", "categories": ["CAFE", "STADIUM"], "manual_review": True}]),
        ("dialogue_controls", [
            {"id": "generate", "text": base, "stadium": "JAMSIL", "categories": cats, "walk": True},
            {"id": "leg", "text": "카페에서 구장까지 구간만 대중교통으로 바꿔줘. 나머지 구간과 장소는 그대로 유지해.", "action": "transport"},
            {"id": "compound", "text": "카페를 스타벅스로 바꾸고 식당 바로 앞으로 옮겨줘. 카페 체류는 20분. 다른 장소는 유지하고 이번 요청에만 적용해.", "action": "compound"},
            {"id": "origin_only", "text": "출발지만 삼성역으로 바꿔줘. 다른 장소와 순서는 그대로.", "action": "origin_only"},
            {"id": "undo", "text": "방금 출발지 변경 취소하고 되돌려줘.", "undo": True},
        ]),
        ("conversation", [
            {"id": "generate", "text": base, "stadium": "JAMSIL", "categories": cats, "walk": True},
            {"id": "replace", "text": "카페만 다른 메가MGC커피로 바꿔줘. 다른 장소는 그대로 둬.", "action": "replace_cafe", "brand": "메가"},
            {"id": "swap", "text": "1번과 2번 순서만 바꿔줘. 장소는 모두 그대로.", "action": "swap"},
            {"id": "undo", "text": "방금 순서 바꾼 것 취소하고 되돌려줘.", "undo": True},
            {"id": "lock", "text": "식당은 마음에 들어. 앞으로 이 식당은 고정해줘.", "action": "lock"},
            {"id": "duration", "text": "카페 체류시간만 20분으로 줄여줘.", "action": "duration"},
            {"id": "delay", "text": "예정보다 30분 늦게 출발해. 남은 일정 시간을 조정해줘.", "action": "delay"},
            {"id": "complete", "text": "1번 식당은 방문 완료했어. 기존 예정대로 식사를 마쳤어.", "action": "complete"},
            {"id": "indoor", "text": "비가 와. 경기 후 산책만 실내 놀거리로 바꿔줘.", "action": "indoor"},
            {"id": "game_delay", "text": "경기가 예상보다 30분 늦게 끝나. 경기 이후 일정만 30분 늦춰줘.", "action": "game_delay"},
            {"id": "switch", "text": "이번에는 롯데 홈구장에서 경기 전 식사와 카페, 경기 후 산책 코스를 새로 짜줘.", "stadium": "SAJIK", "categories": cats},
        ]),
        ("west_origin", [{"id": "origin", "text": base, "stadium": "JAMSIL", "categories": cats,
                           "origin": {"lat": 37.5045, "lng": 127.035}, "outside_origin": True}]),
        ("drawn_path", [{"id": "corridor", "text": "지도에서 선택한 경로 주변에서 경기 전 카페 한 곳, 경기 후 산책 한 곳만 짜줘.",
                        "stadium": "JAMSIL", "categories": ["CAFE", "STADIUM", "WALK"],
                        "routePath": {"source": "drawn", "label": "동쪽 진입 평가 경로", "points": [
                            {"lat": 37.510, "lng": 127.106}, {"lat": 37.509, "lng": 127.091}, {"lat": 37.5122, "lng": 127.0719}]}}]),
        ("gwangju", [{"id": "team", "text": "기아 홈경기 보러 가는데 경기 전에 밥 한 끼 먹고 경기 후 산책만 할래. 코스 짜줘.",
                       "stadium": "GWANGJU", "categories": ["FOOD", "STADIUM", "WALK"], "walk": True}]),
        ("impossible", [{"id": "unsupported", "text": "잠실 경기 전에 카페 한 곳만 갈래. 수영장이 있고 반려동물과 입장 가능하고 24시간 여는 카페만 추천해줘. 조건 확인 안 되면 없다고 해줘.", "stadium": "JAMSIL", "expect_no_course": True, "manual_review": True}]),
        ("late", [{"id": "late", "text": "잠실 경기 전 식사 한 곳과 카페 한 곳, 경기 후 산책 코스를 짜줘. 오후 1시 50분부터 출발할 거야.", "stadium": "JAMSIL", "categories": cats, "late": True}]),
        ("lodging", [{"id": "lodging", "text": "잠실 경기 관람 후 숙박 한 곳으로 이동하는 코스를 짜줘. 주차 가능하고 금연 객실이 있는 호텔이어야 해. 야놀자에서 확인하고 가격과 예약 가능 여부는 말하지 마.", "stadium": "JAMSIL", "categories": ["STADIUM", "STAY"], "walk": True, "manual_review": True}]),
    ]


class Command(BaseCommand):
    help = "Measure real course conversations (paid provider calls; requires --live)."

    def add_arguments(self, parser):
        parser.add_argument("--live", action="store_true")
        parser.add_argument("--output", default="artifacts/course-evaluation.json")
        parser.add_argument("--scenario", choices=[x[0] for x in scenarios()])
        parser.add_argument("--limit", type=int, default=20)
        parser.add_argument("--embedding-input-usd-per-million", type=float)

    def handle(self, *args, **options):
        if not options["live"]:
            raise CommandError("Real paid APIs are used. Review scenarios and pass --live explicitly.")
        if not 1 <= options["limit"] <= 30:
            raise CommandError("--limit must be 1..30")
        if options["embedding_input_usd_per_million"] is not None:
            from llm.v1.rag.club.retrieval import EMBED_MODEL
            PRICES[EMBED_MODEL] = {"input": options["embedding_input_usd_per_million"], "output": 0}
        report = {"started_at": datetime.now(timezone.utc).isoformat(), "model": os.getenv("LLM_MODEL"),
                  "method": "Public Django APIClient views + real providers, guest sessions; excludes proxy/browser time",
                  "price_date": "2026-10-05", "prices_per_million": deepcopy(PRICES), "web_search_usd_per_call": .01,
                  "price_sources": ["https://developers.openai.com/api/docs/pricing", "https://openrouter.ai/typesafe/jev-1.13",
                                    "https://developers.openai.com/api/docs/models/text-embedding-3-small"],
                  "turns": []}
        output = Path(options["output"])
        output.parent.mkdir(parents=True, exist_ok=True)
        client = APIClient(HTTP_HOST="localhost")
        capture = Capture()
        with ExitStack() as stack:
            capture.hooks(stack)
            for name, cases in scenarios():
                if options["scenario"] and options["scenario"] != name:
                    continue
                if len(report["turns"]) >= options["limit"]:
                    break
                response = client.post("/api/v2/chat/sessions/", {"title": "코스 품질 평가 " + name}, format="json")
                if response.status_code != 201:
                    raise CommandError(f"Session creation failed: {response.status_code}")
                session = response.data["id"]
                endpoint = f"/api/v2/chat/sessions/{session}/messages/"
                course, snapshots = None, []
                for case in cases:
                    if len(report["turns"]) >= options["limit"]:
                        break
                    context = {"intent": "route", "stadium": case.get("stadium") or (course or {}).get("stadiumCode", "JAMSIL")}
                    # A team mention must override the existing map selection, just as in the UI.
                    if case["id"] == "switch":
                        context["stadium"] = "JAMSIL"
                    for key in ("origin", "routePath"):
                        if key in case:
                            context[key] = case[key]
                    if course:
                        context["currentCourse"] = current(course)
                    before = deepcopy(course)
                    capture.calls.clear()
                    capture.evidence.clear()
                    started = time.perf_counter()
                    done, frames, first_text, errors = None, [], None, []
                    response = client.post(endpoint, {"content": case["text"], "context": context}, format="json", HTTP_ACCEPT="text/event-stream")
                    if getattr(response, "streaming", False):
                        for chunk in response.streaming_content:
                            raw = chunk.decode() if isinstance(chunk, bytes) else chunk
                            for frame in raw.strip().split("\n\n"):
                                lines = frame.splitlines()
                                event = next((l[7:] for l in lines if l.startswith("event: ")), "")
                                data = json.loads(next(l[6:] for l in lines if l.startswith("data: ")))
                                elapsed = round(time.perf_counter() - started, 3)
                                frames.append({"event": event, "seconds": elapsed})
                                if event == "delta" and first_text is None:
                                    first_text = elapsed
                                if event == "done":
                                    done = data
                                if event == "error":
                                    errors.append(data)
                        response.close()
                    elapsed = round(time.perf_counter() - started, 3)
                    after = (done or {}).get("course")
                    history_response = client.get(endpoint)
                    history = history_response.data if isinstance(history_response.data, list) else []
                    assertions = checks(case, before, after, done, history)
                    assertions.update(evidence_checks(case, after, capture.evidence))
                    if case.get("undo") and after:
                        assertions["undo_exact_places"] = len(snapshots) >= 2 and identities(after) == identities(snapshots[-2])
                    if case.get("outside_origin") and after:
                        assertions["origin_retained"] = bool(after.get("origin"))
                        assertions["entry_point"] = bool(after.get("entryPoint"))
                    charge = UsageCharge.objects.filter(session_id=session).order_by("-created_at").first()
                    calls = deepcopy(capture.calls)
                    for call in calls:
                        call["usd"] = cost(call)
                        call["web_search_usd"] = call.get("web_search_calls", 0) * .01
                    billable = [call for call in calls if call["kind"] != "embedding"]
                    reconciled = bool(charge and not charge.unknown_calls and
                                      all(c.get("input") is not None and c.get("output") is not None for c in billable) and
                                      charge.input_tokens == sum(c["input"] for c in billable) and
                                      charge.output_tokens == sum(c["output"] for c in billable) and charge.calls == len(billable))
                    item = {"scenario": name, "id": case["id"], "request": case["text"], "context": context,
                            "http_status": response.status_code, "first_text_seconds": first_text, "total_seconds": elapsed,
                            "checks": assertions, "pass": all(assertions.values()), "manual_review": case.get("manual_review", False),
                            "answer": (done or {}).get("assistant_message"), "done": done, "errors": errors, "events": frames,
                            "calls": calls, "evidence": deepcopy(capture.evidence), "known_model_usd": round(sum(c["usd"] or 0 for c in calls), 8),
                            "web_search_usd": round(sum(c["web_search_usd"] for c in calls), 8),
                            "ledger_reconciled": reconciled,
                            "unpriced_calls": sum(c["usd"] is None for c in calls),
                            "ledger": ({k: getattr(charge, k) for k in ("status", "input_tokens", "output_tokens", "calls", "unknown_calls")} if charge else None)}
                    report["turns"].append(item)
                    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                    failed = [k for k, v in assertions.items() if not v]
                    self.stdout.write(json.dumps({"scenario": name, "id": case["id"], "seconds": elapsed, "failed": failed,
                                                  "usd": item["known_model_usd"], "unpriced": item["unpriced_calls"]}, ensure_ascii=False))
                    self.stdout.flush()
                    if after:
                        course = after
                        snapshots.append(deepcopy(after))
                    if not course and len(cases) > 1:
                        break  # Dependent edits cannot be evaluated without a base course.
        turns = report["turns"]
        report["summary"] = {"turns": len(turns), "passed": sum(t["pass"] for t in turns),
                             "median_seconds": statistics.median(t["total_seconds"] for t in turns) if turns else None,
                             "known_model_usd": round(sum(t["known_model_usd"] for t in turns), 8),
                             "web_search_usd": round(sum(t["web_search_usd"] for t in turns), 8),
                             "ledger_reconciled_turns": sum(t["ledger_reconciled"] for t in turns),
                             "unpriced_calls": sum(t["unpriced_calls"] for t in turns)}
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        self.stdout.write(json.dumps(report["summary"], ensure_ascii=False))
