"""Explicit stadium arrival requests override the default pre-game schedule."""
import re

from . import feasibility, timeline

_ENTRY = re.compile(r"(?:(?:야구장|경기장|구장)(?:에는|에|엔|까지)?\s*(?:" + feasibility._CLOCK.pattern
                    + r"\s*(?:에|까지|쯤)?\s*)?도착|입장)")
_RELATIVE = re.compile(r"(?:(?P<hours>\d{1,2}|한|두|세)\s*시간\s*(?P<half>반)?)?\s*(?:(?P<minutes>\d{1,3})\s*분)?\s*전")
_HOURS = {"한": 1, "두": 2, "세": 3}


def requested_entry(question, game_time, history=None):
    """Use only user-authored arrival instructions; never assistant timetables."""
    texts = [question, *(m.get("content", "") for m in reversed(history or []) if m.get("role") == "user")]
    for text in texts:
        for clause in reversed(re.split(r"[.!?\n,]", text or "")):
            if not _ENTRY.search(clause):
                continue
            if re.search(r"(?:입장|구장\s*도착).*(?:기본|원래대로)|(?:기본|원래대로).*입장", clause):
                return None
            for match in reversed(list(_RELATIVE.finditer(clause))):
                if not match["hours"] and not match["minutes"]:
                    continue
                prefix = clause[:match.start()].strip()
                suffix = clause[match.end():]
                if re.search(r"말고|아니", suffix) or not (not prefix or "경기" in prefix or _ENTRY.search(prefix)):
                    continue
                hours = _HOURS.get(match["hours"], int(match["hours"]) if (match["hours"] or "").isdecimal() else 0)
                lead = hours * 60 + (30 if match["half"] else 0) + int(match["minutes"] or 0)
                if 0 <= lead <= 720:
                    return timeline.to_min(game_time) - lead
            for match in reversed(list(feasibility._CLOCK.finditer(clause))):
                prefix, suffix = clause[:match.start()], clause[match.end():]
                # '18:30 경기 시작' is not an arrival request, even in a sentence
                # that separately asks about entering the stadium.
                if re.match(r"\s*(?:에\s*)?경기\s*시작", suffix) or re.search(r"경기(?:\s*시작)?\s*$", prefix):
                    continue
                if not (_ENTRY.search(prefix) or re.match(r"\s*(?:에|까지|에는|쯤)?\s*(?:(?:야구장|경기장|구장)(?:에|까지|에는)?\s*)?(?:도착|입장)", suffix)):
                    continue
                value = feasibility.start_minute(match[0] + "에 도착")
                if value is not None:
                    return value
    return None
