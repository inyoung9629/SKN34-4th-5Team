"""Chat wiring for the deterministic planner. Never stream an unvalidated course."""
from datetime import datetime
from copy import deepcopy
import re
from urllib.parse import quote

from django.conf import settings
from django.utils import timezone

from .itinerary_planner import ItineraryPlanner, RoutingUnavailable
from .itinerary_request import ItineraryRequest, KST, extract_itinerary
from .food_verification import public_url
from .knowledge_verification import KnowledgeVerification, KnowledgeFoodVerifier, KnowledgeReviewVerifier
from .revision import prepare_revision, RevisionClarification

COORDINATES = r"(?:위도\s*)?(-?\d+(?:\.\d+)?)\s*,\s*(?:경도\s*)?(-?\d+(?:\.\d+)?)"


MESSAGES = {
    "knowledge_insufficient": "요청한 메뉴·후기를 확인할 재사용 가능한 근거가 부족해 코스를 확정하지 않았어요. 장소가 없거나 메뉴를 팔지 않는다는 뜻은 아니에요. 자동 유료 웹검색은 꺼져 있으며 조건도 임의로 완화하지 않았어요. 상세 근거 검증을 연결하거나 해당 조건을 변경해야 해요.",
    "candidate_search_incomplete": "요청한 반경의 카카오 장소 목록을 끝까지 확보하지 못해 코스를 확정하지 않았어요. 밀집 구역을 나누어 조회하지만 호출·시간 제한이나 응답 변동이 있을 수 있어요. 조건에 맞는 장소가 없다는 뜻은 아니에요. 범위를 좁히거나 잠시 후 다시 요청해 주세요.",
    "candidate_search_unavailable": "카카오 장소 검색에 연결하지 못했거나 응답을 검증하지 못해 코스를 확정하지 않았어요. 일부 목록을 전체 후보처럼 취급하거나 소상공인 자료로 몰래 대체하지 않았어요.",
    "review_search_limited": "후기 검색 횟수·시간 한도 안에서 필수 조건의 근거를 충분히 확인하지 못했어요. 조용하거나 청결한 식당이 없다는 뜻은 아니에요. 범위나 식당을 좁혀 다시 확인할 수 있어요.",
    "review_search_unavailable": "후기 웹검색에 연결하지 못해 필수 후기 조건이 있는 코스를 확정하지 않았어요. 모델 지식이나 상호명으로 조용함·청결도를 대신 판단하지 않았어요.",
    "review_unverified": "같은 지점의 최근 후기와 실제 방문 시간에 맞는 근거가 부족하거나 서로 엇갈려 필수 후기 조건을 확인하지 못했어요. 조건을 몰래 완화하지 않았어요. 해당 조건을 선호로 바꿀지 알려 주세요.",
    "review_mismatch": "검토한 후보의 최근 후기에 요청한 특징과 반대되는 근거가 있어 필수 후기 기준을 통과한 코스를 찾지 못했어요. 매장의 실제 상태를 확정한 것은 아니며 검색하지 못한 다른 후보가 있을 수 있어요.",
    "food_search_limited": "음식 조건을 확인하는 웹검색 횟수·시간 한도에 도달해 코스를 확정하지 못했어요. 조건에 맞는 식당이 없다는 뜻은 아니에요. 원하는 식당명이나 범위를 좁혀 다시 확인할 수 있어요.",
    "food_search_unavailable": "음식 종류·메뉴 웹검색에 연결하지 못해 코스를 확정하지 않았어요. 검색 연결이나 모델 지원 상태를 확인한 뒤 다시 시도해 주세요. 모델 지식만으로 판매 메뉴를 채우지 않았어요.",
    "food_unverified": "검색한 자료만으로 지점·외부 식당 여부 또는 요청 메뉴의 조건을 확정하지 못했어요. 미확인은 미판매라는 뜻이 아니며 조건을 임의로 완화하지 않았어요.",
    "gate_unavailable": "해당 경기의 구장 개방 시각을 확인하지 못해 내부 코스를 확정하지 않았어요. 개방 시각을 알려 주세요. NC 오픈프랙티스나 멤버십 전용 시각을 일반 입장 시간으로 대신 쓰지 않았어요.",
    "gate_window_too_short": "개방 시각부터 경기 시작까지 요청한 내부 체류·이동시간을 모두 넣을 수 없어요. 내부 방문 또는 체류시간을 조정할까요? 자동으로 줄이지 않았어요.",
    "internal_no_match": "수집한 구장 내부 목록에서 요청한 먹거리·시설을 찾지 못했어요. 주변 외부 매장으로 대신 채우지 않았어요. 내부 방문 조건을 변경할까요?",
    "internal_scope_unverified": "요청한 시설의 구장 소속 정보는 있지만 입장 후 내부 시설인지 확정되지 않았어요. 내부 코스에 넣기 전에 위치·입장 구역 확인이 필요해요.",
    "internal_details_unverified": "구장 내부 목록만으로 요청한 음식 분류·상세 조건을 확인하지 못했어요. 해당 조건을 만족한다고 단정하지 않았어요.",
    "constraints_unverified": "필수 조건을 현재 수집본만으로 확인할 수 없어요. 조건을 만족한다고 단정하거나 임의로 완화하지 않았어요. 웹검색 등으로 확인한 뒤 진행하거나, 해당 조건을 선호로 바꿀지 알려 주세요.",
    "unsupported_lodging": "요청한 숙박은 유지했지만, 카카오 숙박 조회가 아직 챗봇 시간 검증에 연결되지 않았어요. 숙박을 임의로 빼거나 추정 장소로 채우지 않았어요.",
    "directions_unavailable": "길찾기 결과를 확인하지 못해 전체 일정의 이동시간을 검증하지 못했어요. 직선거리로 이동시간을 대신 만들어 코스를 확정하지 않았어요. 잠시 후 다시 요청해 주세요.",
    "coverage_incomplete": "검색 범위 일부가 현재 수집 영역 밖이라 조건에 맞는 장소가 없는지 확인할 수 없어요. 수집 범위 안에서도 완성 가능한 코스를 찾지 못했어요. 출발지·방문 조건을 조정할까요?",
    "time_infeasible": "현재 검색 범위에서 모든 방문·체류·이동·여유시간을 포함해 목표 시각을 지키는 코스를 찾지 못했어요. 출발을 앞당기거나 방문 또는 체류시간을 조정할까요? 조건은 그대로 유지했어요.",
    "distance_limit": "지정한 이동거리 제한 안에서 모든 방문을 연결하지 못했어요. 거리 제한을 늘리거나 방문 조건을 바꿀까요? 자동으로 제한을 넘기지는 않았어요.",
    "no_match": "설정한 최대 검색 반경까지 확인했지만 조회된 후보에서 조건에 맞는 완성 코스를 찾지 못했어요. 실제 장소가 없다는 뜻은 아니에요. 검색 거리 또는 조건을 변경할까요?",
    "search_limited": "검색·길찾기 횟수 또는 처리시간 한도 안에서 전체 코스 검증을 완료하지 못했어요. 가능한 코스가 없다는 뜻은 아니에요. 방문 조건을 더 좁혀 다시 요청해 주세요.",
    "data_unavailable": "검증된 구장 좌표 또는 수집 장소 데이터를 불러오지 못했어요. 장소가 없다고 판단하거나 다른 구장으로 대체하지 않았어요.",
    "extraction_failed": "방문 순서·출발시각·이동 조건을 정확히 해석하지 못했어요. 조건을 유지했으니 원하는 방문 순서와 시간을 조금 더 구체적으로 알려 주세요.",
}


