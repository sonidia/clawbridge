import asyncio
import json
import logging
from typing import Any

from playwright.async_api import Page

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# JavaScript to inject into the page for extracting clean, interactive DOM elements.
# Ignores obfuscated CSS classes and focuses on semantic attributes.
EXTRACT_DOM_JS = """
() => {
    // Clean up old cb_id attributes from previous extraction (prevent accumulation)
    document.querySelectorAll('[data-cb-id]').forEach(el => el.removeAttribute('data-cb-id'));

    let idCounter = 0;

    function generateId() {
        return 'cb_' + (idCounter++);
    }

    function getVisibleText(el) {
        const text = (el.innerText || el.textContent || '').trim();
        // Limit text length to keep payload small
        return text.length > 300 ? text.substring(0, 300) + '...' : text;
    }

    function isVisible(el) {
        try {
            const style = window.getComputedStyle(el);
            if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
            // Use getBoundingClientRect instead of offsetParent (CSS contain:content breaks offsetParent)
            const rect = el.getBoundingClientRect();
            if (rect.width === 0 && rect.height === 0) return false;
            return true;
        } catch(e) { return false; }
    }

    function isInViewport(el) {
        const rect = el.getBoundingClientRect();
        return (
            rect.top < window.innerHeight + 200 &&
            rect.bottom > -200 &&
            rect.left < window.innerWidth + 200 &&
            rect.right > -200
        );
    }

    // Selectors for interactive and content-bearing elements
    const selectors = [
        'a[href]',
        'button',
        'input',
        'textarea',
        'select',
        '[role="button"]',
        '[role="link"]',
        '[role="textbox"]',
        '[role="menuitem"]',
        '[role="tab"]',
        '[role="checkbox"]',
        '[role="radio"]',
        '[role="switch"]',
        '[contenteditable="true"]',
        '[data-testid]',
        // Extension-injected buttons (position:fixed clickable divs)
        '#gemini-aff-btn',
        '#aff-ext-badge',
        'div[style*="position: fixed"][style*="cursor: pointer"]',
        'div[style*="position:fixed"][style*="cursor:pointer"]',
    ];

    // Traverse Shadow DOM to find extension-injected elements
    function querySelectorAllDeep(root, selectorStr) {
        const found = [];
        try {
            found.push(...root.querySelectorAll(selectorStr));
        } catch(e) {}

        // Search inside shadow roots (extensions inject elements here)
        const allEls = root.querySelectorAll('*');
        for (const el of allEls) {
            if (el.shadowRoot) {
                try {
                    found.push(...querySelectorAllDeep(el.shadowRoot, selectorStr));
                } catch(e) {}
            }
        }
        return found;
    }

    const allElements = querySelectorAllDeep(document, selectors.join(','));
    const results = [];
    const seen = new Set();

    for (const el of allElements) {
        if (seen.has(el)) continue;
        if (!isVisible(el) || !isInViewport(el)) continue;
        seen.add(el);

        const uid = generateId();
        // Tag the element in the DOM so Playwright can find it later
        el.setAttribute('data-cb-id', uid);

        const tag = el.tagName.toLowerCase();
        const role = el.getAttribute('role') || '';
        const ariaLabel = el.getAttribute('aria-label') || '';
        const placeholder = el.getAttribute('placeholder') || '';
        const type = el.getAttribute('type') || '';
        const href = tag === 'a' ? (el.getAttribute('href') || '') : '';
        const text = getVisibleText(el);
        const isDisabled = el.disabled || el.getAttribute('aria-disabled') === 'true';
        const isChecked = el.checked || el.getAttribute('aria-checked') === 'true';
        const value = el.value || '';

        const rect = el.getBoundingClientRect();
        const entry = {
            id: uid,
            tag: tag,
            bbox: {
                x: Math.round(rect.x),
                y: Math.round(rect.y),
                w: Math.round(rect.width),
                h: Math.round(rect.height),
            },
        };

        if (role) entry.role = role;
        if (ariaLabel) entry.aria_label = ariaLabel;
        if (text && text !== ariaLabel) entry.text = text;
        if (placeholder) entry.placeholder = placeholder;
        if (type) entry.type = type;
        if (href) entry.href = href.substring(0, 200);
        if (isDisabled) entry.disabled = true;
        if (isChecked) entry.checked = true;
        if (value && tag === 'input') entry.value = value.substring(0, 100);

        results.push(entry);
    }

    // Also extract main feed post texts (Facebook wraps posts in [data-ad-comet-rendering-mode] or div[role="article"])
    const posts = document.querySelectorAll('[role="article"], [data-ad-comet-rendering-mode]');
    for (const post of posts) {
        if (!isVisible(post) || !isInViewport(post)) continue;
        if (seen.has(post)) continue;
        seen.add(post);

        const uid = generateId();
        post.setAttribute('data-cb-id', uid);

        const text = getVisibleText(post);
        if (text.length > 20) {
            results.push({
                id: uid,
                tag: 'article',
                role: 'article',
                text: text
            });
        }
    }

    return {
        url: window.location.href,
        title: document.title,
        viewport: { width: window.innerWidth, height: window.innerHeight },
        scroll_y: window.scrollY,
        element_count: results.length,
        elements: results
    };
}
"""


