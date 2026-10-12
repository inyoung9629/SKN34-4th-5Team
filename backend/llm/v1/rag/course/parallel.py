"""Bounded provider I/O. Merge and verify results on the requesting thread."""
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from contextvars import copy_context
from threading import Event
from time import monotonic

from django.db import connections

from llm.service import chat_runs

from ...progress import ProgressCancelled, current

WORKERS = 2


def _call(job):
    chat_runs.check_cancelled()
    collector = current()
    if collector is not None and collector.cancelled:
        raise ProgressCancelled("chat progress cancelled")
    return job()


def _run(context, job):
    try:
        return context.run(_call, job)
    finally:
        # Provider tools can synchronize their results to Django. These worker
        # connections have no request_finished hook and must not leak.
        connections.close_all()


def reads(jobs):
    """Keep input order, propagate failures, and never leave work after return.

    Context copies retain cancellation/tracing and the current stadium policy.
    Jobs must not mutate request-local research budgets or perform model checks.
    """
    jobs = list(jobs)
    if len(jobs) < 2:
        return [_call(job) for job in jobs]
    with ThreadPoolExecutor(max_workers=min(WORKERS, len(jobs)), thread_name_prefix="course-io") as pool:
        pending = [pool.submit(_run, copy_context(), job) for job in jobs]
        try:
            return [future.result() for future in pending]
        finally:
            for future in pending:
                future.cancel()


def alongside(read, action):
    """Overlap one read with main-thread work; join before leaving the request.

    Model interpretation and request-local mutations stay on the caller. The
    reader inherits cancellation/tracing and closes its own DB connections.
    """
    stop = Event()
    cancelled = chat_runs._cancelled.get()
    collector = current()
    context = copy_context()
    context.run(chat_runs._cancelled.set, lambda: stop.is_set() or cancelled()
                or (collector is not None and collector.cancelled))
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="course-prepare") as pool:
        future = pool.submit(_run, context, read)
        try:
            result = action()
            started = monotonic()
            while True:
                _call(lambda: None)
                try:
                    prepared = future.result(timeout=.1)
                    break
                except TimeoutError:
                    if future.done():
                        prepared = future.result()
                        break
            return result, prepared, round((monotonic() - started) * 1000)
        finally:
            # Join only the current bounded I/O, not the remaining speculative
            # queries/pages. _run closes worker DB connections before return.
            stop.set()
            future.cancel()