def resolve_origin(label, preferences, places, stadium, *, coordinate_sources=()):
    """Only supplied coordinates or a unique exact catalogue name. No model geocoding."""
    value = label or preferences.get("origin")
    if not value:
        return None
    match = re.fullmatch(r"\s*" + COORDINATES + r"\s*", value)
    if match:
        lat, lng = map(float, match.groups())
        supplied = {(float(m[0]), float(m[1])) for source in coordinate_sources
                    for m in re.findall(COORDINATES, source)}
        if -90 <= lat <= 90 and -180 <= lng <= 180 and (lat, lng) in supplied:
            return {"lat": lat, "lng": lng, "placeId": "origin", "name": "출발지"}
    exact = [p for p in [stadium, *places] if p["name"].replace(" ", "") == value.replace(" ", "")]
    if len(exact) == 1:
        return exact[0]
    raise ValueError("출발지의 정확한 좌표를 확인하지 못했어요. 출발 위치를 지정하거나 출발지 없이 첫 장소부터 코스를 짤지 알려 주세요.")


def origin_coordinate_sources(inputs, state):
    """Coordinates must have user/UI provenance, not merely model extraction."""
    values = [inputs["question"]]
    values += [str(m.content) for m in inputs.get("chat_history") or ()
               if getattr(m, "type", "") in ("human", "user")]
    screen = (state or {}).get("screen_context", {}).get("origin")
    previous = (state or {}).get("itinerary_origin")
    for origin in (screen, previous):
        if origin:
            values.append(f"{origin['lat']},{origin['lng']}")
    return values


