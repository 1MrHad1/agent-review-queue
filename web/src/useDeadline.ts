import { useEffect, useState } from "react";

/** Time left on a review window, as words.
 *
 *  Returns null when there is no deadline. Returns an "expired" reading rather
 *  than a negative number, because the screen must never imply that a window
 *  which closed is the same as one the reviewer declined.
 */
export function useDeadline(iso: string | null): { text: string; expired: boolean } | null {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!iso) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [iso]);

  if (!iso) return null;

  const remaining = new Date(iso).getTime() - now;
  if (remaining <= 0) return { text: "window closed, no decision recorded", expired: true };

  const hours = Math.floor(remaining / 3_600_000);
  const minutes = Math.floor((remaining % 3_600_000) / 60_000);
  const seconds = Math.floor((remaining % 60_000) / 1000);
  const text = hours > 0 ? `${hours}h ${minutes}m left` : `${minutes}m ${seconds}s left`;
  return { text, expired: false };
}