async def extract_clean_dom(page: Page) -> dict[str, Any]:
    """Extract a clean, minified representation of interactive DOM elements.

    Injects JavaScript into the page to find all interactive elements
    (buttons, links, inputs, textareas, etc.) plus article/post content.
    Each element gets a unique `data-cb-id` attribute set in the DOM
    so it can be targeted later by Playwright.

    Args:
        page: The Playwright Page object.

    Returns:
        A dict containing page metadata and a list of extracted elements.
    """
    logger.info("Extracting clean DOM from: %s", page.url)

    try:
        result = await page.evaluate(EXTRACT_DOM_JS)
    except Exception as e:
        logger.error("DOM extraction failed: %s", e)
        return {
            "url": page.url,
            "title": "",
            "error": str(e),
            "elements": [],
            "element_count": 0,
        }

    logger.info(
        "Extracted %d interactive elements from %s",
        result.get("element_count", 0),
        result.get("url", "unknown"),
    )
    return result


def dom_to_json(dom_data: dict[str, Any], indent: bool = False) -> str:
    """Serialize the extracted DOM data to a compact JSON string.

    Args:
        dom_data: The dict returned by extract_clean_dom().
        indent: If True, pretty-print with indentation.

    Returns:
        A JSON string representation.
    """
    return json.dumps(
        dom_data,
        ensure_ascii=False,
        separators=(",", ":") if not indent else None,
        indent=2 if indent else None,
    )