def load_planning_data(anchor):
    from baseball.models import Stadium
    from baseball.stadium_locations import reviewed_venue
    from travel.collected_places import CatalogueUnavailable
    from travel.kakao_course_candidates import KakaoCourseCandidates

    venue = reviewed_venue(anchor["stadium_code"])
    if not venue:
        row = Stadium.objects.filter(stadium_code=anchor["stadium_code"]).first()
        if row is None:
            raise CatalogueUnavailable
        venue = {"lat": float(row.latitude), "lng": float(row.longitude)}
    stadium = {"placeId": f"stadium:{anchor['stadium_code']}", "name": anchor["stadium_name"],
               "lat": venue["lat"], "lng": venue["lng"], "kind": "stadium"}
    data = {"places": [], "coverage": [], "snapshotId": None,
            "candidate_source": KakaoCourseCandidates(anchor["stadium_code"])}
    return data, stadium


def generate_itinerary(inputs, config=None):
    """Runnable adapter; existing SSE and message snapshot persistence remain unchanged."""
    from travel.collected_places import CatalogueUnavailable, CatalogueQueryError
    from travel.directions_provider import DirectionsError, fetch_directions
    from travel.kakao_course_candidates import CandidateSearchError
    from llm.service.usage import UsageExhausted

    anchor = inputs["course_anchor"]
    preferences = inputs.get("course_preferences") or {}
    state = inputs.get("course_state")
    previous = (state or {}).get("itinerary_request")
    previous_state = inputs.get('course_previous_state') or {}
    if not (state or {}).get('itinerary_reset') and (previous_state.get('itinerary_result') or {}).get('status') != 'ok':
        previous_state = previous_state.get('itinerary_baseline') or previous_state
    history = () if (state or {}).get("itinerary_reset") else inputs.get("chat_history") or ()
    now = timezone.now().astimezone(KST)
    try:
        request = extract_itinerary(inputs["question"], anchor, preferences, previous,
                                    history, now=now, previous_result=previous_state.get('itinerary_result'))
    except UsageExhausted:
        # Budget exhaustion is not a malformed user request. Preserve the
        # metering layer's typed failure and standard SSE error handling.
        raise
    except Exception:
        # Invalid extraction must not become a default unconstrained recommendation.
        result = {"version": 1, "status": "extraction_failed", "stops": [], "legs": []}
        if state is not None:
            state["itinerary_result"] = result
        yield MESSAGES["extraction_failed"]
        return
    if state is not None:
        state["itinerary_request"] = request.model_dump(mode="json")
        state.pop("itinerary_reset", None)
    candidate_source = None
    try:
        pinned, excluded = prepare_revision(request, previous_state, anchor)
        data, stadium = load_planning_data(anchor)
        candidate_source = data.get("candidate_source")
        sources = origin_coordinate_sources(inputs, state)
        try:
            origin = resolve_origin(request.origin, preferences, data["places"], stadium, coordinate_sources=sources)
        except ValueError:
            # Keep unique exact-name origin resolution after retiring the SBIZ
            # pool. No model geocoding or first-result guess; ambiguous names ask.
            label = request.origin or preferences.get("origin") or ""
            if not candidate_source or re.fullmatch(r"\s*" + COORDINATES + r"\s*", label) or len(label) > 100:
                raise
            origins = candidate_source.search(stadium, 2500, "origin", origin_label=label)
            origin = resolve_origin(label, {}, origins, stadium, coordinate_sources=sources)
        if state is not None:
            state["itinerary_origin"] = {k: origin[k] for k in ("lat", "lng")} if origin else None
        internal_records, gate = [], None
        if request.arrive_at_open or any(stop.phase == "inside" for stop in request.stops):
            from travel.stadium_facilities import facility_catalogue
            from .gate_times import gate_window
            internal_records = facility_catalogue(anchor["stadium_code"])["records"]
            gate = gate_window(anchor, request)
        web_enabled = getattr(settings, "COURSE_WEB_VERIFICATION_ENABLED", False) is True
        has_food = any(s.food and s.phase != "inside" for s in request.stops)
        has_reviews = any(s.reviews and s.phase != "inside" for s in request.stops)
        now = timezone.now().astimezone(KST)
        knowledge = KnowledgeVerification(request, web_enabled=web_enabled, now=now)
        knowledge.reuse_only_ids = {str(p['placeId']) for p in pinned.values()}
        planner = ItineraryPlanner(data["places"], stadium, data["coverage"], fetch_directions,
                                   internal_records=internal_records, gate=gate,
                                   food_verifier=KnowledgeFoodVerifier(knowledge) if has_food else None,
                                   review_verifier=KnowledgeReviewVerifier(knowledge) if has_reviews else None,
                                   local_evidence_only=False, candidate_source=candidate_source,
                                   pinned_stops=pinned, excluded_place_ids=excluded)
        result = planner.plan(request, datetime.fromisoformat(anchor["starts_at"]), origin=origin,
                              now=now, historical=inputs.get("course_historical") is True)
        local_status = result["status"]
        missing = bool({"food_unverified", "review_unverified"} & set(result.get("reasons", [])))
        optional_missing = result["status"] == "ok" and any(
            (s.get("review_verification") or {}).get("status") == "unknown" for s in result["stops"])
        if web_enabled and ((missing and result["status"] != "ok") or optional_missing) and result["status"] in (
                "ok", "food_unverified", "review_unverified", "search_limited"):
            knowledge.allow_web = True
            result = planner.plan(request, datetime.fromisoformat(anchor["starts_at"]), origin=origin,
                                  now=now, historical=inputs.get("course_historical") is True,
                                  reuse_routes=True)
        if not web_enabled and result["status"] in ("food_unverified", "review_unverified"):
            from .local_knowledge import missing_required_evidence
            result.update(status="knowledge_insufficient", missing_evidence=missing_required_evidence(request))
        result["snapshot_id"] = data["snapshotId"]
        result["verification_policy"] = {"automatic_web_search_enabled": web_enabled,
                                         "approved_stored_observations_connected": True,
                                         "search_provider": "serper", "local_pass_status": local_status,
                                         "web_fallback_used": knowledge.allow_web,
                                         "evidence_budget": knowledge.audit()}
        if data.get("candidate_retrieval"):
            result["candidate_retrieval"] = data["candidate_retrieval"]
    except RevisionClarification as exc:
        result = {'version': 1, 'status': 'clarification', 'stops': [], 'legs': [], 'message': str(exc)}
    except CandidateSearchError as exc:
        result = {"version": 1, "status": exc.status, "stops": [], "legs": [], "reasons": [exc.reason],
                  "candidate_retrieval": candidate_source.audit() if candidate_source else None}
    except (CatalogueUnavailable, CatalogueQueryError, OSError, KeyError):
        result = {"version": 1, "status": "data_unavailable", "stops": [], "legs": []}
    except ValueError as exc:
        # Origin errors have a deliberately public message, not an upstream error body.
        result = {"version": 1, "status": "clarification", "stops": [], "legs": [],
                  "message": str(exc) if str(exc).startswith("출발지의 정확한") else "출발·도착 위치와 날짜를 확인해 주세요."}
    except (DirectionsError, RoutingUnavailable):
        result = {"version": 1, "status": "directions_unavailable", "stops": [], "legs": []}
    if state is not None:
        state["itinerary_result"] = result
        # A clarification cannot destroy the last completed route needed by a
        # subsequent partial edit. The copy is room-local and not recursive.
        baseline = ({'selected_game': anchor, 'itinerary_request': request.model_dump(mode='json'),
                     'itinerary_result': result} if result['status'] == 'ok' else previous_state)
        if (baseline.get('itinerary_result') or {}).get('status') == 'ok':
            state['itinerary_baseline'] = deepcopy({k: baseline[k] for k in
                ('selected_game', 'itinerary_request', 'itinerary_result')})
    # Deterministic rendering prevents a second model from changing validated places/times.
    yield render_itinerary(result, request)


