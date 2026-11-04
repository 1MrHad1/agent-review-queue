"""Triage runs before any model is called, so it has to be cheap and repeatable."""

from __future__ import annotations

import pytest

from reviews import services
from reviews.models import RiskTier


@pytest.mark.parametrize(
    "payload,expected",
    [
        ({"text": "routine batch note"}, RiskTier.ROUTINE),
        ({"text": "DOSE adjustment required"}, RiskTier.CRITICAL),
        ({"text": "serious adverse event"}, RiskTier.CRITICAL),
        ({"text": "supplier note", "external": True}, RiskTier.ELEVATED),
        ({}, RiskTier.ROUTINE),
    ],
)
def test_triage_is_deterministic(payload, expected):
    assert services.classify_risk(payload) == expected
    # Same input, same tier, every time. The audit trail depends on this being
    # reproducible; a tier that drifted between runs could not be explained.
    assert services.classify_risk(payload) == services.classify_risk(payload)


@pytest.mark.django_db
def test_submitted_work_always_carries_a_tier():
    item, _ = services.submit(
        idempotency_key="k", source_ref="r", payload={"text": "dose check"}
    )
    assert item.risk_tier == RiskTier.CRITICAL


@pytest.mark.django_db
def test_the_database_refuses_an_item_with_no_tier():
    from django.db import IntegrityError, transaction

    from reviews.models import ReviewItem

    # The CHECK constraint validated in migration 0005 is the real guard; the
    # application setting the field is merely the usual path to satisfying it.
    with pytest.raises(IntegrityError), transaction.atomic():
        ReviewItem.objects.create(idempotency_key="x", source_ref="r", risk_tier=None)


@pytest.mark.django_db
def test_critical_work_is_handed_out_before_routine_work():
    from datetime import timedelta

    from django.utils import timezone

    from reviews.models import State

    # The routine item is deliberately the more urgent one by deadline, so this
    # fails if the queue orders on the clock alone.
    for key, text in [("routine", "batch note"), ("critical", "dose adjustment")]:
        services.submit(idempotency_key=key, source_ref=key, payload={"text": text})
        item = services.process_once(worker_id="w")
        assert item.state == State.AWAITING_REVIEW
        if key == "routine":
            item.review_deadline = timezone.now() + timedelta(minutes=5)
            item.save(update_fields=["review_deadline"])

    first = services.claim_for_review(reviewer="dr-smith")

    assert first.risk_tier == RiskTier.CRITICAL, "a routine item jumped the critical one"
