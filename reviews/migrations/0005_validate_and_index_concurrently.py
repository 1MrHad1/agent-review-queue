"""Step 4 of 4: verify the old rows, and index without blocking writes.

``VALIDATE CONSTRAINT`` scans the table but takes only SHARE UPDATE EXCLUSIVE,
so reads and writes carry on around it. ``CREATE INDEX CONCURRENTLY`` likewise.
Both refuse to run inside a transaction block, which is why ``atomic = False``
is mandatory here rather than merely advisable.
"""

from django.contrib.postgres.operations import AddIndexConcurrently, ValidateConstraint
from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False
    dependencies = [("reviews", "0004_risk_tier_constraint_not_valid")]

    operations = [
        ValidateConstraint(model_name="reviewitem", name="item_risk_tier_present"),
        AddIndexConcurrently(
            model_name="reviewitem",
            index=models.Index(fields=["risk_tier", "state"], name="item_risk_state_idx"),
        ),
    ]
