/**
 * Flatten an article subtree into one normalised string plus a map back to the
 * DOM, so a character offset in the flat string can be turned into a live
 * `Range` (see ./to-range.ts).
 *
 * Why streaming, per-character normalisation instead of `normalizeText(node.data)`
 * per node: normalisation changes length (whitespace runs collapse, "…" -> "...",
 * zero-width chars vanish) and whitespace collapses *across* node boundaries.
 * We need to know exactly which raw character in which text node produced each
 * flat character, so we build the flat string one raw char at a time and record
 * that provenance.
 */

/** Elements whose entire subtree is excluded from the flattened text. */
const DEFAULT_SKIP_TAGS = new Set([
  "SCRIPT",
  "STYLE",
  "NOSCRIPT",
  "TEMPLATE",
  "NAV",
  "ASIDE",
  "FIGCAPTION",
  "FORM",
  "BUTTON",
  "SELECT",
  "TEXTAREA",
  "SVG",
  "MATH",
  "IFRAME",
  "AUDIO",
  "VIDEO",
]);

/** Our own injected UI must never be part of the article text index. */
const DASFAX_HOST_TAG = "DASFAX-ROOT";

/** Block-level containers. A boundary between two of these implies a space even
 * when the DOM has no whitespace text node between them (`</p><p>`). */
const BLOCK_TAGS = new Set([
  "P",
  "DIV",
  "SECTION",
  "ARTICLE",
  "MAIN",
  "HEADER",
  "FOOTER",
  "LI",
  "UL",
  "OL",
  "BLOCKQUOTE",
  "PRE",
  "H1",
  "H2",
  "H3",
  "H4",
  "H5",
  "H6",
  "DD",
  "DT",
  "DL",
  "TABLE",
  "TR",
  "TD",
  "TH",
  "FIGURE",
  "BR",
  "HR",
]);

function nearestBlockAncestor(node: Node, root: Element): Element {
  let el: Element | null = node.parentElement;
  while (el && el !== root) {
    if (BLOCK_TAGS.has(el.tagName.toUpperCase())) return el;
    el = el.parentElement;
  }
  return root;
}

const ZERO_WIDTH = new Set([
  "​",
  "‌",
  "‍",
  "⁠",
  "﻿",
  "­",
]);

const QUOTE_DASH: Record<string, string> = {
  "‘": "'",
  "’": "'",
  "‚": "'",
  "‛": "'",
  "′": "'",
  "´": "'",
  "`": "'",
  "“": '"',
  "”": '"',
  "„": '"',
  "‟": '"',
  "″": '"',
  "«": '"',
  "»": '"',
  "‐": "-",
  "‑": "-",
  "‒": "-",
  "–": "-",
  "—": "-",
  "―": "-",
  "−": "-",
};

export interface IndexSegment {
  /** Inclusive start offset of this text node's contribution within `flat`. */
  flatStart: number;
  /** Exclusive end offset within `flat`. */
  flatEnd: number;
  node: Text;
  /**
   * `rawOffsets[i]` is the offset within `node.data` of the raw character that
   * produced `flat[flatStart + i]`. Length === flatEnd - flatStart.
   */
  rawOffsets: number[];
}

export interface TextIndex {
  /** Normalised, whitespace-collapsed concatenation of the article's text. */
  flat: string;
  /** In document order, non-overlapping, ascending `flatStart`. */
  segments: IndexSegment[];
  root: Element;
}

export interface BuildTextIndexOptions {
  /** Extra element tag names (upper-case) to exclude, on top of the defaults. */
  skipTags?: Iterable<string>;
}

function normalizeChar(ch: string): string {
  const mapped = QUOTE_DASH[ch];
  if (mapped !== undefined) return mapped;
  if (ch === "…") return "...";
  // Per-character NFKC. An approximation of full-string NFKC, adequate for
  // article prose; expands the rare ligature/compatibility char to >1 output
  // char, all attributed to the same source offset.
  return ch.normalize("NFKC");
}

function makeFilter(skip: Set<string>): NodeFilter {
  return {
    acceptNode(node: Node): number {
      if (node.nodeType === Node.ELEMENT_NODE) {
        const el = node as Element;
        const tag = el.tagName.toUpperCase();
        if (tag === DASFAX_HOST_TAG || skip.has(tag)) return NodeFilter.FILTER_REJECT;
        if (el.getAttribute("aria-hidden") === "true") return NodeFilter.FILTER_REJECT;
        if (el.hasAttribute("hidden")) return NodeFilter.FILTER_REJECT;
        return NodeFilter.FILTER_SKIP; // descend into it, but it contributes no text itself
      }
      return NodeFilter.FILTER_ACCEPT; // text node
    },
  };
}

