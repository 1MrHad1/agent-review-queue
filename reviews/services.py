"""
Every state change in the pipeline goes through this module.

The rules it enforces, in order of how much trouble they save:

* A transition and its audit row are written in one transaction, so the log can
  never disagree with the state.
* Rows are selected with ``select_for_update(skip_locked=True)``. Two workers,
  or two reviewers, racing for the same queue take different rows instead of
  blocking on each other or, worse, both taking the same one.
* A lease has an expiry. A worker that dies mid-item does not strand that item;
  the next sweep reclaims it. "Stuck in PROCESSING forever" is a failure mode you
  design out, not one you monitor for.
* Running out of attempts is FAILED, with the error kept. Nothing is dropped
  silently, because an error you cannot see is worse than a crash.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Callable

from django.db import transaction
from django.db.models import Case, IntegerField, Q, Value, When
from django.utils import timezone

from .models import AuditEvent, ReviewItem, RiskTier, State

DEFAULT_LEASE_SECONDS = 60
DEFAULT_REVIEW_SLA_SECONDS = 48 * 3600
# How long a reviewer keeps an item before it returns to the pool untouched.
DEFAULT_REVIEW_LEASE_SECONDS = 15 * 60
DEFAULT_MAX_ATTEMPTS = 3


class TransitionError(Exception):
    """Raised when a caller asks for a move the item's current state forbids."""


# --------------------------------------------------------------------------
# Intake
# --------------------------------------------------------------------------

def classify_risk(payload: dict) -> str:
    """Cheap, deterministic triage before any model is called.

    Routing by tier is how the expensive model stays off work that does not
    need it. Keeping it deterministic means the same input always lands in the
    same queue, which the audit trail depends on.
    """
    text = str(payload.get("text", "")).lower()
    if any(word in text for word in ("dose", "adverse", "serious")):
        return RiskTier.CRITICAL
    if payload.get("external"):
        return RiskTier.ELEVATED
    return RiskTier.ROUTINE


def submit(*, idempotency_key: str, source_ref: str, payload: dict) -> tuple[ReviewItem, bool]:
    """Accept work, exactly once per idempotency key.

    The uniqueness is a database constraint rather than a read-then-write check,
    because two retries arriving together would both pass the check and both
    insert. ``get_or_create`` leans on that constraint and re-reads on conflict.
    """
    with transaction.atomic():
        item, created = ReviewItem.objects.get_or_create(
            idempotency_key=idempotency_key,
            defaults={
                "source_ref": source_ref,
                "payload": payload,
                "risk_tier": classify_risk(payload),
            },
        )
        if created:
            AuditEvent.objects.create(
                item=item, seq=1, from_state="", to_state=State.QUEUED,
                actor="api", detail={"source_ref": source_ref},
            )
    return item, created


# --------------------------------------------------------------------------
# Worker side
# --------------------------------------------------------------------------

def claim_for_processing(*, worker_id: str, lease_seconds: int = DEFAULT_LEASE_SECONDS) -> ReviewItem | None:
    """Take the oldest item that is queued, or whose previous lease has lapsed.

    ``skip_locked`` is what makes this safe to run from many workers at once:
    a row another worker is already holding is passed over rather than waited on.
    """
    now = timezone.now()
    with transaction.atomic():
        item = (
            ReviewItem.objects.select_for_update(skip_locked=True)
            .filter(
                Q(state=State.QUEUED)
                | Q(state=State.PROCESSING, lease_expires_at__lt=now)
            )
            .order_by("created_at")
            .first()
        )
        if item is None:
            return None

        reclaimed = item.state == State.PROCESSING
        item.record(
            to_state=State.PROCESSING,
            actor=worker_id,
            detail={"reclaimed_from": item.lease_owner} if reclaimed else {},
        )
        item.state = State.PROCESSING
        item.lease_owner = worker_id
        item.lease_expires_at = now + timedelta(seconds=lease_seconds)
        item.attempts += 1
        item.save(update_fields=["state", "lease_owner", "lease_expires_at", "attempts", "updated_at"])
        return item


def complete_processing(*, item_id, worker_id: str, ai_output: dict,
                        review_sla_seconds: int = DEFAULT_REVIEW_SLA_SECONDS) -> ReviewItem:
    """Hand a finished AI result to a human, with a deadline attached."""
    now = timezone.now()
    with transaction.atomic():
        item = ReviewItem.objects.select_for_update().get(pk=item_id)
        if item.state != State.PROCESSING:
            raise TransitionError(f"cannot complete an item in state {item.state}")
        if item.lease_owner != worker_id:
            # The lease lapsed and someone else took the item. Finishing now
            # would overwrite their work with a stale result.
            raise TransitionError("lease is held by another worker")

        item.record(to_state=State.AWAITING_REVIEW, actor=worker_id)
        item.state = State.AWAITING_REVIEW
        item.ai_output = ai_output
        item.review_deadline = now + timedelta(seconds=review_sla_seconds)
        item.lease_owner = None
        item.lease_expires_at = None
        item.save(update_fields=[
            "state", "ai_output", "review_deadline", "lease_owner",
            "lease_expires_at", "updated_at",
        ])
        return item


def fail_processing(*, item_id, worker_id: str, error: str,
                    max_attempts: int = DEFAULT_MAX_ATTEMPTS) -> ReviewItem:
    """Record a failed attempt; dead-letter the item once attempts run out."""
    with transaction.atomic():
        item = ReviewItem.objects.select_for_update().get(pk=item_id)
        if item.state != State.PROCESSING:
            raise TransitionError(f"cannot fail an item in state {item.state}")

        exhausted = item.attempts >= max_attempts
        to_state = State.FAILED if exhausted else State.QUEUED
        item.record(
            to_state=to_state, actor=worker_id,
            detail={"error": error, "attempt": item.attempts, "exhausted": exhausted},
        )
        item.state = to_state
        item.last_error = error
        item.lease_owner = None
        item.lease_expires_at = None
        item.save(update_fields=[
            "state", "last_error", "lease_owner", "lease_expires_at", "updated_at",
        ])
        return item


