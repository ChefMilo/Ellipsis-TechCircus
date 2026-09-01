import { Readability } from "@mozilla/readability";

/**
 * DRAFT contract with the backend team (WS3). Flags below mark spots where
 * their input would change the shape or content of this object:
 *
 * - `whitelisted` is always `false` in objects this module actually
 *   produces, because the content script only calls extractArticle() on
 *   the "not_whitelisted AND isProbablyArticle" branch -- whitelisted pages
 *   short-circuit before Tier 1/2 ever run (see bootstrap.ts). If the
 *   backend wants extracted text from whitelisted articles too (e.g. to
 *   build a corroboration corpus), that wiring needs to change.
 * - `publishDate` is normalized to ISO 8601 (via `Date.toISOString()`) when
 *   parseable, and `null` otherwise -- rather than forwarding an unparseable
 *   human-readable string that would fail WS3's `ArticleInput.published_at:
 *   datetime | None` validation. Locked with WS3 2026-09-01.
 * - `bodyText` is a single flattened string (paragraph breaks collapsed to
 *   single spaces). Flag if claim extraction (WS5) would rather receive an
 *   array of paragraphs.
 * - `imageUrls` filtering is heuristic (see MIN_IMAGE_DIMENSION_PX below)
 *   and only catches size-based tracking pixels/icons when width/height
 *   attributes are present in markup -- many responsive images have
 *   neither and will pass through unfiltered.
 */
export interface ExtractedArticle {
  url: string;
  title: string;
  bodyText: string;
  publishDate: string | null;
  author: string | null;
  /** Hostname the article was extracted from, e.g. "straitstimes.com". */
  sourceDomain: string | null;
  imageUrls: string[];
  whitelisted: boolean;
  articleConfidence: number;
}

const MIN_IMAGE_DIMENSION_PX = 100;

const TRACKING_OR_ICON_PATTERNS = [
  /\b1x1\b/i,
  /pixel\.(gif|png|jpg)/i,
  /spacer\.(gif|png)/i,
  /blank\.(gif|png)/i,
  /transparent\.(gif|png)/i,
  /[/_-]track(ing)?[/._-]/i,
  /beacon/i,
  /doubleclick\.net/i,
  /googlesyndication/i,
  /facebook\.com\/tr/i,
  /\/favicon/i,
  /sprite/i,
];

const FALLBACK_BYLINE_SELECTOR = '[class*="byline" i], [class*="author" i], [rel="author"]';
const FALLBACK_DATE_SELECTOR = 'time[datetime], [class*="publish" i], [class*="posted-on" i]';

function cleanText(value: string | null | undefined): string | null {
  if (!value) {
    return null;
  }
  const trimmed = value.replace(/\s+/g, " ").trim();
  return trimmed.length > 0 ? trimmed : null;
}

function resolveUrl(raw: string, baseUrl: string): string | null {
  try {
    return new URL(raw, baseUrl).toString();
  } catch {
    return null;
  }
}

function hostnameOf(url: string): string | null {
  try {
    return new URL(url).hostname.toLowerCase() || null;
  } catch {
    return null;
  }
}

/**
 * Normalizes a date string to ISO 8601, or null if unparseable. Backend
 * ArticleInput.published_at is a strict `datetime | None` -- sending an
 * unparseable string would fail validation, so an unparseable date is
 * dropped rather than forwarded as-is.
 */
function normalizeToIso8601(value: string | null): string | null {
  if (!value) {
    return null;
  }
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? null : parsed.toISOString();
}

function looksLikeTrackingOrIcon(
  rawSrc: string,
  widthAttr: string | null,
  heightAttr: string | null
): boolean {
  if (rawSrc.startsWith("data:")) {
    return true; // inline placeholder/blur-up thumbnail, not a real content image
  }
  if (/\.svg(\?|#|$)/i.test(rawSrc)) {
    // Editorial photos are essentially never delivered as inline SVG; in
    // practice this is always UI chrome (close/chevron icons, logos,
    // watermarks) -- confirmed by a "close.svg" theme icon leaking through
    // on a real CNA article during manual testing.
    return true;
  }
  const width = widthAttr ? Number(widthAttr) : NaN;
  const height = heightAttr ? Number(heightAttr) : NaN;
  if (
    (!Number.isNaN(width) && width > 0 && width < MIN_IMAGE_DIMENSION_PX) ||
    (!Number.isNaN(height) && height > 0 && height < MIN_IMAGE_DIMENSION_PX)
  ) {
    return true;
  }
  return TRACKING_OR_ICON_PATTERNS.some((pattern) => pattern.test(rawSrc));
}

/**
 * Collects <img> URLs from Readability's already-isolated article content
 * (so header/footer/ad images are excluded by construction), deduped and
 * filtered for obvious icons/tracking pixels.
 */
function extractImageUrls(articleContentHtml: string, baseUrl: string): string[] {
  const parsed = new DOMParser().parseFromString(articleContentHtml, "text/html");
  const seen = new Set<string>();
  const urls: string[] = [];

  for (const img of Array.from(parsed.querySelectorAll("img"))) {
    const rawSrc = img.getAttribute("src");
    if (!rawSrc) {
      continue;
    }
    if (looksLikeTrackingOrIcon(rawSrc, img.getAttribute("width"), img.getAttribute("height"))) {
      continue;
    }
    const resolved = resolveUrl(rawSrc, baseUrl);
    if (!resolved || seen.has(resolved)) {
      continue;
    }
    seen.add(resolved);
    urls.push(resolved);
  }
  return urls;
}

function fallbackAuthor(doc: Document): string | null {
  return cleanText(doc.querySelector(FALLBACK_BYLINE_SELECTOR)?.textContent ?? null);
}

function fallbackDate(doc: Document): string | null {
  const timeEl = doc.querySelector("time[datetime]");
  if (timeEl) {
    return cleanText(timeEl.getAttribute("datetime")) ?? cleanText(timeEl.textContent);
  }
  return cleanText(doc.querySelector(FALLBACK_DATE_SELECTOR)?.textContent ?? null);
}

export interface ExtractArticleOptions {
  whitelisted: boolean;
  articleConfidence: number;
}

/**
 * Extracts title/body/date/author/images from the current page. Intended
 * to run only after isProbablyArticle() has already returned true -- this
 * function does no page-type detection of its own. 100% local: no network
 * calls, nothing sent anywhere.
 */
export function extractArticle(
  document: Document,
  url: string,
  options: ExtractArticleOptions
): ExtractedArticle | null {
  let parsed: ReturnType<Readability["parse"]> | null;
  try {
    // Parse a clone so Readability's DOM mutations don't alter the live page.
    parsed = new Readability(document.cloneNode(true) as Document).parse();
  } catch {
    parsed = null;
  }

  if (!parsed || !cleanText(parsed.textContent)) {
    return null;
  }

  return {
    url,
    title: cleanText(parsed.title) ?? cleanText(document.title) ?? "",
    bodyText: cleanText(parsed.textContent) ?? "",
    publishDate: normalizeToIso8601(cleanText(parsed.publishedTime) ?? fallbackDate(document)),
    author: cleanText(parsed.byline) ?? fallbackAuthor(document),
    sourceDomain: hostnameOf(url),
    imageUrls: parsed.content ? extractImageUrls(parsed.content, url) : [],
    whitelisted: options.whitelisted,
    articleConfidence: options.articleConfidence,
  };
}