/**
 * Build a flattened, normalised text index for `root`'s subtree.
 * Whitespace is collapsed across node boundaries; a leading space is never
 * emitted, so `flat` has no leading/trailing space.
 */
export function buildTextIndex(
  root: Element,
  options: BuildTextIndexOptions = {},
): TextIndex {
  const skip = new Set(DEFAULT_SKIP_TAGS);
  for (const t of options.skipTags ?? []) skip.add(t.toUpperCase());

  const doc = root.ownerDocument;
  const walker = doc.createTreeWalker(
    root,
    NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT,
    makeFilter(skip),
  );

  const segments: IndexSegment[] = [];
  let flat = "";
  // `true` at start so a leading whitespace char is dropped rather than emitted.
  let lastWasSpace = true;
  let prevBlock: Element | null = null;

  for (let node = walker.nextNode(); node !== null; node = walker.nextNode()) {
    if (node.nodeType !== Node.TEXT_NODE) continue;
    const textNode = node as Text;
    const data = textNode.data;
    if (data.length === 0) continue;

    const flatStart = flat.length;
    const rawOffsets: number[] = [];
    let chunk = "";

    // `</p><p>` and friends carry no whitespace text node; synthesise the gap.
    const block = nearestBlockAncestor(textNode, root);
    if (
      prevBlock !== null &&
      block !== prevBlock &&
      flat.length > 0 &&
      !lastWasSpace
    ) {
      chunk += " ";
      rawOffsets.push(0);
      lastWasSpace = true;
    }

    for (let i = 0; i < data.length; i++) {
      const ch = data[i]!;
      if (ZERO_WIDTH.has(ch)) continue;

      if (/\s/.test(ch)) {
        if (lastWasSpace) continue;
        chunk += " ";
        rawOffsets.push(i);
        lastWasSpace = true;
        continue;
      }

      const out = normalizeChar(ch);
      for (let k = 0; k < out.length; k++) {
        chunk += out[k]!;
        rawOffsets.push(i);
      }
      lastWasSpace = false;
    }

    if (chunk.length === 0) continue;
    flat += chunk;
    segments.push({
      flatStart,
      flatEnd: flat.length,
      node: textNode,
      rawOffsets,
    });
    prevBlock = block;
  }

  // A trailing collapsed space would have `lastWasSpace === true` with a space
  // as the final flat char; strip it and fix the owning segment.
  if (flat.endsWith(" ")) {
    flat = flat.slice(0, -1);
    const last = segments[segments.length - 1];
    if (last) {
      last.flatEnd -= 1;
      last.rawOffsets.pop();
      if (last.flatEnd <= last.flatStart) segments.pop();
    }
  }

  return { flat, segments, root };
}

/**
 * Resolve a flat-string offset to its `(text node, offset-within-node)`.
 * `bias: "end"` maps an exclusive end offset to the position just past the last
 * included character. Returns `null` if the offset is out of range.
 */
export function locateOffset(
  index: TextIndex,
  flatOffset: number,
  bias: "start" | "end" = "start",
): { node: Text; offset: number } | null {
  const { segments } = index;
  if (segments.length === 0) return null;

  if (bias === "end") {
    if (flatOffset <= 0) return null;
    const probe = flatOffset - 1;
    for (const seg of segments) {
      if (probe >= seg.flatStart && probe < seg.flatEnd) {
        const raw = seg.rawOffsets[probe - seg.flatStart]!;
        return { node: seg.node, offset: raw + 1 };
      }
    }
    return null;
  }

  for (const seg of segments) {
    if (flatOffset >= seg.flatStart && flatOffset < seg.flatEnd) {
      const raw = seg.rawOffsets[flatOffset - seg.flatStart]!;
      return { node: seg.node, offset: raw };
    }
  }
  // Exactly at the very end.
  const last = segments[segments.length - 1]!;
  if (flatOffset === last.flatEnd) {
    const raw = last.rawOffsets[last.rawOffsets.length - 1]!;
    return { node: last.node, offset: raw + 1 };
  }
  return null;
}
