"""Step 1 of 4: add the column, nullable, with no default.

On Postgres 11+ this is a catalogue-only change: no table rewrite, no scan, and
the ACCESS EXCLUSIVE lock is held for microseconds. Giving it a NOT NULL or a
volatile default here instead is what turns a routine deploy into an outage.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("reviews", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="reviewitem",
            name="risk_tier",
            field=models.CharField(
                blank=True,
                choices=[("ROUTINE", "Routine"), ("ELEVATED", "Elevated"), ("CRITICAL", "Critical")],
                max_length=20,
                null=True,
            ),
        ),
    ]
