import type {
  EntitlementReason,
  EntitlementVerdict,
  OfferEntitlement,
  PrimeOffer,
} from "./api";
import { isUnverifiedOffer } from "./availability";

export interface VerdictPresentation {
  label: string;
  tone: "positive" | "addon" | "transaction" | "ads" | "blocked" | "unknown";
}

const VERDICT_LABELS: Record<EntitlementVerdict | "MISSING", VerdictPresentation> = {
  INCLUDED: { label: "Included with your selected plan", tone: "positive" },
  REQUIRES_ADDON: { label: "Requires channel add-on", tone: "addon" },
  RENT_OR_BUY: { label: "Rent or buy", tone: "transaction" },
  FREE_WITH_ADS: { label: "Free with ads", tone: "ads" },
  BLOCKED: { label: "Blocked for your selection", tone: "blocked" },
  UNKNOWN: { label: "Plan access unconfirmed", tone: "unknown" },
  MISSING: { label: "Plan access unconfirmed", tone: "unknown" },
};

const REASON_COPY: Record<string, string> = {
  PLAN_COVERS_TITLE: "Your selected plan covers this title under your declared selection. Account status and playback are unverified.",
  CHANNEL_AND_BASE_COVER_TITLE: "Your held base plan and channel together cover this title. Account status and playback are unverified.",
  CHANNEL_NOT_HELD: "The channel is not marked as held. Select the exact child channel on its host to confirm.",
  PUBLIC_FREE: "Explicitly free for this title and market — no subscription required.",
  PUBLIC_AD_FUNDED: "Explicitly free with advertising for this title and market — not a paid plan with ads.",
  RENTAL: "A separate rental action is available. This is not included in a subscription.",
  PURCHASE: "A separate purchase action is available. This is not included in a subscription.",
  BASE_NOT_HELD: "The required base subscription is marked as not held.",
  PLAN_EXCLUDED: "The selected plan explicitly excludes this offer.",
  REGION_EXCLUDED: "The selected product excludes this market.",
  PLAN_UNSPECIFIED: "Confirm your exact plan and holding to resolve plan-dependent access.",
  PLAN_UNSUPPORTED: "The declared plan has no supported mapping for this market.",
  HOLDING_UNCONFIRMED: "The required holding is unconfirmed — it is not counted as access.",
  NO_APPLICABLE_RULE: "Coverage under the selected plan is unconfirmed for this title.",
  PORTABILITY_UNVERIFIED: "Your billing market differs from the offer market and no reviewed portability applies.",
  TERRITORY_UNKNOWN: "The offer's territory is unknown — local access is not claimed.",
  SOURCE_REPORTED: "A source reported this offer; it is not confirmed current availability.",
  STALE_EVIDENCE: "This offer was last seen in an earlier check and needs a recheck.",
  EVIDENCE_CONFLICT: "Applicable offer evidence conflicts; no positive can be asserted.",
  OFFER_CLASS_UNKNOWN: "The monetization class could not be resolved.",
  PACKAGE_UNRESOLVED: "The source package has no reviewed identity for this offer.",
  EVIDENCE_UNVERIFIED: "The observation does not satisfy the current evidence gate.",
  SOURCE_UNSUPPORTED: "This source/market mapping has not passed review for positive results.",
  PUBLIC_CONDITIONS_UNCONFIRMED: "Required conditions for this public offer remain unconfirmed.",
};

/** Pure presentation of the server verdict; never infers coverage itself. */
export function presentVerdict(
  entitlement: OfferEntitlement | null | undefined
): VerdictPresentation {
  if (!entitlement) return VERDICT_LABELS.MISSING;
  if (entitlement.verdict === "INCLUDED") {
    if (entitlement.reason === "PUBLIC_FREE") return { label: "Free", tone: "positive" };
    if (entitlement.reason === "PUBLIC_AD_FUNDED") return { label: "Free with ads", tone: "ads" };
    if (entitlement.reason === "CHANNEL_AND_BASE_COVER_TITLE") {
      return { label: "Included with your channel", tone: "positive" };
    }
  }
  if (entitlement.verdict === "RENT_OR_BUY") {
    if (entitlement.reason === "RENTAL") return { label: "Rent", tone: "transaction" };
    if (entitlement.reason === "PURCHASE") return { label: "Buy", tone: "transaction" };
  }
  return VERDICT_LABELS[entitlement.verdict] ?? VERDICT_LABELS.MISSING;
}

export function explainReason(reason: EntitlementReason | undefined): string {
  if (!reason) return "Coverage could not be resolved.";
  return REASON_COPY[reason] ?? "Coverage could not be resolved from the reported facts.";
}

export function isPositiveVerdict(
  entitlement: OfferEntitlement | null | undefined
): boolean {
  return Boolean(
    entitlement &&
      (entitlement.verdict === "INCLUDED" || entitlement.verdict === "FREE_WITH_ADS")
  );
}

export function isLegacyPlanOffer(offer: Pick<PrimeOffer, "entitlement" | "offer_type" | "is_addon" | "access_type">): boolean {
  return !offer.entitlement && (
    offer.offer_type === "subscription" || offer.offer_type === "addon" ||
    offer.access_type === "subscription" || offer.access_type === "addon" || offer.is_addon === true
  );
}

export function isAccessibleEntitlementOffer(
  offer: Pick<PrimeOffer, "entitlement" | "offer_type" | "is_addon" | "access_type" | "availability_evidence" | "is_accessible">
): boolean {
  return offer.is_accessible === true && !isUnverifiedOffer(offer) && (
    offer.entitlement ? isPositiveVerdict(offer.entitlement) : !isLegacyPlanOffer(offer)
  );
}
