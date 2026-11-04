"""Shared helpers.

The concurrency tests here run real threads against a real Postgres rather than
mocking the database. Locking is the thing under test, and a mock cannot be
wrong in the way a database can, so a mocked version of these tests would pass
whether or not the code was correct.
"""

from __future__ import annotations

import threading

import pytest
from django.db import connection

from reviews import services


def run_concurrently(fn, n: int) -> list:
    """Run ``fn(i)`` on ``n`` threads released at the same instant.

    The barrier matters. Without it the threads start far enough apart that the
    first one finishes before the second begins, and the test passes against
    code that has no locking at all.
    """
    results: list = []
    lock = threading.Lock()
    barrier = threading.Barrier(n)

    def wrapped(i: int) -> None:
        barrier.wait()
        try:
            outcome = fn(i)
        except Exception as exc:  # recorded, then asserted on by the caller
            outcome = exc
        finally:
            # Each thread opened its own connection; leaving them open keeps the
            # test database busy and makes teardown hang.
            connection.close()
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=wrapped, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert all(not t.is_alive() for t in threads), "a worker thread deadlocked"
    return results


@pytest.fixture
def submit_items():
    def _submit(count: int, prefix: str = "item"):
        return [
            services.submit(
                idempotency_key=f"{prefix}-{i}",
                source_ref=f"{prefix}/{i}",
                payload={"text": f"body {i}"},
            )[0]
            for i in range(count)
        ]

    return _submit