EXTRACT_PAGE_TEXT_JS = """
() => {
    // =================================================================
    // UNIVERSAL CONTENT EXTRACTION ENGINE
    // Works on ANY website - no hardcoded selectors needed!
    //
    // Strategy:
    // 1. Find repeating content blocks (posts/cards/items) via sibling pattern detection
    // 2. Extract text from each block with smart text density analysis
    // 3. Auto-classify as posts/products/generic based on content patterns
    // 4. Use semantic hints (role, aria, schema.org) as enhancement, not dependency
    // =================================================================

    function isVisible(el) {
        try {
            const style = window.getComputedStyle(el);
            if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
            const rect = el.getBoundingClientRect();
            if (rect.width === 0 && rect.height === 0) return false;
            return true;
        } catch(e) { return false; }
    }

    function getText(el) {
        if (!el) return '';
        return (el.innerText || el.textContent || '').trim();
    }

    // ---- PHASE 1: Find repeating content blocks (universal) ----
    // This finds groups of similar sibling elements = content feed/list
    // Works on Facebook posts, Shopee products, news articles, forums, etc.

    function findRepeatingBlocks() {
        const candidates = [];
        const containers = document.querySelectorAll('main, [role="main"], [role="feed"], #content, .content, article, [role="article"]');
        const searchRoots = containers.length > 0 ? containers : [document.body];

        for (const root of searchRoots) {
            // Walk through children looking for groups of similar elements
            const childMap = new Map(); // tagName+role+className_pattern -> [elements]

            function analyzeChildren(parent, depth) {
                if (depth > 6) return;
                const children = parent.children;
                if (!children || children.length < 1) return;

                // Group children by their "shape" (tag + role + rough class pattern)
                const groups = new Map();
                for (const child of children) {
                    if (!isVisible(child)) continue;
                    const text = getText(child);
                    if (text.length < 15) continue; // Skip tiny elements

                    const tag = child.tagName.toLowerCase();
                    const role = child.getAttribute('role') || '';
                    const ariaLabel = child.getAttribute('aria-label') || '';
                    // Normalize class to pattern (strip unique IDs/hashes)
                    const cls = (child.className && typeof child.className === 'string')
                        ? child.className.replace(/[a-f0-9]{6,}/gi, '*').replace(/\\d+/g, '#').substring(0, 60)
                        : '';

                    const shape = tag + '|' + role + '|' + cls;
                    if (!groups.has(shape)) groups.set(shape, []);
                    groups.get(shape).push(child);
                }

                // A group with 2+ similar elements = content feed!
                for (const [shape, els] of groups) {
                    if (els.length >= 2) {
                        // Score: more elements = more likely a feed
                        candidates.push({ parent, els, shape, score: els.length, depth });
                    }
                }

                // Recurse into children to find nested feeds
                for (const child of children) {
                    if (child.children && child.children.length > 1) {
                        analyzeChildren(child, depth + 1);
                    }
                }
            }

            analyzeChildren(root, 0);
        }

        // Also check role="article" elements directly (semantic hint)
        const articles = document.querySelectorAll('[role="article"]');
        if (articles.length >= 2) {
            candidates.push({
                parent: articles[0].parentElement,
                els: Array.from(articles),
                shape: 'article_role',
                score: articles.length * 2,
                depth: 0
            });
        }

        // Sort by score (most elements first), prefer shallower depth
        candidates.sort((a, b) => (b.score - a.score) || (a.depth - b.depth));
        return candidates;
    }

    // ---- PHASE 2: Extract content from each block ----

    function extractBlockContent(el) {
        const result = {
            text: '',
            author: '',
            timestamp: '',
            link: '',
            price: '',
            image: '',
            meta: {}
        };

        // Author detection (universal patterns)
        const authorHints = el.querySelectorAll(
            'a[role="link"] strong, h2 a, h3 a, h4 a, strong a, a strong, ' +
            '[class*="author"], [class*="Author"], [class*="user"], [class*="User"], ' +
            '[class*="name"][class*="profile"], [itemprop="author"], ' +
            '[data-ad-rendering-role="profile_name"], a[href*="/user"], a[href*="/profile"]'
        );
        for (const ah of authorHints) {
            const a = getText(ah);
            if (a && a.length > 1 && a.length < 80) { result.author = a; break; }
        }

        // Timestamp detection (universal)
        const timeHints = el.querySelectorAll(
            'time, abbr, [datetime], [data-utime], [class*="time"], [class*="date"], ' +
            '[class*="Time"], [class*="Date"], [class*="ago"], [class*="posted"], ' +
            '[itemprop="datePublished"], [itemprop="dateCreated"]'
        );
        for (const th of timeHints) {
            const ts = (th.getAttribute('datetime') || th.getAttribute('aria-label') ||
                        th.getAttribute('title') || getText(th)).trim();
            if (ts && ts.length > 1 && ts.length < 100) { result.timestamp = ts; break; }
        }

        // Price detection (universal - currency symbols & patterns)
        const pricePattern = /[₫$€£¥₩]\\s*[\\d.,]+|[\\d.,]+\\s*[₫đ]|\\d{1,3}([.,]\\d{3})+/;
        const allSpans = el.querySelectorAll('span, div, p, strong, b');
        for (const sp of allSpans) {
            const t = getText(sp);
            if (t.length < 30 && pricePattern.test(t)) {
                result.price = t;
                break;
            }
        }

        // Link detection
        const linkEl = el.tagName === 'A' ? el : el.querySelector('a[href]:not([href="#"]):not([href="javascript:void(0)"])');
        if (linkEl) result.link = (linkEl.getAttribute('href') || '').substring(0, 400);

        // Image detection
        const imgEl = el.querySelector('img[src]:not([src*="emoji"]):not([src*="icon"])');
        if (imgEl) result.image = (imgEl.getAttribute('alt') || '').substring(0, 200);

        // Main text extraction (multiple strategies)
        const seenLocal = new Set();

        // Strategy A: dir="auto" blocks (works for React/Facebook editors)
        const dirAutoEls = el.querySelectorAll('div[dir="auto"], span[dir="auto"], p[dir="auto"]');
        const dirTexts = [];
        for (const d of dirAutoEls) {
            const t = getText(d);
            if (t.length > 3 && !seenLocal.has(t)) {
                seenLocal.add(t);
                dirTexts.push(t);
            }
        }
        if (dirTexts.join('\\n').length > 10) {
            result.text = dirTexts.join('\\n');
            return result;
        }

        // Strategy B: Paragraph/content elements
        const contentEls = el.querySelectorAll('p, [class*="content"], [class*="Content"], [class*="body"], [class*="Body"], [class*="text"], [class*="Text"], [class*="description"], [class*="Description"], blockquote');
        const contentTexts = [];
        for (const ce of contentEls) {
            const t = getText(ce);
            if (t.length > 10 && !seenLocal.has(t)) {
                seenLocal.add(t);
                contentTexts.push(t);
            }
        }
        if (contentTexts.join('\\n').length > 10) {
            result.text = contentTexts.join('\\n');
            return result;
        }

        // Strategy C: Get element's own text (last resort)
        const rawText = getText(el);
        if (rawText.length > 15) {
            // Remove author/timestamp from the text to get clean content
            let cleanText = rawText;
            if (result.author) cleanText = cleanText.replace(result.author, '');
            if (result.timestamp) cleanText = cleanText.replace(result.timestamp, '');
            // Split into lines, take meaningful ones
            const lines = cleanText.split('\\n').map(l => l.trim()).filter(l => l.length > 3);
            result.text = lines.slice(0, 40).join('\\n');
        }

        return result;
    }

    // ---- PHASE 3: Auto-classify content type ----

    function classifyContent(blocks) {
        if (blocks.length === 0) return 'generic';

        let hasPrice = 0, hasAuthor = 0, hasLongText = 0;
        for (const b of blocks) {
            if (b.price) hasPrice++;
            if (b.author) hasAuthor++;
            if (b.text.length > 50) hasLongText++;
        }

        const total = blocks.length;
        if (hasPrice / total > 0.3) return 'products';
        if (hasAuthor / total > 0.3 || hasLongText / total > 0.5) return 'posts';
        return 'generic';
    }

    // ---- EXECUTE ----

    const repeatingGroups = findRepeatingBlocks();
    const posts = [];
    const products = [];
    const seenTexts = new Set();
    let postId = 0;
    let prodId = 0;

    // Process the best repeating group(s)
    const processedEls = new Set();
    for (const group of repeatingGroups.slice(0, 3)) {
        const blocks = [];
        for (const el of group.els) {
            if (processedEls.has(el)) continue;
            processedEls.add(el);

            const content = extractBlockContent(el);
            if (content.text.length > 5 || content.author || content.price) {
                blocks.push(content);
            }
        }

        if (blocks.length === 0) continue;

        const contentType = classifyContent(blocks);

        for (const b of blocks) {
            if (seenTexts.has(b.text.substring(0, 100))) continue;
            if (b.text.length > 5) seenTexts.add(b.text.substring(0, 100));

            if (contentType === 'products' || b.price) {
                products.push({
                    product_id: 'prod_' + (prodId++),
                    name: (b.text || b.image || '').substring(0, 300),
                    price: b.price,
                    rating: '',
                    link: b.link,
                });
            } else {
                posts.push({
                    post_id: 'post_' + (postId++),
                    author: b.author,
                    timestamp: b.timestamp,
                    text: b.text.substring(0, 2000),
                    reactions: '',
                    comments: '',
                    link: b.link,
                });
            }
        }
    }

    // ---- PHASE 4: Reaction/comment enhancement for posts (universal) ----
    // Look for engagement metrics near post elements
    if (posts.length > 0) {
        const articleEls = document.querySelectorAll('[role="article"]');
        let idx = 0;
        for (const art of articleEls) {
            if (idx >= posts.length) break;
            // Reactions (aria-label patterns)
            const reactionEl = art.querySelector('[aria-label*="reaction"], [aria-label*="like"], [aria-label*="thích"], [aria-label*="cảm xúc"], [aria-label*="people"]');
            if (reactionEl) posts[idx].reactions = (reactionEl.getAttribute('aria-label') || getText(reactionEl)).trim();
            // Comments
            const spans = art.querySelectorAll('span, a');
            for (const sp of spans) {
                const t = getText(sp);
                if (t && /\\d+\\s*(comment|bình luận|phản hồi|repl)/i.test(t)) {
                    posts[idx].comments = t;
                    break;
                }
            }
            idx++;
        }
    }

    // ---- PHASE 5: Fallback text blocks (when no structured content found) ----
    const allTextBlocks = [];
    if (posts.length === 0 && products.length === 0) {
        // Walk the DOM for any meaningful text blocks
        const walker = document.createTreeWalker(
            document.body,
            NodeFilter.SHOW_ELEMENT,
            {
                acceptNode: function(node) {
                    if (!isVisible(node)) return NodeFilter.FILTER_REJECT;
                    const tag = node.tagName.toLowerCase();
                    if (['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'td', 'th',
                         'article', 'section', 'blockquote', 'figcaption', 'pre', 'code'].includes(tag) ||
                        (tag === 'div' && node.children.length < 5) ||
                        (tag === 'span' && node.children.length < 3)) {
                        const text = (node.innerText || '').trim();
                        if (text.length > 10 && text.length < 5000 && !seenTexts.has(text)) {
                            if (node.children.length < 5 || text.length < 300) {
                                return NodeFilter.FILTER_ACCEPT;
                            }
                        }
                    }
                    return NodeFilter.FILTER_SKIP;
                }
            }
        );

        let node;
        while (node = walker.nextNode()) {
            const text = (node.innerText || '').trim();
            if (text && !seenTexts.has(text)) {
                seenTexts.add(text);
                allTextBlocks.push(text.substring(0, 500));
            }
            if (allTextBlocks.length >= 50) break;
        }
    }

    return {
        url: window.location.href,
        title: document.title,
        viewport: { width: window.innerWidth, height: window.innerHeight },
        scroll_y: window.scrollY,
        post_count: posts.length,
        posts: posts,
        product_count: products.length,
        products: products,
        text_blocks: allTextBlocks,
    };
}
"""


