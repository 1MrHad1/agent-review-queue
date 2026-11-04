"""HTTP surface. Thin on purpose: every rule lives in services.py so the tests
exercise the rules directly rather than through a request cycle."""

from __future__ import annotations

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from . import services
from .models import ReviewItem, RiskTier, State


def _serialize(item: ReviewItem, *, with_events: bool = False) -> dict:
    data = {
        "id": str(item.id),
        "source_ref": item.source_ref,
        "state": item.state,
        "risk_tier": item.risk_tier,
        "payload": item.payload,
        "ai_output": item.ai_output,
        "attempts": item.attempts,
        "last_error": item.last_error,
        "reviewer": item.reviewer,
        "review_deadline": item.review_deadline,
        "decided_at": item.decided_at,
        "created_at": item.created_at,
    }
    if with_events:
        data["events"] = [
            {
                "seq": e.seq,
                "from_state": e.from_state,
                "to_state": e.to_state,
                "actor": e.actor,
                "detail": e.detail,
                "created_at": e.created_at,
            }
            for e in item.events.all()
        ]
    return data


@api_view(["GET"])
def health(request):
    return Response({"ok": True})


@api_view(["GET", "POST"])
def items(request):
    if request.method == "GET":
        qs = ReviewItem.objects.all().order_by("-created_at")
        state = request.query_params.get("state")
        if state:
            if state not in State.values:
                return Response({"error": f"unknown state {state!r}"}, status=status.HTTP_400_BAD_REQUEST)
            qs = qs.filter(state=state)
        tier = request.query_params.get("risk_tier")
        if tier:
            if tier not in RiskTier.values:
                return Response({"error": f"unknown risk_tier {tier!r}"}, status=status.HTTP_400_BAD_REQUEST)
            qs = qs.filter(risk_tier=tier)
        return Response([_serialize(i) for i in qs[:100]])

    key = request.headers.get("Idempotency-Key") or request.data.get("idempotency_key")
    if not key:
        return Response(
            {"error": "an Idempotency-Key header or idempotency_key field is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    item, created = services.submit(
        idempotency_key=key,
        source_ref=request.data.get("source_ref", ""),
        payload=request.data.get("payload", {}),
    )
    # A repeat of a request we already accepted is a 200 describing the original,
    # not a 409. The caller retried because it did not hear us the first time.
    return Response(
        _serialize(item),
        status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
    )


@api_view(["GET"])
def item_detail(request, item_id):
    item = get_object_or_404(ReviewItem.objects.prefetch_related("events"), pk=item_id)
    return Response(_serialize(item, with_events=True))


@api_view(["POST"])
def claim_review(request):
    reviewer = request.data.get("reviewer")
    if not reviewer:
        return Response({"error": "reviewer is required"}, status=status.HTTP_400_BAD_REQUEST)
    item = services.claim_for_review(reviewer=reviewer)
    if item is None:
        return Response({"item": None, "message": "nothing awaiting review"})
    return Response({"item": _serialize(item)})


@api_view(["POST"])
def decide_item(request, item_id):
    reviewer = request.data.get("reviewer")
    decision = request.data.get("decision")
    if decision not in {"approve", "reject"}:
        return Response({"error": "decision must be 'approve' or 'reject'"},
                        status=status.HTTP_400_BAD_REQUEST)
    if not reviewer:
        return Response({"error": "reviewer is required"}, status=status.HTTP_400_BAD_REQUEST)
    try:
        item = services.decide(
            item_id=item_id, reviewer=reviewer,
            approve=(decision == "approve"), note=request.data.get("note", ""),
        )
    except ReviewItem.DoesNotExist:
        return Response({"error": "no such item"}, status=status.HTTP_404_NOT_FOUND)
    except services.TransitionError as exc:
        # 409 rather than 400: the request was well formed, the world moved.
        return Response({"error": str(exc)}, status=status.HTTP_409_CONFLICT)
    return Response(_serialize(item, with_events=False))
