/** Types mirror the serializer in reviews/api.py. */

export type State =
  | "QUEUED"
  | "PROCESSING"
  | "AWAITING_REVIEW"
  | "APPROVED"
  | "REJECTED"
  | "EXPIRED"
  | "FAILED";

export interface AuditEvent {
  seq: number;
  from_state: State | "";
  to_state: State;
  actor: string;
  detail: Record<string, unknown>;
  created_at: string;
}

export type RiskTier = "ROUTINE" | "ELEVATED" | "CRITICAL";

export interface ReviewItem {
  id: string;
  source_ref: string;
  state: State;
  risk_tier: RiskTier | null;
  payload: Record<string, unknown>;
  ai_output: Record<string, unknown> | null;
  attempts: number;
  last_error: string | null;
  reviewer: string | null;
  review_deadline: string | null;
  decided_at: string | null;
  created_at: string;
  events?: AuditEvent[];
}

/** A 409 means the request was fine but the item moved while it was being read.
 *  It is kept distinct from a transport failure because the screen responds to
 *  it differently: it reloads rather than offering a retry. */
export class ConflictError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ConflictError";
  }
}

async function send<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (response.status === 409) {
    const body = (await response.json()) as { error?: string };
    throw new ConflictError(body.error ?? "this item changed while you were reading it");
  }
  if (!response.ok) {
    throw new Error(`${init?.method ?? "GET"} ${path} failed with ${response.status}`);
  }
  return (await response.json()) as T;
}

export const api = {
  claimForReview: (reviewer: string) =>
    send<{ item: ReviewItem | null }>("/api/review/claim", {
      method: "POST",
      body: JSON.stringify({ reviewer }),
    }),

  detail: (id: string) => send<ReviewItem>(`/api/items/${id}`),

  decide: (id: string, reviewer: string, decision: "approve" | "reject", note: string) =>
    send<ReviewItem>(`/api/items/${id}/decide`, {
      method: "POST",
      body: JSON.stringify({ reviewer, decision, note }),
    }),
};
