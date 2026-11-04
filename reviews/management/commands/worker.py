"""A worker loop. Deliberately a management command rather than Celery: the
queue is the database, which is one fewer moving part to lose work in, and at
this size it is the right trade."""

import signal
import time

from django.core.management.base import BaseCommand

from reviews import services


class Command(BaseCommand):
    help = "Claim queued items, run the AI stage, hand results to review."

    def add_arguments(self, parser):
        parser.add_argument("--worker-id", default="worker-1")
        parser.add_argument("--idle-sleep", type=float, default=1.0)
        parser.add_argument("--once", action="store_true", help="drain the queue and exit")

    def handle(self, *args, **opts):
        stopping = False

        def stop(_signum, _frame):
            nonlocal stopping
            stopping = True
            self.stdout.write("\nfinishing the current item, then stopping")

        signal.signal(signal.SIGINT, stop)
        signal.signal(signal.SIGTERM, stop)

        while not stopping:
            item = services.process_once(worker_id=opts["worker_id"])
            if item is None:
                if opts["once"]:
                    break
                time.sleep(opts["idle_sleep"])
                continue
            self.stdout.write(f"{item.source_ref} -> {item.state}")
