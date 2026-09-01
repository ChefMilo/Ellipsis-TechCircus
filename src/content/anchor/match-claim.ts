/**
 * Locate a claim sentence within a flattened article text index.
 *
 * Strategy (cheapest first):
 *   1. Exact normalised substring match. Disambiguate multiple hits with the
 *      optional `char_start` / `prefix` / `suffix` hints, else take the first.
 *   2. Fuzzy: slide a token window (±25% of the claim's token count) across the
 *      article and score each window against the claim with a Sørensen–Dice
 *      coefficient over word bigrams. Keep the best window at or above
 *      `threshold`. The default (0.72) tolerates light editorial rewording
 *      ("very sophisticated and hard" vs "sophisticated and difficult") while
 *      still needing most word pairs to coincide — a mis-anchor only shifts a
 *      highlight to a near-identical sentence, it never changes a verdict.
 *
 * Output is a `[startOffset, endOffset)` span into `index.flat`. Turning that
 * into a DOM `Range` (and rejecting cross-block matches) is ./to-range.ts's job.
 */
import type { Claim } from "../../shared/contract";
import { normalizeText, tokenize } from "../../shared/text-normalize";
import type { TextIndex } from "./text-index";

export interface ClaimMatch {
  startOffset: number;
  endOffset: number;
  /** 1 for an exact match; the Dice coefficient (0..1) for a fuzzy match. */
  score: number;
  exact: boolean;
}

export interface MatchOptions {
  /** Minimum Dice coefficient for a fuzzy match to count. Default 0.85. */
  threshold?: number;
  /** Fractional slack on the token-window length. Default 0.2. */
  windowSlack?: number;
}

const DEFAULT_THRESHOLD = 0.72;
const DEFAULT_WINDOW_SLACK = 0.25;

interface FlatToken {
  text: string; // case-folded
  start: number; // char offset into flat
  end: number; // exclusive
}

const TOKEN_RE = /[\p{L}\p{N}]+/gu;

function flatTokens(flatLower: string): FlatToken[] {
  const out: FlatToken[] = [];
  for (const m of flatLower.matchAll(TOKEN_RE)) {
    const start = m.index ?? 0;
    out.push({ text: m[0], start, end: start + m[0].length });
  }
  return out;
}

function bigrams(tokens: string[]): string[] {
  if (tokens.length <= 1) return tokens.slice();
  const out: string[] = [];
  for (let i = 0; i < tokens.length - 1; i++) out.push(`${tokens[i]} ${tokens[i + 1]}`);
  return out;
}

/** Sørensen–Dice over two bigram multisets. */
function dice(a: string[], b: string[]): number {
  if (a.length === 0 && b.length === 0) return 1;
  if (a.length === 0 || b.length === 0) return 0;
  const counts = new Map<string, number>();
  for (const g of a) counts.set(g, (counts.get(g) ?? 0) + 1);
  let intersection = 0;
  for (const g of b) {
    const c = counts.get(g) ?? 0;
    if (c > 0) {
      intersection++;
      counts.set(g, c - 1);
    }
  }
  return (2 * intersection) / (a.length + b.length);
}

function allIndexesOf(haystack: string, needle: string): number[] {
  const out: number[] = [];
  if (needle.length === 0) return out;
  let from = 0;
  for (;;) {
    const idx = haystack.indexOf(needle, from);
    if (idx === -1) break;
    out.push(idx);
    from = idx + 1;
  }
  return out;
}

/**
 * Score an exact-match candidate by how well the surrounding flat text agrees
 * with the claim's `prefix` / `suffix` hints and its `char_start` hint.
 */
function scoreExactCandidate(
  flatLower: string,
  at: number,
  needleLen: number,
  claim: Claim,
): number {
  let score = 0;
  const prefix = claim.prefix ? normalizeText(claim.prefix, { caseFold: true }) : "";
  const suffix = claim.suffix ? normalizeText(claim.suffix, { caseFold: true }) : "";
  if (prefix) {
    const before = flatLower.slice(Math.max(0, at - prefix.length - 4), at);
    if (before.endsWith(prefix)) score += 2;
    else if (before.includes(prefix.slice(-Math.min(prefix.length, 12)))) score += 1;
  }
  if (suffix) {
    const after = flatLower.slice(at + needleLen, at + needleLen + suffix.length + 4);
    if (after.startsWith(suffix)) score += 2;
    else if (after.includes(suffix.slice(0, Math.min(suffix.length, 12)))) score += 1;
  }
  if (typeof claim.char_start === "number") {
    // Smaller distance is better; convert to a small positive bonus.
    const distance = Math.abs(at - claim.char_start);
    score += Math.max(0, 1 - distance / 400);
  }
  return score;
}

export function matchClaim(
  index: TextIndex,
  claim: Claim,
  options: MatchOptions = {},
): ClaimMatch | null {
  const threshold = options.threshold ?? DEFAULT_THRESHOLD;
  const slack = options.windowSlack ?? DEFAULT_WINDOW_SLACK;

  const { flat } = index;
  if (flat.length === 0) return null;

  const flatLower = flat.toLowerCase();
  // Case folding is assumed length-preserving; if a locale expands a character,
  // fall back to case-sensitive matching so offsets stay valid.
  const lowerOk = flatLower.length === flat.length;
  const hay = lowerOk ? flatLower : flat;

  const needle = lowerOk
    ? normalizeText(claim.text, { caseFold: true })
    : normalizeText(claim.text);
  if (needle.length === 0 || needle.length > hay.length) return null;

  // 1. Exact.
  const hits = allIndexesOf(hay, needle);
  if (hits.length === 1) {
    return { startOffset: hits[0]!, endOffset: hits[0]! + needle.length, score: 1, exact: true };
  }
  if (hits.length > 1) {
    let best = hits[0]!;
    let bestScore = -Infinity;
    for (const at of hits) {
      const s = scoreExactCandidate(hay, at, needle.length, claim);
      if (s > bestScore) {
        bestScore = s;
        best = at;
      }
    }
    return { startOffset: best, endOffset: best + needle.length, score: 1, exact: true };
  }

  // 2. Fuzzy.
  const needleTokens = tokenize(claim.text);
  if (needleTokens.length === 0) return null;
  const needleGrams = bigrams(needleTokens);

  const tokens = flatTokens(hay);
  if (tokens.length === 0) return null;

  const n = needleTokens.length;
  const minLen = Math.max(1, Math.floor(n * (1 - slack)));
  const maxLen = Math.min(tokens.length, Math.ceil(n * (1 + slack)));

  let best: ClaimMatch | null = null;
  for (let len = minLen; len <= maxLen; len++) {
    for (let i = 0; i + len <= tokens.length; i++) {
      const windowTokens: string[] = [];
      for (let k = 0; k < len; k++) windowTokens.push(tokens[i + k]!.text);
      const score = dice(needleGrams, bigrams(windowTokens));
      if (score >= threshold && (best === null || score > best.score)) {
        best = {
          startOffset: tokens[i]!.start,
          endOffset: tokens[i + len - 1]!.end,
          score,
          exact: false,
        };
      }
    }
  }
  return best;
}
