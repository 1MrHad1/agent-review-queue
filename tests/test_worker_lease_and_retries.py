"""A worker that dies must not strand its item, and an error must not vanish."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from reviews import services
from reviews.models import State


@pytest.mark.django_db
def test_a_live_lease_blocks_other_workers(submit_items):
    submit_items(1)
    services.claim_for_processing(worker_id="worker-a", lease_seconds=300)

    assert services.claim_for_processing(worker_id="worker-b") is None


@pytest.mark.django_db
def test_a_lapsed_lease_is_reclaimed_rather_than_stuck(submit_items):
    submit_items(1)
    item = services.claim_for_processing(worker_id="worker-a", lease_seconds=300)

    # worker-a dies here, so nothing ever completes the item.
    item.lease_expires_at = timezone.now() - timedelta(seconds=1)
    item.save(update_fields=["lease_expires_at"])

    reclaimed = services.claim_for_processing(worker_id="worker-b")

    assert reclaimed is not None
    assert reclaimed.id == item.id
    assert reclaimed.lease_owner == "worker-b"
    assert reclaimed.attempts == 2
    assert reclaimed.events.order_by("-seq").first().detail["reclaimed_from"] == "worker-a"


@pytest.mark.django_db
def test_a_stale_worker_cannot_overwrite_the_new_holder(submit_items):
    submit_items(1)
    item = services.claim_for_processing(worker_id="worker-a", lease_seconds=300)
    item.lease_expires_at = timezone.now() - timedelta(seconds=1)
    item.save(update_fields=["lease_expires_at"])
    services.claim_for_processing(worker_id="worker-b")

    # worker-a wakes up and tries to finish work that is no longer its own.
    with pytest.raises(services.TransitionError):
        services.complete_processing(
            item_id=item.id, worker_id="worker-a", ai_output={"summary": "stale"}
        )


@pytest.mark.django_db
def test_a_failed_attempt_returns_the_item_to_the_queue(submit_items):
    submit_items(1)

    def boom(_payload):
        raise RuntimeError("model timed out")

    item = services.process_once(worker_id="w", ai_stage=boom)

    assert item.state == State.QUEUED
    assert item.attempts == 1
    assert item.last_error == "model timed out"


@pytest.mark.django_db
def test_attempts_run_out_into_a_dead_letter_not_a_silence(submit_items):
    submit_items(1)

    def boom(_payload):
        raise RuntimeError("model timed out")

    for _ in range(3):
        item = services.process_once(worker_id="w", ai_stage=boom, max_attempts=3)

    assert item.state == State.FAILED
    assert item.attempts == 3
    assert item.last_error == "model timed out"
    last = item.events.order_by("-seq").first()
    assert last.detail["exhausted"] is True
    assert last.detail["error"] == "model timed out"


@pytest.mark.django_db
def test_a_malformed_model_response_is_a_failure_not_a_publish(submit_items):
    submit_items(1)

    item = services.process_once(worker_id="w", ai_stage=lambda p: "just a string")

    # The point: output that is not the agreed shape never reaches a reviewer.
    assert item.state == State.QUEUED
    assert "unusable shape" in item.last_error


@pytest.mark.django_db
def test_the_happy_path_hands_a_result_to_a_human(submit_items):
    submit_items(1)

    item = services.process_once(worker_id="w")

    assert item.state == State.AWAITING_REVIEW
    assert item.ai_output["summary"] == "body 0"
    assert item.review_deadline is not None
    assert item.lease_owner is None


# --- reviewer leases -------------------------------------------------------
# These exist because of a bug found by running the UI rather than the tests.
# React StrictMode double-invoked the mount effect, so the screen claimed two
# items and rendered one. The discarded one kept its reviewer forever and
# dropped out of every queue, with its review deadline still counting down and
# nothing anywhere reporting it unreachable.

@pytest.mark.django_db
def test_a_claimed_item_is_hidden_from_other_reviewers(submit_items):
    submit_items(1)
    services.process_once(worker_id="w")

    assert services.claim_for_review(reviewer="dr-smith") is not None
    assert services.claim_for_review(reviewer="dr-jones") is None


@pytest.mark.django_db
def test_an_abandoned_claim_returns_to_the_pool(submit_items):
    submit_items(1)
    services.process_once(worker_id="w")
    claimed = services.claim_for_review(reviewer="dr-smith", lease_seconds=900)

    # dr-smith closes the tab. Nothing tells the server.
    claimed.review_lease_expires_at = timezone.now() - timedelta(seconds=1)
    claimed.save(update_fields=["review_lease_expires_at"])

    recovered = services.claim_for_review(reviewer="dr-jones")

    assert recovered is not None, "the item was stranded by an abandoned claim"
    assert recovered.id == claimed.id
    assert recovered.reviewer == "dr-jones"


@pytest.mark.django_db
def test_deciding_clears_the_lease(submit_items):
    submit_items(1)
    services.process_once(worker_id="w")
    item = services.claim_for_review(reviewer="dr-smith")

    decided = services.decide(item_id=item.id, reviewer="dr-smith", approve=True)

    assert decided.review_lease_expires_at is None
