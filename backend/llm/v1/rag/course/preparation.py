"""Read-only new-course inputs, independent of the interpretation model."""
import logging
from datetime import datetime
from time import monotonic
from zoneinfo import ZoneInfo

from llm.service.chat_runs import Stopped, check_cancelled
from llm.v1.progress import ProgressCancelled, ProgressStorageError

log = logging.getLogger(__name__)


def read(question, code, *, origin=None):
    from . import agent, slots, venue_policy
    if code not in agent.STADIUM_KO or agent.PUBLIC_COURSE_LOOKUP.search(question):
        return None
    started = monotonic()
    now = datetime.now(ZoneInfo("Asia/Seoul"))
    today = now.date().isoformat()
    result = {"question": question, "code": code, "day": today}
    try:
        check_cancelled()
        requested = agent.requested_date(question, [], today, code)
        check_cancelled()
        schedule, stadium_id = agent.load_schedule(code, requested, now)
        check_cancelled()
        result.update(requested=requested, schedule=schedule, stadium_id=stadium_id)
        game, _, _ = agent.find_game(code, requested or "", today, schedule, now=now, stadium_id=stadium_id)
        # Don't speculate on place lookups when no usable game was found.
        if game and not schedule.get("lookup_failed"):
            check_cancelled()
            result["anchor"] = agent.stadium_anchor(code)
            result["extras"] = {}
            for kind in slots.parse(question, []).get("extras") or []:
                check_cancelled()
                result["extras"][kind] = agent.kakao.nearby(code, kind)
            sl = slots.parse(question, [])
            before, after = agent.plan_steps(sl, agent.is_evening(game))
            kinds = set(before + after)
            anchor = result["anchor"]
            jobs = []
            # Only raw map discovery overlaps interpretation. Menu/quality
            # decisions stay on the requesting thread after its final policy.
            for kinds_, category in (({"FOOD", "BAR"}, "FD6"), ({"CAFE"}, "CE7")):
                if kinds & kinds_ and not venue_policy.mentions_internal(question):
                    jobs.append(({"method": "category", "category": category, "latitude": anchor["lat"],
                                  "longitude": anchor["lng"], "radius": agent.MAX_DISTANCE_M, "limit": 15}, category))
            point = agent.valid_origin(origin)
            if point and agent.geo._dist(point, anchor) <= agent.MAX_DISTANCE_M and before:
                kind = before[0]
                # Internal-only requests must not trigger Kakao tenant discovery.
                if kind in ("FOOD", "CAFE", "BAR") and not venue_policy.mentions_internal(question):
                    query = agent._step_keyword(kind, sl)
                    args = {"method": "keyword" if query else "category", "latitude": point["lat"],
                            "longitude": point["lng"], "radius": agent.STEP_RADII_M[0], "limit": 15,
                            "sort": "distance", "category": agent.STEP_KAKAO[kind]}
                    if query:
                        args["query"] = query
                    jobs.insert(0, (args, agent.STEP_CATEGORY[kind]))
            result["discovery"] = {}
            with venue_policy.request_policy("", code):
                for args, category in jobs[:3]:
                    check_cancelled()
                    if monotonic() - started >= 5:
                        break  # Speculation never starts an unbounded lookup chain.
                    details = {}
                    found = venue_policy.discover_candidates(agent.invoke_domain_tool, args, category, diagnostics=details)
                    # Failed/partial provider responses remain retryable in the main path.
                    if not details.get("warning") and not details.get("error"):
                        result["discovery"][venue_policy.discovery_key(args, category)] = {"places": found, "diagnostics": details}
    except (Stopped, ProgressCancelled, ProgressStorageError):
        raise
    except Exception as exc:
        # A speculative read must not turn a previously answerable request into
        # an error. The caller performs its usual lookup for any missing input.
        log.info("course preparation incomplete: %s", type(exc).__name__)
    result["elapsed_ms"] = round((monotonic() - started) * 1000)
    return result


def matches(prepared, question, code, today):
    return bool(prepared and prepared.get("question") == question
                and prepared.get("code") == code and prepared.get("day") == today)
