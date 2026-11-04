"""The record is the product. If the application can revise it, it is not a record."""

from __future__ import annotations

import pytest

from reviews import services
from reviews.models import State


@pytest.mark.django_db
def test_an_audit_row_cannot_be_edited():
    item, _ = services.submit(idempotency_key="k", source_ref="r", payload={})
    event = item.events.first()

    event.actor = "someone else"
    with pytest.raises(ValueError, match="append-only"):
        event.save()


@pytest.mark.django_db
def test_an_audit_row_cannot_be_deleted():
    item, _ = services.submit(idempotency_key="k", source_ref="r", payload={})
    event = item.events.first()

    with pytest.raises(ValueError, match="append-only"):
        event.delete()


@pytest.mark.django_db
def test_the_trail_is_a_gapless_sequence_through_the_whole_life(submit_items):
    submit_items(1)
    item = services.process_once(worker_id="w")
    services.decide(item_id=item.id, reviewer="dr-smith", approve=True)

    trail = list(item.events.values_list("seq", "from_state", "to_state"))
    assert trail == [
        (1, "", State.QUEUED),
        (2, State.QUEUED, State.PROCESSING),
        (3, State.PROCESSING, State.AWAITING_REVIEW),
        (4, State.AWAITING_REVIEW, State.APPROVED),
    ]
