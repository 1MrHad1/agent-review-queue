"""Move reviews past their deadline to EXPIRED.

Run on a schedule. It is separate from the worker on purpose: a stalled worker
must not also stop the clock on human review windows.
"""

from django.core.management.base import BaseCommand

from reviews import services


class Command(BaseCommand):
    help = "Expire reviews whose window closed with no decision."

    def handle(self, *args, **opts):
        count = services.expire_overdue()
        self.stdout.write(f"expired {count} item(s) with no decision (these are not rejections)")
