"""Bounded provider I/O. Merge and verify results on the requesting thread."""
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context

from django.db import connections

from ...progress import ProgressCancelled, current

WORKERS = 2


def _call(job):
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
