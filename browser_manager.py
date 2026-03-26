import asyncio
import logging

import aiohttp
from playwright.async_api import async_playwright, Browser, BrowserContext, Page

from config import get_settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


async def start_adspower_profile(profile_id: str) -> str:
    """Start an AdsPower browser profile and return its WebSocket (CDP) endpoint.

    Args:
        profile_id: The AdsPower profile/user ID to launch.

    Returns:
        The puppeteer WebSocket endpoint URL for CDP connection.

    Raises:
        RuntimeError: If AdsPower API returns an error or is unreachable.
    """
    settings = get_settings()
    url = f"{settings.adspower_api_url}/api/v1/browser/start?user_id={profile_id}"

    # AdsPower Global requires API key in Authorization header
    headers = {}
    if settings.adspower_api_key:
        headers["Authorization"] = f"Bearer {settings.adspower_api_key}"

    logger.info("Starting AdsPower profile: %s", profile_id)

    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                data = await resp.json()
        except aiohttp.ClientError as e:
            raise RuntimeError(
                f"Cannot connect to AdsPower API at {settings.adspower_api_url}. "
                f"Make sure AdsPower is running. Error: {e}"
            ) from e

    if data.get("code") != 0:
        raise RuntimeError(
            f"AdsPower API error: {data.get('msg', 'Unknown error')} "
            f"(code={data.get('code')})"
        )

    ws_endpoint = data.get("data", {}).get("ws", {}).get("puppeteer")
    if not ws_endpoint:
        raise RuntimeError(
            "AdsPower API response missing ws.puppeteer endpoint. "
            f"Full response: {data}"
        )

    logger.info("Got WebSocket endpoint: %s", ws_endpoint)
    return ws_endpoint


# Module-level Playwright instance for proper cleanup
_playwright_instance = None


async def connect_playwright(ws_endpoint: str) -> tuple[Browser, BrowserContext, Page]:
    """Connect Playwright to an existing browser via CDP WebSocket.

    Args:
        ws_endpoint: The Chrome DevTools Protocol WebSocket URL.

    Returns:
        A tuple of (browser, context, page).
    """
    global _playwright_instance
    logger.info("Connecting Playwright via CDP...")

    _playwright_instance = await async_playwright().start()
    browser = await _playwright_instance.chromium.connect_over_cdp(ws_endpoint)

    # AdsPower usually has one default context with one page already open
    contexts = browser.contexts
    if not contexts:
        raise RuntimeError("No browser contexts found after CDP connection.")

    context = contexts[0]
    pages = context.pages
    page = pages[0] if pages else await context.new_page()

    # Apply stealth patches to bypass robot detection
    await apply_stealth(context, page)

    logger.info("Playwright connected (stealth applied). Active page: %s", page.url)
    return browser, context, page


async def close_playwright() -> None:
    """Properly close the Playwright instance to avoid asyncio warnings."""
    global _playwright_instance
    if _playwright_instance:
        await _playwright_instance.stop()
        _playwright_instance = None


