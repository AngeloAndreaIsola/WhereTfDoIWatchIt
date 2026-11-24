import type { PrimeOffer } from "@/lib/api";
import { formatLastSeen } from "@/lib/time";

/**
 * #97 display contract — one derivation shared by every offer consumer.
 *
 * An offer is "current" only when `availability_evidence` is `verified` (or
 * absent, for legacy payloads whose offer list already means verified).
 * `stale` and `unknown` positives are history: they stay visible but must
 * carry an explicit Unverified badge and must never be promoted to a
 * watchable verdict by a card, filter, export or badge component.
 */
export const UNVERIFIED_BADGE = "Unverified";

export function isUnverifiedOffer(
  offer?: Pick<PrimeOffer, "availability_evidence"> | null,
): boolean {
  const evidence = offer?.availability_evidence;
  return evidence === "stale" || evidence === "unknown";
}

/** Badge plus the newest positive observation, when we have one. */
export function unverifiedOfferLabel(
  offer: Pick<PrimeOffer, "availability_evidence" | "last_seen_at">,
): string {
  const when = formatLastSeen(offer.last_seen_at);
  return offer.availability_evidence === "stale"
    ? `${UNVERIFIED_BADGE} · last seen — ${when}`
    : `${UNVERIFIED_BADGE} · ${when}`;
}