def display_time(value):
    return datetime.fromisoformat(value).astimezone(KST).strftime("%m/%d %H:%M")


def render_itinerary(result, request: ItineraryRequest):
    if result["status"] != "ok":
        message = result.get("message") or MESSAGES.get(result["status"], MESSAGES["extraction_failed"])
        if result.get("gate") and result["gate"].get("warning"):
            message += "\n" + result["gate"]["warning"]
        unresolved = request.unverified_requirements + [v for stop in request.stops for v in stop.unverified_requirements]
        unresolved += [label for item in result.get("missing_evidence", []) for label in item["conditions"]]
        return message + ("\n확인 필요한 조건: " + ", ".join(unresolved) if unresolved else "")
    mode = {"walk": "도보", "car": "차량", "transit": "대중교통"}[result["mode"]]
    arrival_label = "구장 입장 목표" if result.get("internal_finish_at") else "구장 도착 목표"
    lines = [f"{mode} 기준으로 전체 이동·체류·여유시간을 계산한 코스예요. 실제 도착을 보장하는 것은 아니에요.",
             f"{arrival_label}: {display_time(result['arrival_deadline'])}(한국 시간)", ""]
    if result.get('retained_place_ids'):
        lines.append('요청한 부분만 교체하고 나머지 장소는 유지했어요. 전체 이동·체류·경기 도착 시각은 다시 계산했어요.')
    for i, stop in enumerate(result["stops"], 1):
        if stop["phase"] == "game":
            lines.append(f"{i}. {display_time(stop['arrive_at'])} {stop['name']} 도착 · 경기 시작 {display_time(stop['starts_at'])}")
        else:
            default = " · 기본 체류시간 적용" if stop["stay_is_default"] else ""
            lines.append(f"{i}. {display_time(stop['arrive_at'])}~{display_time(stop['depart_at'])} "
                         f"{stop['name']} ({stop['stay_minutes']}분{default})")
            if stop["phase"] == "inside":
                lines.append(f"   구장 내부 · {stop.get('floor') or '층 미확인'} · {stop.get('zone') or '구역 미확인'}")
            food = stop.get("food_verification")
            if food and food["status"] == "pass":
                # Only selected evidence is saved/displayed; no full pages or raw tool output.
                labels = [re.sub(r"[\r\n\[\]<>]", " ", condition["label"]) for condition in food["conditions"]
                          if condition["status"] == "pass"]
                label = "저장된 근거에서 확인한 음식 조건: " if food.get("method") == "stored_evidence" else "웹 자료에서 확인한 음식 조건: "
                lines.append("   " + label + ", ".join(labels))
                links = [f"[음식 근거 {n}](<{quote(source['url'], safe=':/?&=%#@+;,$!~*-._')}>)"
                         for n, source in enumerate(food["sources"], 1) if public_url(source["url"])]
                lines.append(f"   확인: {display_time(food['checked_at'])} · " + " · ".join(links))
            reviews = stop.get("review_verification")
            if reviews:
                labels = {"positive": "긍정 근거 있음", "negative": "반대 근거 있음", "mixed": "후기 혼재", "unknown": "근거 부족/미확인"}
                for condition in reviews["conditions"]:
                    priority = "필수" if condition["priority"] == "required" else "선호"
                    lines.append(f"   후기 기준 {condition['label']}({priority}): {labels[condition['status']]} "
                                 f"· 중복 제외 긍정 {condition['positive_count']} / 반대 {condition['negative_count']}개 근거")
                    if condition["context_excluded_count"]:
                        lines.append("   다른 시간대·요일 등의 후기는 이 방문 시간의 근거에서 제외했어요.")
                used_urls = {url for condition in reviews["conditions"] for url in condition["source_urls"]}
                links = []
                for n, source in enumerate(reviews.get("sources", []), 1):
                    if public_url(source["url"]):
                        # A branch identity page is not automatically trait evidence.
                        label = "후기 근거" if source["url"] in used_urls else "지점·검토 자료"
                        links.append(f"[{label} {n}](<{quote(source['url'], safe=':/?&=%#@+;,$!~*-._')}>)")
                if links:
                    lines.append(f"   확인: {display_time(reviews['checked_at'])} · " + " · ".join(links))
                if reviews["reason"] in ("review_search_limited", "review_search_unavailable"):
                    lines.append("   후기 검색이 제한되거나 연결되지 않아 선호 충족 여부는 확인하지 못했어요.")
                if reviews.get("method") == "local_knowledge_unverified":
                    searched = ((result.get('verification_policy') or {}).get('evidence_budget') or {}).get('searches', 0)
                    lines.append("   저장된 후기 근거가 없어 이 선호는 미확인이에요." if searched else
                                 "   저장된 후기 근거가 없어 이 선호는 미확인이며, 별도 웹검색은 하지 않았어요.")
    lines += ["", f"이동 합계: 약 {math_ceil_minutes(result['total_travel_seconds'])}분 / {result['total_distance_m']:,}m. "
              f"각 이동 구간에 여유 {result['leg_buffer_minutes']}분을 추가했어요."]
    if request.mode is None:
        lines.append("이동수단을 지정하지 않아 도보로 계산했어요.")
    if not result.get("origin_included"):
        lines.append("출발지가 없어 첫 장소부터 시작하며, 출발지→첫 장소 이동은 포함하지 않아요.")
    if result.get("suggested_start"):
        lines.append(f"출발시각이 없어 {display_time(result['start_at'])} 시작 일정으로 역산했어요.")
    if result.get("timing_status") == "conditional_game_end":
        lines.append(f"경기 후 출발은 {display_time(result['after_start_at'])}로 가정한 조건부 일정이에요. "
                     "별도 지정이 없으면 경기 시작 3시간 후로 가정하며, 실제 종료 후 다시 계산해야 해요.")
    if result.get("internal_finish_at"):
        gate = result["gate"]
        evidence = {"official_claim_in_user_table": "첨부표상 공식 확인", "blog_reference": "블로그 참고값",
                    "estimated_lg_rule_for_doosan": "두산은 LG와 같다고 추정한 값", "user_supplied": "사용자 지정"}.get(gate["evidence"], "미확인")
        lines.append(f"구장 개방 기준: {display_time(gate['opens_at'])} · {gate['source']} ({evidence}). 실제 경기별 공지는 미확인이에요.")
        lines.append(f"내부 코스 종료 기한은 경기 시작 {display_time(result['pre_game_deadline'])}이며, 마지막 내부 장소에서 구장까지의 이동을 중복 계산하지 않았어요.")
        lines.append(f"실내 길찾기는 미연결이므로 각 내부 방문 전 이동·대기 {result['internal_transfer_minutes']}분을 가정했어요. 실제 동선·대기에 따라 달라지는 조건부 시간표예요.")
        if result.get("default_internal"):
            lines.append("오픈 맞춤 요청에 내부 종류가 없어 내부 먹거리 1곳을 기본 제안했어요.")
    if result.get("historical"):
        lines.append("과거 코스의 수정안이며, 과거 교통상황이나 현재 방문 가능성을 검증한 결과가 아니에요.")
    if "coverage_incomplete" in result.get("reasons", []):
        lines.append("일부 검색 영역은 미수집이며, 수집본 안에서 확인된 후보로 구성했어요.")
    if request.soft_notes:
        lines.append("아직 확인하지 못한 선호: " + ", ".join(request.soft_notes))
    if any((stop.get("review_verification") or {}).get("method") != "local_knowledge_unverified"
           and stop.get("review_verification") for stop in result["stops"]):
        lines.append("후기 근거는 최근 180일·지점·방문 시간·중복·광고 표시를 기준으로 평가해요. 근거 부족 항목은 미확인이며, 전체 후기의 대표성이나 실제 소음·청결·위생 안전을 보장하지 않아요.")
        if any(s.reviews and s.reviews.has_preferences for s in request.stops):
            lines.append("후기 선호는 같은 반경의 소수 후보 비교에 반영했으며, 모든 장소를 비교한 최적 코스는 아니에요. 시간대 제한 후기는 최종 방문 시간에서만 평가했어요.")
    if any(stop.get("food_verification") for stop in result["stops"]):
        lines.append("음식 조건은 링크된 웹 자료를 모델이 해석한 결과이며, 방문일 판매·품절·재료 안전을 보장하지 않아요. 그 외 메뉴·영업시간·대기·가격·예약은 미확인이에요.")
    else:
        lines.append("현재 영업시간·메뉴·대기·예약 가능 여부는 미확인이에요.")
    lines.append("구장 도착점은 입장 게이트가 아닌 검토된 구장 기준점이에요.")
    return "\n".join(lines)


def math_ceil_minutes(seconds):
    return (seconds + 59) // 60