async def extract_page_text(page: Page) -> dict[str, Any]:
    """Extract all visible text content from the page, including Facebook post content.

    Unlike extract_clean_dom() which focuses on interactive elements,
    this function extracts the actual text content of posts, articles,
    and other visible text blocks on the page.

    For Facebook and other dynamic sites, waits for content to render before extraction.

    Returns:
        A dict with posts (author, text, reactions, comments) and text_blocks.
    """
    logger.info("Extracting page text from: %s", page.url)

    # Universal: wait for dynamic content to render on JS-heavy sites
    # Instead of checking specific domains, wait for page to stabilize
    try:
        # Wait for network to be mostly idle (dynamic content loaded)
        await page.wait_for_load_state("networkidle", timeout=5000)
    except Exception:
        pass  # Timeout is fine - some sites never go fully idle

    # Extra wait: check if DOM is still changing (JS rendering content)
    try:
        size_before = await page.evaluate("document.body.innerHTML.length")
        await asyncio.sleep(1.0)
        size_after = await page.evaluate("document.body.innerHTML.length")
        if size_after > size_before * 1.1:
            # Content is still loading, wait a bit more
            await asyncio.sleep(1.5)
            logger.info("Dynamic content detected (DOM grew %d→%d), waited extra", size_before, size_after)
    except Exception:
        pass

    try:
        result = await page.evaluate(EXTRACT_PAGE_TEXT_JS)
    except Exception as e:
        logger.error("Page text extraction failed: %s", e)
        return {
            "url": page.url,
            "error": str(e),
            "posts": [],
            "text_blocks": [],
        }

    logger.info(
        "Extracted %d posts, %d text blocks from %s",
        result.get("post_count", 0),
        len(result.get("text_blocks", [])),
        result.get("url", "unknown"),
    )
    return result


