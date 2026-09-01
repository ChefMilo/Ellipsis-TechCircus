/**
 * Single source of truth for how each claim status looks — used both for the
 * in-page highlight tint and for the badge in the evidence panel, so the two
 * never drift apart.
 *
 * `unverified` is not a contract status; it is what WS2 shows for a
 * `VerifiedClaim` whose `assessment` is `null` (extracted but not yet judged).
 */
import type { AssessmentStatus } from "../../shared/contract";

export type StatusKey = AssessmentStatus | "unverified";

export interface StatusTreatment {
  key: StatusKey;
  /** Short human label, e.g. "Contradicted". */
  label: string;
  /** Solid accent colour for badges, borders, the highlight underline. */
  accent: string;
  /** Translucent fill for the ::highlight() pseudo / fallback <span>. */
  tint: string;
  /** One-line explanation of what this status means, shown in the panel. */
  blurb: string;
  /** 16×16 inline SVG using `currentColor`. */
  icon: string;
}

const ICONS = {
  check:
    '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><path fill="currentColor" d="M13.5 3.5 6 11 2.5 7.5 1 9l5 5 9-9z"/></svg>',
  tilde:
    '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><path fill="currentColor" d="M1 9c1.2-2.4 2.6-3.6 4.2-3.6 1.2 0 2 .6 2.9 1.7.7.9 1.1 1.2 1.7 1.2.8 0 1.5-.6 2.2-2L15 8c-1.2 2.4-2.6 3.6-4.2 3.6-1.2 0-2-.6-2.9-1.7-.7-.9-1.1-1.2-1.7-1.2-.8 0-1.5.6-2.2 2L1 9z"/></svg>',
  cross:
    '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><path fill="currentColor" d="M12.7 4.7 9.4 8l3.3 3.3-1.4 1.4L8 9.4l-3.3 3.3-1.4-1.4L6.6 8 3.3 4.7l1.4-1.4L8 6.6l3.3-3.3z"/></svg>',
  question:
    '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><path fill="currentColor" d="M8 1a7 7 0 1 0 0 14A7 7 0 0 0 8 1zm.9 10.6H7.1v-1.8h1.8v1.8zm1.4-4.3c-.4.5-1 .8-1.2 1.1-.2.2-.3.5-.3 1H7.1c0-.8.1-1.3.4-1.7.3-.4.9-.8 1.1-1 .3-.3.4-.6.4-.9 0-.6-.5-1-1.1-1s-1.1.4-1.2 1.1L5 5.6C5.2 4.1 6.4 3.2 8 3.2c1.7 0 2.9 1 2.9 2.4 0 .6-.2 1.1-.6 1.7z"/></svg>',
  quote:
    '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><path fill="currentColor" d="M3 10c0-2 .8-3.6 2.5-4.7L6.3 6C5.2 6.8 4.7 7.6 4.7 8.6c.7 0 1.3.6 1.3 1.4S5.3 11.4 4.4 11.4 3 10.8 3 10zm6 0c0-2 .8-3.6 2.5-4.7L12.3 6c-1.1.8-1.6 1.6-1.6 2.6.7 0 1.3.6 1.3 1.4s-.7 1.4-1.6 1.4S9 10.8 9 10z"/></svg>',
  dot:
    '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><circle cx="8" cy="8" r="3.2" fill="currentColor"/></svg>',
} as const;

export const STATUS: Record<StatusKey, StatusTreatment> = {
  supported: {
    key: "supported",
    label: "Supported",
    accent: "#1a7f37",
    tint: "rgba(26, 127, 55, 0.18)",
    blurb: "Retrieved sources back up this claim.",
    icon: ICONS.check,
  },
  partially_supported: {
    key: "partially_supported",
    label: "Partially supported",
    accent: "#9a6700",
    tint: "rgba(154, 103, 0, 0.20)",
    blurb: "Sources support part of this claim but not all of it.",
    icon: ICONS.tilde,
  },
  contradicted: {
    key: "contradicted",
    label: "Contradicted",
    accent: "#cf222e",
    tint: "rgba(207, 34, 46, 0.20)",
    blurb: "Retrieved sources conflict with this claim.",
    icon: ICONS.cross,
  },
  needs_review: {
    key: "needs_review",
    label: "Needs review",
    accent: "#0969da",
    tint: "rgba(9, 105, 218, 0.16)",
    blurb: "Not enough evidence was found to judge this claim either way.",
    icon: ICONS.question,
  },
  opinion: {
    key: "opinion",
    label: "Opinion",
    accent: "#8250df",
    tint: "rgba(130, 80, 223, 0.16)",
    blurb: "This is a value judgement or prediction, not a checkable fact.",
    icon: ICONS.quote,
  },
  unverified: {
    key: "unverified",
    label: "Not yet verified",
    accent: "#57606a",
    tint: "rgba(87, 96, 106, 0.14)",
    blurb: "This claim was extracted but has not been assessed yet.",
    icon: ICONS.dot,
  },
};

/** All keys that get a distinct `::highlight()` registration. */
export const STATUS_KEYS = Object.keys(STATUS) as StatusKey[];

export function statusKeyFor(assessmentStatus: AssessmentStatus | null | undefined): StatusKey {
  return assessmentStatus ?? "unverified";
}
