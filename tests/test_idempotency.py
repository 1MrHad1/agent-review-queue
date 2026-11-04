"""A retried submission must not become a second piece of work."""

from __future__ import annotations

import pytest

from reviews import services
from reviews.models import AuditEvent, ReviewItem, State
from tests.conftest import run_concurrently


@pytest.mark.django_db(transaction=True)
def test_repeat_submit_returns_the_original():
    first, created_first = services.submit(
        idempotency_key="abc", source_ref="trial/1", payload={"text": "x"}
    )
    second, created_second = services.submit(
        idempotency_key="abc", source_ref="trial/1", payload={"text": "x"}
    )

    assert created_first is True
    assert created_second is False
    assert first.id == second.id
    assert ReviewItem.objects.count() == 1


@pytest.mark.django_db(transaction=True)
def test_concurrent_identical_submits_create_one_row():
    results = run_concurrently(
        lambda i: services.submit(
            idempotency_key="same-key", source_ref="trial/1", payload={"i": i}
        ),
        n=8,
    )

    assert not any(isinstance(r, Exception) for r in results), results
    assert ReviewItem.objects.count() == 1, "the unique constraint did not hold"
    assert sum(1 for _, created in results if created) == 1, "two callers both think they created it"
    # One row means one intake event. A duplicate here would corrupt the trail.
    assert AuditEvent.objects.filter(to_state=State.QUEUED).count() == 1


@pytest.mark.django_db(transaction=True)
def test_different_keys_are_different_work():
    services.submit(idempotency_key="a", source_ref="r", payload={})
    services.submit(idempotency_key="b", source_ref="r", payload={})
    assert ReviewItem.objects.count() == 2
