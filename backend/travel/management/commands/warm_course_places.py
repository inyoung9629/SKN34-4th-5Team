"""Prepare branch quality and permitted menu observations outside chat requests."""
import json
import signal
from threading import Event
from django.core.management.base import BaseCommand, CommandError
from django.db import connections
from llm.service.chat_runs import _cancelled, Stopped
from travel.course_place_warmer import warm


class Command(BaseCommand):
    help = "광주·잠실 식당/카페/술집 근거를 미리 갱신합니다. --loop는 별도 워커에서 실행합니다."

    def add_arguments(self, parser):
        parser.add_argument("--stadium", action="append", choices=["GWANGJU", "JAMSIL"])
        for key, default in (("limit", 6), ("max-seconds", 120), ("seed-count", 2), ("refresh-days", 3), ("interval", 900)):
            parser.add_argument("--" + key, type=int, default=default)
        parser.add_argument("--loop", action="store_true")

    def handle(self, *args, **options):
        for key, low, high in (("limit", 1, 48), ("max_seconds", 1, 600), ("seed_count", 0, 20),
                              ("refresh_days", 1, 7), ("interval", 60, 86400)):
            if not low <= options[key] <= high:
                raise CommandError(f"{key} must be {low}..{high}")
        stop, handlers = Event(), {}
        if options["loop"]:
            for sig in (signal.SIGINT, signal.SIGTERM):
                handlers[sig] = signal.signal(sig, lambda *_: stop.set())
        token = _cancelled.set(stop.is_set)
        try:
            while not stop.is_set():
                try:
                    result = warm(options["stadium"] or ["GWANGJU", "JAMSIL"],
                        **{k: options[k] for k in ("limit", "max_seconds", "seed_count", "refresh_days")})
                except Stopped:
                    break
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
                stop.wait(10 if result["status"] == "deferred" else options["interval"])
        finally:
            _cancelled.reset(token)
            for sig, handler in handlers.items():
                signal.signal(sig, handler)
