"""Warm public lodging source pages once or as a bounded background worker."""
import json
import os
import signal
from threading import Event

from django.core.management.base import BaseCommand, CommandError
from django.db import connections

from llm.v1.rag.nearby.kakao import STADIUM_QUERY
from llm.service.chat_runs import _cancelled, Stopped
from travel.lodging_warmer import warm


class Command(BaseCommand):
    help = "미리 검증한 숙소 공개 원문을 준비합니다. --loop는 독립 worker에서 사용합니다."

    def add_arguments(self, parser):
        parser.add_argument("--stadium", action="append", choices=sorted(STADIUM_QUERY))
        parser.add_argument("--limit", type=int, default=6)
        parser.add_argument("--per-stadium", type=int, default=2)
        parser.add_argument("--max-seconds", type=int, default=180)
        parser.add_argument("--refresh-before", type=int, default=1800)
        parser.add_argument("--loop", action="store_true")
        parser.add_argument("--interval", type=int, default=900)

    def handle(self, *args, **options):
        configured = [v.strip() for v in os.getenv("LODGING_WARM_STADIUMS", "").split(",") if v.strip()]
        stadiums = options["stadium"] or configured or list(STADIUM_QUERY)
        if any(code not in STADIUM_QUERY for code in stadiums):
            raise CommandError("Unknown LODGING_WARM_STADIUMS stadium")
        for key, lower, upper in (("limit", 1, 24), ("per_stadium", 1, 5), ("max_seconds", 1, 600),
                                  ("refresh_before", 0, 21600), ("interval", 60, 21600)):
            if not lower <= options[key] <= upper:
                raise CommandError(f"{key} must be {lower}..{upper}")
        stop = Event()
        handlers = {}
        if options["loop"]:
            for sig in (signal.SIGINT, signal.SIGTERM):
                handlers[sig] = signal.signal(sig, lambda *_: stop.set())
        token = _cancelled.set(stop.is_set)
        try:
            while not stop.is_set():
                try:
                    result = warm(stadiums, **{k: options[k] for k in ("limit", "max_seconds", "refresh_before", "per_stadium")})
                except Stopped:
                    if stop.is_set():
                        break
                    raise
                except Exception as exc:
                    if not options["loop"]:
                        raise CommandError(type(exc).__name__) from None
                    result = {"status": "error", "error_type": type(exc).__name__}
                finally:
                    connections.close_all()
                self.stdout.write(json.dumps(result, ensure_ascii=False))
                self.stdout.flush()
                if not options["loop"] or result["status"] == "disabled":
                    break
                stop.wait(options["interval"])
        finally:
            _cancelled.reset(token)
            for sig, handler in handlers.items():
                signal.signal(sig, handler)
