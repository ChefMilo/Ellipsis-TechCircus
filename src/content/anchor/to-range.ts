/**
 * Turn claim-match offset spans (from ./match-claim.ts) into live DOM `Range`s,
 * rejecting/clamping spans that would cross a block-level boundary.
 *
 * A highlight that runs from the end of one paragraph into the next reads as a
 * bug, so when a fuzzy match straddles two blocks we keep only the portion in
 * whichever block holds more of the matched text.
 */
import type { VerifiedClaim } from "../../shared/contract";
import { matchClaim, type ClaimMatch, type MatchOptions } from "./match-claim";
import { buildTextIndex, locateOffset, type TextIndex } from "./text-index";

const BLOCK_TAGS = new Set([
  "P",
  "LI",
  "BLOCKQUOTE",
  "H1",
  "H2",
  "H3",
  "H4",
  "H5",
  "H6",
  "DD",
  "DT",
  "PRE",
  "FIGCAPTION",
  "TD",
  "TH",
  "SECTION",
  "ARTICLE",
  "MAIN",
  "DIV",
]);

function nearestBlock(node: Node, root: Element): Element {
  let el: Element | null =
    node.nodeType === Node.ELEMENT_NODE ? (node as Element) : node.parentElement;
  while (el && el !== root) {
    if (BLOCK_TAGS.has(el.tagName.toUpperCase())) return el;
    el = el.parentElement;
  }
  return root;
}

type BlockRange = { flatStart: number; flatEnd: number };

/** For every block that owns at least one text segment, its covered flat range. */
export function buildBlockMap(index: TextIndex): Map<Element, BlockRange> {
  const map = new Map<Element, BlockRange>();
  for (const seg of index.segments) {
    const block = nearestBlock(seg.node, index.root);
    const existing = map.get(block);
    if (existing) {
      existing.flatStart = Math.min(existing.flatStart, seg.flatStart);
      existing.flatEnd = Math.max(existing.flatEnd, seg.flatEnd);
    } else {
      map.set(block, { flatStart: seg.flatStart, flatEnd: seg.flatEnd });
    }
  }
  return map;
}

export interface SpanRangeResult {
  range: Range;
  /** True if the span was shortened to stay within one block. */
  clamped: boolean;
}

export function spanToRange(
  index: TextIndex,
  blockMap: Map<Element, BlockRange>,
  startOffset: number,
  endOffset: number,
): SpanRangeResult | null {
  const startLoc = locateOffset(index, startOffset, "start");
  const endLoc = locateOffset(index, endOffset, "end");
  if (!startLoc || !endLoc) return null;

  const startBlock = nearestBlock(startLoc.node, index.root);
  const endBlock = nearestBlock(endLoc.node, index.root);

  let s = startOffset;
  let e = endOffset;
  let clamped = false;

  if (startBlock !== endBlock) {
    const startRange = blockMap.get(startBlock);
    const endRange = blockMap.get(endBlock);
    const headLen = startRange ? Math.min(e, startRange.flatEnd) - s : 0;
    const tailLen = endRange ? e - Math.max(s, endRange.flatStart) : 0;
    if (headLen >= tailLen && startRange) {
      e = Math.min(e, startRange.flatEnd);
    } else if (endRange) {
      s = Math.max(s, endRange.flatStart);
    }
    clamped = true;
  }

  const cs = locateOffset(index, s, "start");
  const ce = locateOffset(index, e, "end");
  if (!cs || !ce || e <= s) return null;

  const range = index.root.ownerDocument.createRange();
  try {
    range.setStart(cs.node, cs.offset);
    range.setEnd(ce.node, ce.offset);
  } catch {
    return null;
  }
  if (range.collapsed) return null;
  return { range, clamped };
}

export interface RangesForClaimsResult {
  /** claim id -> live Range for the anchored claims. */
  ranges: Map<string, Range>;
  /** claim id -> the match that produced the range (score/exactness for debug). */
  matches: Map<string, ClaimMatch>;
  /** claim ids that could not be located on the page. */
  unanchored: string[];
}

export interface RangesForClaimsOptions extends MatchOptions {
  /** Article root to index. Defaults to `document.body`. */
  root?: Element;
  /** Prebuilt index (skips `buildTextIndex`); `root` is ignored if given. */
  index?: TextIndex;
}

/**
 * Anchor every claim in `verifiedClaims` against the page. Claims that do not
 * match are returned in `unanchored` — callers must still surface them, never
 * drop them.
 */
export function rangesForClaims(
  verifiedClaims: VerifiedClaim[],
  options: RangesForClaimsOptions = {},
): RangesForClaimsResult {
  const root = options.index?.root ?? options.root ?? document.body;
  const index = options.index ?? buildTextIndex(root);
  const blockMap = buildBlockMap(index);

  const ranges = new Map<string, Range>();
  const matches = new Map<string, ClaimMatch>();
  const unanchored: string[] = [];

  for (const vc of verifiedClaims) {
    const { claim } = vc;
    const match = matchClaim(index, claim, options);
    if (!match) {
      unanchored.push(claim.id);
      continue;
    }
    const result = spanToRange(index, blockMap, match.startOffset, match.endOffset);
    if (!result) {
      unanchored.push(claim.id);
      continue;
    }
    ranges.set(claim.id, result.range);
    matches.set(claim.id, match);
  }

  return { ranges, matches, unanchored };
}
