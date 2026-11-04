from django.urls import path

from reviews import api

urlpatterns = [
    path("api/items", api.items, name="items"),
    path("api/items/<uuid:item_id>", api.item_detail, name="item-detail"),
    path("api/items/<uuid:item_id>/decide", api.decide_item, name="item-decide"),
    path("api/review/claim", api.claim_review, name="review-claim"),
    path("api/health", api.health, name="health"),
]
