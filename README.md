# agent-review-queue

A Django and Postgres service for AI output that a person has to sign off before
it counts, with a record of who decided what and when.

Work arrives through an idempotent endpoint, a worker runs an AI stage over it,
the result waits for a human, and every transition appends to a log that the
application cannot edit.

## Why this exists, plainly

I have four years of production backends in Node and TypeScript, and PHP before
that. I had not shipped Django. Rather than claim I would pick it up, I rebuilt
a system I already know well in a language I did not, so the code could make the
argument instead of me.

What is deliberately not claimed: this is a week old and it is small. The
judgment in it is not new, though. The design rule it is built around came from
getting it wrong in production, which is described below.

## The rule this is built around

**A review nobody answered is not a rejection.**

A publishing pipeline I ran had a human approval step with a two-day timeout.
The false branch of its "approved?" condition was wired straight into the
rejection handler, so a timeout and a real rejection produced the same message.
Then the team stopped tapping Approve and started publishing drafts directly in
the CMS instead. Every order timed out. Every order was reported to the team as
rejected, while going live exactly as intended. The client concluded the
automation was destroying their work, and believed it for weeks, because why
would the system lie about its own behaviour.

The symptom was a false message. The root cause was a failure path that could
not tell "no answer" from "no".

Here, `EXPIRED` is its own state, its audit entry says `not_a_rejection: true`
in words, and a decision arriving after the window closed is refused with the
actual state named rather than quietly accepted.
`tests/test_expiry_is_not_rejection.py` is that incident, frozen as assertions.

## Correctness under concurrency

Everything below is exercised by tests that run real threads against a real
Postgres. Locking is the thing under test, and a mocked database cannot be wrong
in the way a real one can, so mocked versions of these tests would pass against
code with no locking at all.

| Concern | How |
| --- | --- |
| Two workers taking the same item | `select_for_update(skip_locked=True)`; contenders take different rows instead of blocking or colliding |
| Two reviewers opening the same item | the same, over the awaiting-review queue |
| A retried submission creating a second item | unique constraint on the idempotency key, leaned on rather than a read-then-write check, which two concurrent retries would both pass |
| A crashed worker stranding an item | leases expire; the next sweep reclaims the row and records who it was taken from |
| A reviewer who opens an item and closes the tab | the reviewer claim is a lease too, so an abandoned item returns to the pool instead of disappearing from every queue |
| A revived worker overwriting the new holder | completion checks it still owns the lease |
| An error disappearing | bounded attempts, then `FAILED` with the error kept and the audit entry marked exhausted |
| Model output in the wrong shape | validated before it is written; a bad shape is a failure, not something a reviewer has to catch |
| State and audit log disagreeing | both written in one `transaction.atomic()`; neither can happen alone |

### One of those rows came from running it, not from thinking about it

The reviewer lease is in the table above because the first version did not have
one, and the bug only appeared in the browser. React StrictMode double-invokes
mount effects in development, so the screen claimed two items and rendered one.
The discarded item kept its reviewer forever, dropped out of every queue, and
sat there with its review deadline counting down and nothing anywhere reporting
it unreachable. It was the highest-priority item in the queue.

I had given workers leases and not reviewers, which is the same mistake in two
places with only one of them noticed. The fix is a lease on the reviewer claim,
plus a guard on the client so it stops leaking claims. The server-side lease is
the real fix: a client cannot be trusted to clean up after a closed tab.

`tests/test_worker_lease_and_retries.py` covers both halves.

## Migrations

`0002` through `0004` are a deliberate demonstration rather than a single
`makemigrations` dump, because the posting asked about safe migrations:

* add the column nullable, which is instant on Postgres and takes no heavy lock
* backfill in batches, so a long transaction does not hold up writes
* add the constraint `NOT VALID`, then `VALIDATE` it separately, which takes a
  `SHARE UPDATE EXCLUSIVE` lock instead of `ACCESS EXCLUSIVE`
* add the index with `AddIndexConcurrently` and `atomic = False`

`manage.py sqlmigrate reviews 0005` prints the `VALIDATE CONSTRAINT` and
`CREATE INDEX CONCURRENTLY` these produce, if you would rather read the SQL.

The naive version of the same change locks the table against writes for as long
as the scan takes, which on a large table is an outage.

## Running it

Needs Python 3.11+ and a Postgres you can create a database in.

```
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
createdb reviewqueue
./.venv/bin/python manage.py migrate
./.venv/bin/python manage.py runserver
```

Connection settings come from the standard `PG*` environment variables.

```
./.venv/bin/python -m pytest          # the full suite, against real Postgres
./.venv/bin/python manage.py worker   # one worker, polling
./.venv/bin/python manage.py sweep    # expire overdue reviews
```

## API

| Method | Path | Notes |
| --- | --- | --- |
| `POST` | `/api/items` | requires `Idempotency-Key`; a repeat returns `200` with the original, not a `409` |
| `GET` | `/api/items?state=` | |
| `GET` | `/api/items/{id}` | includes the full audit trail |
| `POST` | `/api/review/claim` | hands the caller an item nobody else holds |
| `POST` | `/api/items/{id}/decide` | `409` if the world moved while the reviewer was reading |

## Layout

```
reviews/models.py     state machine and the append-only audit log
reviews/services.py   every transition, each in one transaction
reviews/api.py        thin HTTP surface; no rules live here
reviews/worker.py     claim, run, hand over or fail
tests/                threads against real Postgres
web/                  a small React and TypeScript reviewer screen
```
