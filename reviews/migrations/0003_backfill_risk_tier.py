"""Step 2 of 4: fill the existing rows, in batches.

One UPDATE over the whole table would hold row locks and bloat a single
transaction for as long as it ran, blocking writers behind it. Batching keeps
each transaction short, so the backfill shares the table rather than owning it.

``atomic = False`` is the point of the exercise: without it Django wraps the
whole migration in one transaction and the batching achieves nothing.
"""

from django.db import migrations

BATCH = 1000


def fill(apps, schema_editor):
    ReviewItem = apps.get_model("reviews", "ReviewItem")
    while True:
        ids = list(
            ReviewItem.objects.filter(risk_tier__isnull=True)
            .values_list("pk", flat=True)[:BATCH]
        )
        if not ids:
            break
        # Existing rows predate triage, so they take the safe tier rather than a
        # guess. A wrong guess here would silently downgrade scrutiny on work
        # that already passed through the system.
        ReviewItem.objects.filter(pk__in=ids).update(risk_tier="ELEVATED")


def unfill(apps, schema_editor):
    # Reversing drops the data this migration invented, nothing more.
    apps.get_model("reviews", "ReviewItem").objects.update(risk_tier=None)


class Migration(migrations.Migration):
    atomic = False
    dependencies = [("reviews", "0002_risk_tier_nullable")]
    operations = [migrations.RunPython(fill, unfill)]
