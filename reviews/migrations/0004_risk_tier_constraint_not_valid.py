"""Step 3 of 4: add the constraint without checking the existing rows.

``NOT VALID`` applies the rule to every new write immediately while skipping the
scan of what is already there. That is the trade: the table is protected from
this moment on, and the expensive verification is deferred to 0005 where it can
take a weaker lock.
"""

from django.contrib.postgres.operations import AddConstraintNotValid
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("reviews", "0003_backfill_risk_tier")]

    operations = [
        AddConstraintNotValid(
            model_name="reviewitem",
            constraint=models.CheckConstraint(
                condition=models.Q(risk_tier__isnull=False),
                name="item_risk_tier_present",
            ),
        ),
    ]
