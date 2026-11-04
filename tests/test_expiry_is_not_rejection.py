"""The rule this whole repository is built around.

A review nobody answered and a review someone rejected are different facts.
A system that reports them identically will tell its operators it destroyed
work that it actually published, and they will believe it, because why would
the machine lie about its own behaviour.

This is not hypothetical. I shipped the version that got it wrong: an approval
step with a two-day timeout whose false branch was wired to the rejection
handler. The team had stopped tapping Approve and published directly in the CMS
instead, so every order timed out, and every order was reported as rejected
while going live exactly as intended. These tests are that incident, frozen.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from reviews import services
from reviews.models import State


@pytest.fixture
def awaiting(db):
    def _awaiting(*, deadline_offset_seconds: int, key: str = "k"):
        item, _ = services.submit(idempotency_key=key, source_ref="trial/1", payload={"text": "x"})
        item = services.claim_for_processing(worker_id="w")
        item = services.complete_processing(
            item_id=item.id, worker_id="w", ai_output={"summary": "s"}
        )
        item.review_deadline = timezone.now() + timedelta(seconds=deadline_offset_seconds)
        item.save(update_fields=["review_deadline"])
        return item

    return _awaiting


@pytest.mark.django_db
def test_timeout_produces_expired_not_rejected(awaiting):
    item = awaiting(deadline_offset_seconds=-60)

    assert services.expire_overdue() == 1

    item.refresh_from_db()
    assert item.state == State.EXPIRED
    assert item.state != State.REJECTED


@pytest.mark.django_db
def test_the_audit_trail_says_in_words_that_it_was_not_a_rejection(awaiting):
    item = awaiting(deadline_offset_seconds=-60)
    services.expire_overdue()

    last = item.events.order_by("-seq").first()
    assert last.to_state == State.EXPIRED
    assert last.actor == "system"
    assert last.detail["not_a_rejection"] is True
    assert "no decision" in last.detail["reason"]


@pytest.mark.django_db
def test_an_item_inside_its_window_is_left_alone(awaiting):
    item = awaiting(deadline_offset_seconds=3600)

    assert services.expire_overdue() == 0

    item.refresh_from_db()
    assert item.state == State.AWAITING_REVIEW


@pytest.mark.django_db
def test_a_decision_arriving_after_expiry_is_refused_and_says_why(awaiting):
    item = awaiting(deadline_offset_seconds=-60)
    services.expire_overdue()

    with pytest.raises(services.TransitionError) as exc:
        services.decide(item_id=item.id, reviewer="dr-smith", approve=True)

    assert State.EXPIRED in str(exc.value)
    item.refresh_from_db()
    # The late approval must not have silently landed.
    assert item.state == State.EXPIRED
    assert item.decided_at is None


@pytest.mark.django_db
def test_a_real_rejection_is_recorded_as_one(awaiting):
    item = awaiting(deadline_offset_seconds=3600)

    services.decide(item_id=item.id, reviewer="dr-smith", approve=False, note="dose wrong")

    item.refresh_from_db()
    assert item.state == State.REJECTED
    assert item.reviewer == "dr-smith"
    assert item.decided_at is not None
    assert item.events.order_by("-seq").first().detail["note"] == "dose wrong"