async def extract_snapshot(page: Page) -> dict[str, Any]:
    """Extract a full page snapshot: interactive elements + text content + accessibility info.

    Combines extract_clean_dom (interactive elements) with extract_page_text
    (content text) for a complete picture of the page.

    Returns:
        A dict with elements, posts, text_blocks, and page metadata.
    """
    logger.info("Taking full snapshot of: %s", page.url)

    dom_data = await extract_clean_dom(page)
    text_data = await extract_page_text(page)

    return {
        "url": dom_data.get("url", page.url),
        "title": dom_data.get("title", ""),
        "viewport": dom_data.get("viewport", {}),
        "scroll_y": dom_data.get("scroll_y", 0),
        "element_count": dom_data.get("element_count", 0),
        "elements": dom_data.get("elements", []),
        "post_count": text_data.get("post_count", 0),
        "posts": text_data.get("posts", []),
        "product_count": text_data.get("product_count", 0),
        "products": text_data.get("products", []),
        "text_blocks": text_data.get("text_blocks", []),
    }


FIND_BY_TEXT_JS = """
(searchText) => {
    const results = [];
    const searchLower = searchText.toLowerCase();

    function isVisible(el) {
        try {
            const style = window.getComputedStyle(el);
            if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
            // Use getBoundingClientRect instead of offsetParent (Facebook CSS breaks offsetParent)
            const rect = el.getBoundingClientRect();
            if (rect.width === 0 && rect.height === 0) return false;
            return true;
        } catch(e) { return false; }
    }

    function searchInRoot(root) {
        // Search all elements including shadow DOM
        const allEls = root.querySelectorAll('*');
        for (const el of allEls) {
            // Check this element's text
            const text = (el.innerText || el.textContent || '').trim();
            if (text.toLowerCase().includes(searchLower) && isVisible(el)) {
                const rect = el.getBoundingClientRect();
                // Prefer smaller/more specific elements (leaf nodes)
                if (el.children.length < 5 || text.length < 100) {
                    results.push({
                        tag: el.tagName.toLowerCase(),
                        text: text.substring(0, 200),
                        bbox: {
                            x: Math.round(rect.x),
                            y: Math.round(rect.y),
                            w: Math.round(rect.width),
                            h: Math.round(rect.height),
                        },
                        // Center point for clicking
                        center_x: Math.round(rect.x + rect.width / 2),
                        center_y: Math.round(rect.y + rect.height / 2),
                    });
                }
            }

            // Search inside shadow roots (extension-injected elements)
            if (el.shadowRoot) {
                searchInRoot(el.shadowRoot);
            }
        }
    }

    searchInRoot(document);

    // Sort by size (smallest first = most specific match)
    results.sort((a, b) => (a.bbox.w * a.bbox.h) - (b.bbox.w * b.bbox.h));

    return {
        search_text: searchText,
        found: results.length,
        matches: results.slice(0, 10),
    };
}
"""


