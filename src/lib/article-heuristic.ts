import { isProbablyReaderable } from "@mozilla/readability";

export interface ArticleHeuristicResult {
  isArticle: boolean;
  confidence: number;
}

/**
 * Confidence threshold used to derive the boolean `isArticle` from the
 * numeric score. Exported so the extraction/API-call layer (or a future
 * options UI) can tune it without duplicating the constant.
 */
export const ARTICLE_CONFIDENCE_THRESHOLD = 0.5;

const MIN_PARAGRAPH_CHARS = 40;
const SUBSTANTIAL_PARAGRAPH_TARGET = 6;
const GOOD_AVG_PARAGRAPH_CHARS = 120;
const GOOD_TEXT_TO_MARKUP_RATIO = 0.25;
const PRODUCT_GRID_MATCH_THRESHOLD = 4;
const CHAT_UI_MATCH_THRESHOLD = 2;
const VIDEO_DOMINANT_MAX_PARAGRAPHS = 3;

const BYLINE_DATE_SELECTOR = [
  '[class*="byline" i]',
  '[class*="author" i]',
  '[rel="author"]',
  "time",
  '[class*="publish" i]',
  '[class*="posted-on" i]',
].join(", ");

const BYLINE_META_SELECTOR = [
  'meta[property="article:author"]',
  'meta[property="article:published_time"]',
  'meta[name="author"]',
  'meta[name="date"]',
  'meta[name="publish-date"]',
].join(", ");

const PRODUCT_GRID_SELECTOR = [
  '[class*="product" i]',
  '[class*="add-to-cart" i]',
  '[class*="price" i]',
  '[id*="product" i]',
].join(", ");

const CHAT_UI_SELECTOR = [
  '[class*="inbox" i]',
  '[class*="chat-" i]',
  '[class*="chatwindow" i]',
  '[class*="conversation" i]',
  '[class*="message-list" i]',
  '[class*="thread-list" i]',
].join(", ");

const VIDEO_SELECTOR = [
  "video",
  'iframe[src*="youtube" i]',
  'iframe[src*="youtube-nocookie" i]',
  'iframe[src*="vimeo" i]',
  'iframe[src*="player" i]',
].join(", ");

function getSubstantialParagraphs(doc: Document): HTMLElement[] {
  return Array.from(doc.querySelectorAll("p")).filter(
    (p) => (p.textContent ?? "").trim().length >= MIN_PARAGRAPH_CHARS
  ) as HTMLElement[];
}

function textToMarkupRatio(doc: Document): number {
  const bodyHtml = doc.body?.innerHTML ?? "";
  const bodyText = doc.body?.textContent ?? "";
  if (bodyHtml.length === 0) {
    return 0;
  }
  return bodyText.trim().length / bodyHtml.length;
}

function hasVideoPlayerDominantLayout(doc: Document, paragraphCount: number): boolean {
  const videoCount = doc.querySelectorAll(VIDEO_SELECTOR).length;
  // A big embedded player with hardly any body copy reads as "watch this",
  // not "read this" -- even on a page that happens to wrap things in <article>.
  return videoCount > 0 && paragraphCount < VIDEO_DOMINANT_MAX_PARAGRAPHS;
}

/**
 * Tier 1 article-detection heuristic. Runs entirely client-side against the
 * live DOM -- no network calls. Scores several independent signals and
 * combines them into a 0-1 confidence, rather than returning a bare
 * boolean, so the threshold can be tuned later without re-deriving signals.
 */
export function isProbablyArticle(document: Document): ArticleHeuristicResult {
  let score = 0;

  // Positive signals
  const hasArticleTag = document.querySelector("article") !== null;
  if (hasArticleTag) {
    score += 0.2;
  }

  const paragraphs = getSubstantialParagraphs(document);
  score += Math.min(paragraphs.length / SUBSTANTIAL_PARAGRAPH_TARGET, 1) * 0.25;

  const avgParagraphLength =
    paragraphs.length > 0
      ? paragraphs.reduce((sum, p) => sum + (p.textContent ?? "").trim().length, 0) /
        paragraphs.length
      : 0;
  score += Math.min(avgParagraphLength / GOOD_AVG_PARAGRAPH_CHARS, 1) * 0.15;

  const ratio = textToMarkupRatio(document);
  score += Math.min(ratio / GOOD_TEXT_TO_MARKUP_RATIO, 1) * 0.15;

  const hasByline =
    document.querySelector(BYLINE_DATE_SELECTOR) !== null ||
    document.querySelector(BYLINE_META_SELECTOR) !== null;
  if (hasByline) {
    score += 0.15;
  }

  let readerable = false;
  try {
    readerable = isProbablyReaderable(document);
  } catch {
    // Readability can throw on pathological documents; treat as "no signal".
    readerable = false;
  }
  if (readerable) {
    score += 0.1;
  }

  // Negative signals -- typical non-article layouts.
  const hasProductGrid =
    document.querySelectorAll(PRODUCT_GRID_SELECTOR).length >= PRODUCT_GRID_MATCH_THRESHOLD;
  if (hasProductGrid) {
    score -= 0.3;
  }

  if (hasVideoPlayerDominantLayout(document, paragraphs.length)) {
    score -= 0.3;
  }

  const hasChatUi =
    document.querySelectorAll(CHAT_UI_SELECTOR).length >= CHAT_UI_MATCH_THRESHOLD;
  if (hasChatUi) {
    score -= 0.3;
  }

  const confidence = Math.max(0, Math.min(1, score));

  return {
    isArticle: confidence >= ARTICLE_CONFIDENCE_THRESHOLD,
    confidence,
  };
}