# ---------------------------------------------------------------------------
# Stealth JS injection - bypass WebDriver / automation detection
# ---------------------------------------------------------------------------
# CRITICAL: This script runs via add_init_script() BEFORE any page script.
# Using page.evaluate() would run AFTER page scripts = too late, sites like
# Shopee detect automation before stealth patches apply.
STEALTH_JS = """
() => {
    // =================================================================
    // 1. Hide navigator.webdriver flag (PRIMARY detection vector)
    // =================================================================
    Object.defineProperty(navigator, 'webdriver', {
        get: () => undefined,
    });

    // Also delete if it was set as a property
    if (navigator.webdriver !== undefined) {
        try { delete navigator.__proto__.webdriver; } catch(e) {}
    }

    // =================================================================
    // 2. Override Permissions API
    // =================================================================
    if (window.navigator.permissions) {
        const originalQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = (parameters) =>
            parameters.name === 'notifications'
                ? Promise.resolve({ state: Notification.permission })
                : originalQuery(parameters);
    }

    // =================================================================
    // 3. Fake plugins array (real Chrome has plugins)
    // =================================================================
    Object.defineProperty(navigator, 'plugins', {
        get: () => {
            var arr = [
                { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format', length: 1 },
                { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '', length: 1 },
                { name: 'Native Client', filename: 'internal-nacl-plugin', description: '', length: 2 },
            ];
            arr.refresh = function() {};
            return arr;
        },
    });

    // =================================================================
    // 4. Languages matching Vietnamese locale (CRITICAL for Shopee VN!)
    // =================================================================
    Object.defineProperty(navigator, 'languages', {
        get: () => ['vi-VN', 'vi', 'en-US', 'en'],
    });
    Object.defineProperty(navigator, 'language', {
        get: () => 'vi-VN',
    });

    // =================================================================
    // 5. Hardware fingerprint (consistent realistic values)
    // =================================================================
    Object.defineProperty(navigator, 'hardwareConcurrency', {
        get: () => 8,
    });
    Object.defineProperty(navigator, 'deviceMemory', {
        get: () => 8,
    });
    Object.defineProperty(navigator, 'maxTouchPoints', {
        get: () => 0,  // Desktop = 0
    });

    // =================================================================
    // 6. Network info (looks like real browser)
    // =================================================================
    if (navigator.connection) {
        Object.defineProperty(navigator.connection, 'rtt', { get: () => 50 });
        Object.defineProperty(navigator.connection, 'downlink', { get: () => 10 });
        Object.defineProperty(navigator.connection, 'effectiveType', { get: () => '4g' });
    }

    // =================================================================
    // 7. Chrome object (extensions expect chrome.runtime)
    // =================================================================
    if (window.chrome) {
        window.chrome.runtime = window.chrome.runtime || {};
    } else {
        window.chrome = { runtime: {} };
    }

    // =================================================================
    // 8. Prevent Function.toString detection
    // Shopee checks if native functions have been overridden
    // =================================================================
    const origToString = Function.prototype.toString;
    const customToString = function() {
        if (this === navigator.webdriver ||
            this === navigator.permissions.query ||
            this === WebGLRenderingContext.prototype.getParameter) {
            return 'function ' + (this.name || '') + '() { [native code] }';
        }
        return origToString.call(this);
    };
    // Make our toString also look native
    Object.defineProperty(Function.prototype, 'toString', {
        value: customToString,
        writable: true,
        configurable: true,
    });
    Object.defineProperty(customToString, 'toString', {
        value: function() { return 'function toString() { [native code] }'; },
    });

    // =================================================================
    // 9. WebGL vendor/renderer spoof
    // =================================================================
    const getParameter = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function(parameter) {
        if (parameter === 37445) return 'Intel Inc.';          // UNMASKED_VENDOR
        if (parameter === 37446) return 'Intel Iris OpenGL Engine';  // UNMASKED_RENDERER
        return getParameter.call(this, parameter);
    };
    // Also handle WebGL2
    if (typeof WebGL2RenderingContext !== 'undefined') {
        const getParam2 = WebGL2RenderingContext.prototype.getParameter;
        WebGL2RenderingContext.prototype.getParameter = function(parameter) {
            if (parameter === 37445) return 'Intel Inc.';
            if (parameter === 37446) return 'Intel Iris OpenGL Engine';
            return getParam2.call(this, parameter);
        };
    }

    // =================================================================
    // 10. Shadow DOM open mode (for extension support)
    // =================================================================
    const originalAttachShadow = Element.prototype.attachShadow;
    Element.prototype.attachShadow = function(init) {
        return originalAttachShadow.call(this, { ...init, mode: 'open' });
    };

    // =================================================================
    // 11. Clean ALL CDP (Chrome DevTools Protocol) markers
    // Playwright/Puppeteer inject these variables for automation
    // =================================================================
    // Pattern: cdc_adoQpoasnfa76pfcZLmcfl_*
    for (var key in window) {
        if (key.match(/^cdc_/)) {
            try { delete window[key]; } catch(e) {}
        }
    }
    // Also check document
    for (var key in document) {
        if (key.match(/^cdc_|^\\$cdc_/)) {
            try { delete document[key]; } catch(e) {}
        }
    }

    // =================================================================
    // 12. Remove Playwright-specific markers
    // =================================================================
    try { delete window.__playwright; } catch(e) {}
    try { delete window.__pw_manual; } catch(e) {}
    try { delete window.__PW_inspect; } catch(e) {}

    // =================================================================
    // 13. Canvas fingerprint noise (subtle random noise)
    // Makes each canvas read slightly different = looks like real browser
    // =================================================================
    const origToDataURL = HTMLCanvasElement.prototype.toDataURL;
    HTMLCanvasElement.prototype.toDataURL = function(type) {
        if (this.width > 16 && this.height > 16) {
            try {
                var ctx = this.getContext('2d');
                if (ctx) {
                    // Add random imperceptible noise to a few pixels
                    var rx = Math.floor(Math.random() * this.width);
                    var ry = Math.floor(Math.random() * this.height);
                    var px = ctx.getImageData(rx, ry, 1, 1);
                    px.data[0] = (px.data[0] + Math.floor(Math.random() * 3) - 1) & 0xFF;
                    px.data[1] = (px.data[1] + Math.floor(Math.random() * 3) - 1) & 0xFF;
                    ctx.putImageData(px, rx, ry);
                }
            } catch(e) {}
        }
        return origToDataURL.apply(this, arguments);
    };

    // =================================================================
    // 14. Iframe contentWindow protection
    // Bot detection scripts check if iframes behave normally
    // =================================================================
    try {
        Object.defineProperty(HTMLIFrameElement.prototype, 'contentWindow', {
            get: function() {
                return new Proxy(this.contentWindow || window, {
                    get: function(target, prop) {
                        if (prop === 'chrome') return window.chrome;
                        return target[prop];
                    }
                });
            }
        });
    } catch(e) {}
}
"""