async def find_by_text(page: Page, search_text: str) -> dict[str, Any]:
    """Find all visible elements containing the given text.

    Uses multiple strategies to find elements:
    1. Main page DOM + Shadow DOM traversal
    2. All iframes/frames on the page
    3. Playwright native locator (get_by_text)

    Args:
        page: The Playwright Page object.
        search_text: Text to search for (case-insensitive).

    Returns:
        A dict with matches, each containing bbox and center coordinates.
    """
    logger.info("Finding elements by text: '%s'", search_text)
    all_matches = []

    # Strategy 1: Main page JS (includes Shadow DOM)
    try:
        result = await page.evaluate(FIND_BY_TEXT_JS, search_text)
        for m in result.get("matches", []):
            m["source"] = "main_page"
            all_matches.append(m)
    except Exception as e:
        logger.warning("Strategy 1 (main page JS) failed: %s", e)

    # Strategy 2: Search all iframes/frames
    try:
        for i, frame in enumerate(page.frames):
            if frame == page.main_frame:
                continue
            try:
                frame_result = await frame.evaluate(FIND_BY_TEXT_JS, search_text)
                for m in frame_result.get("matches", []):
                    m["source"] = f"frame_{i}"
                    all_matches.append(m)
            except Exception:
                pass
    except Exception as e:
        logger.warning("Strategy 2 (frames) failed: %s", e)

    # Strategy 3: Playwright native locator (handles more edge cases)
    try:
        locator = page.get_by_text(search_text, exact=False)
        count = await locator.count()
        for idx in range(min(count, 5)):
            try:
                el = locator.nth(idx)
                if await el.is_visible():
                    bbox = await el.bounding_box()
                    if bbox:
                        text_content = await el.text_content() or ""
                        all_matches.append({
                            "tag": await el.evaluate("el => el.tagName.toLowerCase()"),
                            "text": text_content.strip()[:200],
                            "bbox": {
                                "x": round(bbox["x"]),
                                "y": round(bbox["y"]),
                                "w": round(bbox["width"]),
                                "h": round(bbox["height"]),
                            },
                            "center_x": round(bbox["x"] + bbox["width"] / 2),
                            "center_y": round(bbox["y"] + bbox["height"] / 2),
                            "source": "playwright_locator",
                        })
            except Exception:
                pass
    except Exception as e:
        logger.warning("Strategy 3 (Playwright locator) failed: %s", e)

    # Deduplicate by center coordinates (within 10px)
    unique = []
    for m in all_matches:
        is_dup = False
        for u in unique:
            if abs(m.get("center_x", 0) - u.get("center_x", 0)) < 10 and \
               abs(m.get("center_y", 0) - u.get("center_y", 0)) < 10:
                is_dup = True
                break
        if not is_dup:
            unique.append(m)

    # Sort by size (smallest first = most specific)
    unique.sort(key=lambda m: m.get("bbox", {}).get("w", 9999) * m.get("bbox", {}).get("h", 9999))

    logger.info("Found %d unique matches for '%s'", len(unique), search_text)
    return {
        "search_text": search_text,
        "found": len(unique),
        "matches": unique[:10],
    }


