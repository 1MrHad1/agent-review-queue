"""
Domain model for a human-approved AI pipeline.

The shape is deliberately small: work arrives, a worker runs an AI stage over it,
a person decides, and every transition is appended to a log that is never edited.

Two things here are load-bearing and are the reason the rest exists:

1. A review that nobody answered is EXPIRED, not REJECTED. Those are different
   facts about the world and collapsing them into one state is how a pipeline
   ends up telling its operators that it destroyed work it actually published.
   See the note in README.md; this rule is the scar from a real incident.

2. Nothing moves state without appending to the audit log in the same
   transaction. If the two could diverge, the log would be decoration.
"""

from __future__ import annotations

import uuid

from django.db import models, transaction
from django.utils import timezone


class State(models.TextChoices):
    QUEUED = "QUEUED", "Queued"
    PROCESSING = "PROCESSING", "Processing"
    AWAITING_REVIEW = "AWAITING_REVIEW", "Awaiting review"
    APPROVED = "APPROVED", "Approved"
    REJECTED = "REJECTED", "Rejected"
    # Nobody answered before the deadline. NOT a rejection.
    EXPIRED = "EXPIRED", "Expired without a decision"
    # Ran out of attempts. Kept, with the last error, so it can be inspected.
    FAILED = "FAILED", "Failed"

    @classmethod
    def terminal(cls) -> set[str]:
        return {cls.APPROVED, cls.REJECTED, cls.EXPIRED, cls.FAILED}


class RiskTier(models.TextChoices):
    """Added after launch, which is why migrations 0002-0005 exist in the shape
    they do. See README.md; a column added the naive way locks the table."""

    ROUTINE = "ROUTINE", "Routine"
    ELEVATED = "ELEVATED", "Elevated"
    CRITICAL = "CRITICAL", "Critical"


class ReviewItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # The caller's key. A retried submission must not create a second item, so
    # this is enforced by the database rather than by a read-then-write check,
    # which would race under concurrent retries.
    idempotency_key = models.CharField(max_length=200, unique=True)

    source_ref = models.CharField(max_length=200)
    payload = models.JSONField(default=dict)

    # Nullable in the schema even though every row now has one. The NOT NULL is
    # expressed as a validated CHECK constraint instead, because adding a real
    # NOT NULL to a populated table takes an ACCESS EXCLUSIVE lock for the
    # length of a full scan, and that is an outage on a big table.
    risk_tier = models.CharField(
        max_length=20, choices=RiskTier.choices, null=True, blank=True
    )

    state = models.CharField(
        max_length=20, choices=State.choices, default=State.QUEUED
    )

    ai_output = models.JSONField(null=True, blank=True)

    # Worker lease. A worker claims an item for a bounded window; if it dies,
    # the lease lapses and another worker may take the item. Without this a
    # crashed worker leaves rows stuck in PROCESSING forever.
    lease_owner = models.CharField(max_length=100, null=True, blank=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True)

    attempts = models.PositiveIntegerField(default=0)
    last_error = models.TextField(null=True, blank=True)

    # Set when the item enters AWAITING_REVIEW.
    review_deadline = models.DateTimeField(null=True, blank=True)
    reviewer = models.CharField(max_length=100, null=True, blank=True)
    # A reviewer claim is a lease, exactly like a worker's. Found the hard way:
    # without one, a reviewer who opens an item and then closes the tab removes
    # it from everyone else's queue permanently, and nothing reports it missing.
    # An item nobody can reach is worse than one nobody has started.
    review_lease_expires_at = models.DateTimeField(null=True, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(risk_tier__isnull=False),
                name="item_risk_tier_present",
            ),
        ]
        indexes = [
            # Reviewers work the critical tier first, so the queue is read by
            # tier and state together.
            models.Index(fields=["risk_tier", "state"], name="item_risk_state_idx"),
            # The worker's queue scan: oldest QUEUED first.
            models.Index(fields=["state", "created_at"], name="item_state_created_idx"),
            # The expiry sweep only ever looks at items awaiting a decision, so
            # a partial index keeps it small no matter how much history piles up.
            models.Index(
                fields=["review_deadline"],
                name="item_review_deadline_idx",
                condition=models.Q(state="AWAITING_REVIEW"),
            ),
            # Reclaiming abandoned reviewer claims.
            models.Index(
                fields=["review_lease_expires_at"],
                name="item_review_lease_idx",
                condition=models.Q(state="AWAITING_REVIEW"),
            ),
            # Reclaiming lapsed leases, likewise only relevant while processing.
            models.Index(
                fields=["lease_expires_at"],
                name="item_lease_idx",
                condition=models.Q(state="PROCESSING"),
            ),
        ]

    def __str__(self) -> str:
        return f"{self.source_ref} [{self.state}]"

    @property
    def is_terminal(self) -> bool:
        return self.state in State.terminal()

    def record(self, *, to_state: str, actor: str, detail: dict | None = None) -> "AuditEvent":
        """Append an audit event for a transition out of the current state.

        Call this inside the same transaction as the state change. The sequence
        number is taken under the row lock the caller already holds, so two
        concurrent writers cannot produce the same seq for one item.
        """
        last = (
            AuditEvent.objects.filter(item=self)
            .order_by("-seq")
            .values_list("seq", flat=True)
            .first()
        )
        return AuditEvent.objects.create(
            item=self,
            seq=(last or 0) + 1,
            from_state=self.state,
            to_state=to_state,
            actor=actor,
            detail=detail or {},
        )


class AuditEvent(models.Model):
    """Append-only. Updates and deletes are refused at the model layer.

    A regulated reviewer is not interested in the current state, they are
    interested in who decided what and when. That record is worthless if any
    part of the application can quietly revise it.
    """

    id = models.BigAutoField(primary_key=True)
    item = models.ForeignKey(ReviewItem, on_delete=models.PROTECT, related_name="events")
    seq = models.PositiveIntegerField()

    from_state = models.CharField(max_length=20)
    to_state = models.CharField(max_length=20)
    actor = models.CharField(max_length=100)
    detail = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["item", "seq"], name="audit_item_seq_unique"),
        ]
        indexes = [models.Index(fields=["item", "seq"], name="audit_item_seq_idx")]
        ordering = ["item_id", "seq"]

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValueError("AuditEvent is append-only; existing rows cannot be modified")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("AuditEvent is append-only; rows cannot be deleted")

    def __str__(self) -> str:
        return f"{self.item_id} #{self.seq} {self.from_state} -> {self.to_state}"