async def apply_stealth(context: BrowserContext, page: Page) -> None:
    """Inject stealth JavaScript to bypass common bot detection.

    Uses context.add_init_script() so patches run BEFORE any page script.
    This is CRITICAL - page.evaluate() runs AFTER page scripts, which means
    detection scripts (like Shopee's) would detect automation before patches apply.

    AdsPower already handles most fingerprinting, but this adds
    extra protection against Playwright/CDP-specific detection.
    """
    # add_init_script runs BEFORE any page JavaScript on every navigation
    try:
        await context.add_init_script(STEALTH_JS)
        logger.info("Stealth init_script registered on context (runs BEFORE page scripts).")
    except Exception as e:
        logger.warning("add_init_script failed (non-fatal): %s", e)

    # Also apply immediately to current page (already loaded)
    try:
        await page.evaluate(STEALTH_JS)
        logger.info("Stealth patches applied to current page.")
    except Exception as e:
        logger.warning("Stealth evaluate warning (non-fatal): %s", e)


async def stop_adspower_profile(profile_id: str) -> None:
    """Stop/close an AdsPower browser profile.

    Args:
        profile_id: The AdsPower profile/user ID to close.
    """
    settings = get_settings()
    url = f"{settings.adspower_api_url}/api/v1/browser/stop?user_id={profile_id}"

    headers = {}
    if settings.adspower_api_key:
        headers["Authorization"] = f"Bearer {settings.adspower_api_key}"

    logger.info("Stopping AdsPower profile: %s", profile_id)

    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                data = await resp.json()
                if data.get("code") != 0:
                    logger.warning("Stop profile warning: %s", data.get("msg"))
                else:
                    logger.info("Profile %s stopped successfully.", profile_id)
        except aiohttp.ClientError as e:
            logger.warning("Failed to stop profile %s: %s", profile_id, e)


async def main():
    """Test: start AdsPower profile, connect Playwright, navigate to Facebook."""
    settings = get_settings()
    profile_id = settings.adspower_profile_id

    ws_endpoint = await start_adspower_profile(profile_id)
    browser, context, page = await connect_playwright(ws_endpoint)

    try:
        logger.info("Navigating to https://facebook.com ...")
        await page.goto("https://facebook.com", wait_until="domcontentloaded", timeout=30000)
        title = await page.title()
        logger.info("✅ Page title: %s", title)

        # Keep browser open for manual inspection
        logger.info("Browser is open. Press Ctrl+C to exit.")
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        await browser.close()
        await stop_adspower_profile(profile_id)


if __name__ == "__main__":
    asyncio.run(main())
