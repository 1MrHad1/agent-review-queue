import { useCallback, useEffect, useRef, useState } from "react";

import { api, ConflictError, type ReviewItem, type RiskTier } from "./api";
import { useDeadline } from "./useDeadline";

type Status =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "empty" }
  | { kind: "ready"; item: ReviewItem }
  | { kind: "error"; message: string; recoverable: boolean };

export function ReviewScreen({ reviewer }: { reviewer: string }) {
  const [status, setStatus] = useState<Status>({ kind: "idle" });
  const [note, setNote] = useState("");
  const [submitting, setSubmitting] = useState(false);
  // Claiming is not idempotent: each call takes an item out of everyone else's
  // queue. StrictMode runs mount effects twice in development, and a user can
  // double-click, so overlapping calls are guarded here. The server holds the
  // real safety net, a lease that expires, because a client cannot be trusted
  // to clean up after a closed tab.
  const claiming = useRef(false);

  const claimNext = useCallback(async () => {
    if (claiming.current) return;
    claiming.current = true;
    setStatus({ kind: "loading" });
    setNote("");
    try {
      const { item } = await api.claimForReview(reviewer);
      if (!item) {
        setStatus({ kind: "empty" });
        return;
      }
      setStatus({ kind: "ready", item: await api.detail(item.id) });
    } catch (error) {
      setStatus({
        kind: "error",
        message: error instanceof Error ? error.message : "something went wrong",
        recoverable: true,
      });
    } finally {
      claiming.current = false;
    }
  }, [reviewer]);

  useEffect(() => {
    void claimNext();
  }, [claimNext]);

  async function decide(decision: "approve" | "reject") {
    if (status.kind !== "ready" || submitting) return;
    setSubmitting(true);
    try {
      await api.decide(status.item.id, reviewer, decision, note);
      await claimNext();
    } catch (error) {
      if (error instanceof ConflictError) {
        // The item moved underneath the reviewer, most likely its window
        // closed. Say what happened rather than offering a retry that would
        // fail the same way, and show the true state.
        const latest = await api.detail(status.item.id);
        setStatus({
          kind: "error",
          message: `${error.message} It is now ${latest.state}. Your decision was not recorded.`,
          recoverable: true,
        });
      } else {
        setStatus({
          kind: "error",
          message: error instanceof Error ? error.message : "could not save the decision",
          recoverable: true,
        });
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="screen">
      <header>
        <h1>Review queue</h1>
        <span className="who">signed in as {reviewer}</span>
      </header>

      {status.kind === "loading" && <p className="muted">Finding the next item…</p>}

      {status.kind === "empty" && (
        <div className="panel">
          <p>Nothing is waiting for a decision.</p>
          <button onClick={() => void claimNext()}>Check again</button>
        </div>
      )}

      {status.kind === "error" && (
        <div className="panel warn">
          <p>{status.message}</p>
          {status.recoverable && <button onClick={() => void claimNext()}>Next item</button>}
        </div>
      )}

      {status.kind === "ready" && (
        <ItemCard
          item={status.item}
          note={note}
          onNote={setNote}
          submitting={submitting}
          onDecide={decide}
        />
      )}
    </main>
  );
}

function TierPill({ tier }: { tier: RiskTier }) {
  // Critical items are handed out first by the API; saying so on the card means
  // a reviewer is never guessing why this one landed in front of them.
  return <span className={`pill tier-${tier.toLowerCase()}`}>{tier.toLowerCase()}</span>;
}

function ItemCard({
  item,
  note,
  onNote,
  submitting,
  onDecide,
}: {
  item: ReviewItem;
  note: string;
  onNote: (value: string) => void;
  submitting: boolean;
  onDecide: (decision: "approve" | "reject") => void;
}) {
  const deadline = useDeadline(item.review_deadline);

  return (
    <article className="panel">
      <div className="row">
        <h2>{item.source_ref}</h2>
        <div className="pills">
          {item.risk_tier && <TierPill tier={item.risk_tier} />}
          {deadline && (
            <span className={deadline.expired ? "pill expired" : "pill"}>{deadline.text}</span>
          )}
        </div>
      </div>

      <div className="split">
        <section>
          <h3>Input</h3>
          <pre>{JSON.stringify(item.payload, null, 2)}</pre>
        </section>
        <section>
          <h3>Proposed by the model</h3>
          <pre>{JSON.stringify(item.ai_output, null, 2)}</pre>
        </section>
      </div>

      <label htmlFor="note">Note, kept on the record</label>
      <textarea
        id="note"
        value={note}
        onChange={(event) => onNote(event.target.value)}
        rows={3}
        placeholder="Why you decided what you decided"
      />

      <div className="actions">
        <button
          className="approve"
          disabled={submitting || deadline?.expired}
          onClick={() => onDecide("approve")}
        >
          Approve
        </button>
        <button
          className="reject"
          disabled={submitting || deadline?.expired}
          onClick={() => onDecide("reject")}
        >
          Reject
        </button>
      </div>

      {item.events && item.events.length > 0 && (
        <details className="trail">
          <summary>Audit trail ({item.events.length})</summary>
          <ol>
            {item.events.map((event) => (
              <li key={event.seq}>
                <code>
                  {event.from_state || "new"} &rarr; {event.to_state}
                </code>{" "}
                by {event.actor}
                {"not_a_rejection" in event.detail && (
                  <em> (no decision was made; this is not a rejection)</em>
                )}
              </li>
            ))}
          </ol>
        </details>
      )}
    </article>
  );
}
