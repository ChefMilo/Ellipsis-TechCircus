/**
 * Text normalisation shared by the claim matcher (src/content/anchor/) and any
 * caller that needs the same notion of "the same text" the backend used.
 *
 * The backend extracts `claim.text` from cleaned article text; the live DOM has
 * that same text with different whitespace, smart-quote variants, soft hyphens,
 * NBSPs, and inline markup. Normalising both sides to a canonical form lets an
 * exact `indexOf` succeed far more often, and gives the fuzzy matcher a stable
 * token stream to work on.
 */

/** Any run of whitespace. JS `\s` already covers NBSP (U+00A0), the
 * U+2000–U+200A range, U+202F, U+205F and U+3000. Collapses to one ASCII space. */
const WHITESPACE_RE = /\s+/g;

/** Zero-width space / joiners (U+200B–U+200D), word joiner (U+2060), BOM
 * (U+FEFF) and soft hyphen (U+00AD) — no textual meaning, stripped outright. */
const ZERO_WIDTH_RE = /[​‌‍⁠﻿­]/g;

const QUOTE_MAP: Record<string, string> = {
  "‘": "'", // ‘
  "’": "'", // ’
  "‚": "'", // ‚
  "‛": "'", // ‛
  "′": "'", // ′
  "´": "'", // ´
  "`": "'", // `
  "“": '"', // “
  "”": '"', // ”
  "„": '"', // „
  "‟": '"', // ‟
  "″": '"', // ″
  "«": '"', // «
  "»": '"', // »
};

const DASH_MAP: Record<string, string> = {
  "‐": "-", // ‐
  "‑": "-", // ‑ non-breaking hyphen
  "‒": "-", // ‒
  "–": "-", // – en dash
  "—": "-", // — em dash
  "―": "-", // ―
  "−": "-", // − minus sign
};

const PUNCT_RE =
  /[‘’‚‛′´`“”„‟″«»‐‑‒–—―−]/g;
const ELLIPSIS_RE = /…/g; // …

export interface NormalizeOptions {
  /** Lowercase the result. Use for match keys only, never for display. */
  caseFold?: boolean;
  /** Trim leading/trailing whitespace (default true). */
  trim?: boolean;
}

/**
 * Canonicalise a string for text matching:
 *  - Unicode NFKC (compatibility decomposition + canonical composition)
 *  - strip zero-width / soft-hyphen characters
 *  - unify smart quotes -> ' and ", dashes -> -, ellipsis -> "..."
 *  - collapse every run of whitespace to a single ASCII space
 */
export function normalizeText(input: string, options: NormalizeOptions = {}): string {
  const { caseFold = false, trim = true } = options;

  let out = input.normalize("NFKC");
  out = out.replace(ZERO_WIDTH_RE, "");
  out = out.replace(ELLIPSIS_RE, "...");
  out = out.replace(PUNCT_RE, (ch) => QUOTE_MAP[ch] ?? DASH_MAP[ch] ?? ch);
  out = out.replace(WHITESPACE_RE, " ");
  if (trim) out = out.trim();
  if (caseFold) out = out.toLowerCase();
  return out;
}

/** Word-ish tokens for similarity scoring. Punctuation is dropped. */
export function tokenize(input: string): string[] {
  const normalized = normalizeText(input, { caseFold: true });
  const matches = normalized.match(/[\p{L}\p{N}]+/gu);
  return matches ?? [];
}