# --------------------------------------------------------------------------
# Reviewer side
# --------------------------------------------------------------------------

def claim_for_review(*, reviewer: str,
                     lease_seconds: int = DEFAULT_REVIEW_LEASE_SECONDS) -> ReviewItem | None:
    """Give a reviewer an item nobody else is currently looking at.

    The claim is a lease, not a permanent assignment. A reviewer who opens an
    item and walks away must not take it out of circulation, because the item
    still has a deadline running against it and nobody would know it was
    unreachable until that deadline passed.
    """
    now = timezone.now()
    with transaction.atomic():
        # Critical work first, then whatever is closest to its deadline.
        # Spelled out with Case/When rather than leaning on the fact that the
        # tier names happen to sort the right way alphabetically, which is an
        # accident that a future tier name would quietly break.
        tier_rank = Case(
            When(risk_tier=RiskTier.CRITICAL, then=Value(0)),
            When(risk_tier=RiskTier.ELEVATED, then=Value(1)),
            default=Value(2),
            output_field=IntegerField(),
        )
        item = (
            ReviewItem.objects.select_for_update(skip_locked=True)
            .filter(state=State.AWAITING_REVIEW)
            .filter(Q(reviewer__isnull=True) | Q(review_lease_expires_at__lt=now))
            .annotate(tier_rank=tier_rank)
            .order_by("tier_rank", "review_deadline")
            .first()
        )
        if item is None:
            return None
        item.reviewer = reviewer
        item.review_lease_expires_at = now + timedelta(seconds=lease_seconds)
        item.save(update_fields=["reviewer", "review_lease_expires_at", "updated_at"])
        return item


def decide(*, item_id, reviewer: str, approve: bool, note: str = "") -> ReviewItem:
    """Record a human decision.

    An item that already expired is not quietly accepted. The reviewer is told
    what state it is actually in, because an approval arriving after the window
    closed is a fact worth surfacing rather than papering over.
    """
    with transaction.atomic():
        item = ReviewItem.objects.select_for_update().get(pk=item_id)
        if item.state != State.AWAITING_REVIEW:
            raise TransitionError(
                f"item is {item.state}, not awaiting review; no decision recorded"
            )

        to_state = State.APPROVED if approve else State.REJECTED
        item.record(to_state=to_state, actor=reviewer, detail={"note": note})
        item.state = to_state
        item.reviewer = reviewer
        item.decided_at = timezone.now()
        item.review_lease_expires_at = None
        item.save(update_fields=[
            "state", "reviewer", "decided_at", "review_lease_expires_at", "updated_at",
        ])
        return item


def expire_overdue(*, now=None, limit: int = 500) -> int:
    """Move items past their deadline to EXPIRED.

    This is the whole reason the EXPIRED state exists. An earlier version of a
    system I ran wired the "nobody answered" branch straight into the rejection
    handler, so every timeout was reported to the team as a rejection, while the
    work in question had in fact gone live. The states are distinct here and the
    audit detail says in words that no decision was made.
    """
    now = now or timezone.now()
    expired = 0
    with transaction.atomic():
        items = list(
            ReviewItem.objects.select_for_update(skip_locked=True)
            .filter(state=State.AWAITING_REVIEW, review_deadline__lt=now)
            .order_by("review_deadline")[:limit]
        )
        for item in items:
            item.record(
                to_state=State.EXPIRED, actor="system",
                detail={
                    "reason": "review window closed with no decision",
                    "not_a_rejection": True,
                    "deadline": item.review_deadline.isoformat(),
                },
            )
            item.state = State.EXPIRED
            item.save(update_fields=["state", "updated_at"])
            expired += 1
    return expired


# --------------------------------------------------------------------------
# The AI stage
# --------------------------------------------------------------------------

def default_ai_stage(payload: dict) -> dict:
    """Stand-in for the model call, so tests stay hermetic and offline.

    A real adapter belongs here: one provider behind one signature, the response
    validated against a schema before it is returned, and a second provider
    behind the same signature as a fallback. The important part for this
    repository is the shape, which is that whatever comes back is validated
    before it is written, never trusted because it parsed.
    """
    text = str(payload.get("text", ""))
    return {
        "summary": text[:160],
        "confidence": 0.5 if text else 0.0,
        "model": "stub-v1",
    }


def process_once(*, worker_id: str, ai_stage: Callable[[dict], dict] = default_ai_stage,
                 lease_seconds: int = DEFAULT_LEASE_SECONDS,
                 max_attempts: int = DEFAULT_MAX_ATTEMPTS) -> ReviewItem | None:
    """One worker turn: claim, run the stage, hand to review or record failure."""
    item = claim_for_processing(worker_id=worker_id, lease_seconds=lease_seconds)
    if item is None:
        return None
    try:
        output = ai_stage(item.payload)
        if not isinstance(output, dict) or "summary" not in output:
            raise ValueError(f"ai stage returned an unusable shape: {type(output).__name__}")
    except Exception as exc:
        return fail_processing(
            item_id=item.id, worker_id=worker_id, error=str(exc), max_attempts=max_attempts
        )
    return complete_processing(item_id=item.id, worker_id=worker_id, ai_output=output)
