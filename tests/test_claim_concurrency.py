"""Two workers must never get the same row."""

from __future__ import annotations

import pytest

from reviews import services
from reviews.models import State
from tests.conftest import run_concurrently


@pytest.mark.django_db(transaction=True)
def test_one_item_many_workers_exactly_one_wins(submit_items):
    submit_items(1)

    results = run_concurrently(
        lambda i: services.claim_for_processing(worker_id=f"worker-{i}"), n=8
    )

    claimed = [r for r in results if r is not None]
    assert not any(isinstance(r, Exception) for r in results), results
    assert len(claimed) == 1, f"expected one winner, got {len(claimed)}"


@pytest.mark.django_db(transaction=True)
def test_many_items_many_workers_no_item_claimed_twice(submit_items):
    submit_items(10)

    results = run_concurrently(
        lambda i: services.claim_for_processing(worker_id=f"worker-{i}"), n=10
    )

    assert not any(isinstance(r, Exception) for r in results), results
    ids = [r.id for r in results if r is not None]
    assert len(ids) == 10, "every queued item should have been picked up"
    assert len(set(ids)) == len(ids), "an item was handed to two workers"


@pytest.mark.django_db(transaction=True)
def test_claim_marks_processing_and_logs_it(submit_items):
    submit_items(1)
    item = services.claim_for_processing(worker_id="worker-a")

    assert item.state == State.PROCESSING
    assert item.lease_owner == "worker-a"
    assert item.lease_expires_at is not None
    assert item.attempts == 1

    seq = list(item.events.values_list("seq", "from_state", "to_state"))
    assert seq == [(1, "", State.QUEUED), (2, State.QUEUED, State.PROCESSING)]


@pytest.mark.django_db(transaction=True)
def test_two_reviewers_never_claim_the_same_item(submit_items):
    submit_items(1)
    item = services.claim_for_processing(worker_id="w")
    services.complete_processing(item_id=item.id, worker_id="w", ai_output={"summary": "s"})

    results = run_concurrently(lambda i: services.claim_for_review(reviewer=f"dr-{i}"), n=6)

    claimed = [r for r in results if r is not None]
    assert not any(isinstance(r, Exception) for r in results), results
    assert len(claimed) == 1