WAIT_FOR_LAZY_CONTENT_JS = """
(scrollCount) => {
    return new Promise((resolve) => {
        const before = document.body.innerHTML.length;
        let scrollsDone = 0;
        const maxScrolls = scrollCount || 3;

        function doScroll() {
            window.scrollBy(0, window.innerHeight * 0.7);
            scrollsDone++;

            if (scrollsDone >= maxScrolls) {
                // Wait a bit for final content to load
                setTimeout(() => {
                    const after = document.body.innerHTML.length;
                    resolve({
                        scrolls_done: scrollsDone,
                        content_before: before,
                        content_after: after,
                        new_content_loaded: after > before,
                        scroll_y: window.scrollY,
                        page_height: document.body.scrollHeight,
                    });
                }, 2000);
            } else {
                // Wait between scrolls for lazy content to load
                setTimeout(doScroll, 1500);
            }
        }

        doScroll();
    });
}
"""


async def wait_for_lazy_content(page: Page, scroll_count: int = 3) -> dict[str, Any]:
    """Scroll down the page multiple times and wait for lazy-loaded content.

    Sites like Shopee, Facebook use lazy loading / infinite scroll.
    This function scrolls incrementally and waits between scrolls
    for new content to appear.

    Args:
        page: The Playwright Page object.
        scroll_count: Number of scroll increments (default 3).

    Returns:
        A dict with scroll info and whether new content was loaded.
    """
    logger.info("Waiting for lazy content (scrolling %d times)...", scroll_count)

    try:
        result = await page.evaluate(WAIT_FOR_LAZY_CONTENT_JS, scroll_count)
        logger.info(
            "Lazy scroll done: %d scrolls, content %s→%s bytes (new=%s)",
            result.get("scrolls_done", 0),
            result.get("content_before", 0),
            result.get("content_after", 0),
            result.get("new_content_loaded", False),
        )
        return result
    except Exception as e:
        logger.error("Lazy scroll failed: %s", e)
        return {"error": str(e), "scrolls_done": 0, "new_content_loaded": False}


async def scroll_and_snapshot(page: Page, scroll_count: int = 3) -> dict[str, Any]:
    """Scroll to load lazy content, then take a full snapshot.

    This is the ultimate endpoint for lazy-loading sites like Shopee/Facebook.
    It scrolls down multiple times, waits for content to load, then extracts
    both interactive elements and text content.

    Args:
        page: The Playwright Page object.
        scroll_count: Number of scroll increments before snapshot.

    Returns:
        Full snapshot data including scroll info.
    """
    scroll_info = await wait_for_lazy_content(page, scroll_count)
    snapshot = await extract_snapshot(page)
    snapshot["scroll_info"] = scroll_info
    return snapshot


async def find_element_by_cb_id(page: Page, cb_id: str):
    """Locate a page element by its ClawBridge unique ID (data-cb-id).

    Args:
        page: The Playwright Page object.
        cb_id: The unique ID assigned during DOM extraction.

    Returns:
        An ElementHandle, or None if not found.
    """
    selector = f'[data-cb-id="{cb_id}"]'
    try:
        element = await page.query_selector(selector)
        return element
    except Exception as e:
        logger.warning("Could not find element with cb_id=%s: %s", cb_id, e)
        return None


if __name__ == "__main__":
    # Quick test: connect to an already-running AdsPower profile and extract DOM
    from browser_manager import start_adspower_profile, connect_playwright, stop_adspower_profile
    from config import get_settings

    async def test_dom_extraction():
        settings = get_settings()
        profile_id = settings.adspower_profile_id

        ws_endpoint = await start_adspower_profile(profile_id)
        browser, context, page = await connect_playwright(ws_endpoint)

        try:
            await page.goto("https://facebook.com", wait_until="domcontentloaded", timeout=30000)
            # Wait a moment for dynamic content to load
            await asyncio.sleep(3)

            dom_data = await extract_clean_dom(page)
            print(dom_to_json(dom_data, indent=True))
        finally:
            await browser.close()
            await stop_adspower_profile(profile_id)

    asyncio.run(test_dom_extraction())
