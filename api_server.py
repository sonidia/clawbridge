"""ClawBridge API Server - HTTP endpoint for OpenClaw to send commands.

Usage:
    python api_server.py                  (start on default port 8899)
    python api_server.py --port 9000      (custom port)
    python api_server.py --vision         (enable AI vision mode)

API Endpoints (Proxy Mode - OpenClaw controls directly, NO AI key needed):
    POST /execute     - Execute a specific action (click, type, scroll, etc.)
    POST /click_xy    - Click at specific coordinates (for extension buttons)
    POST /comment     - Type into contenteditable/rich-text editor (any site)
    GET  /find_text   - Find elements by visible text (returns coordinates)
    POST /navigate    - Navigate to a URL
    GET  /status      - Get current browser state
    GET  /dom         - Get current page DOM elements (interactive only)
    GET  /snapshot    - Get full page snapshot (elements + post text content)
    GET  /page_text   - Get page text content (posts, articles)
    GET  /scroll_and_snapshot - Scroll + wait for lazy content + snapshot
    GET  /screenshot  - Get page screenshot as base64
    POST /stop        - Shutdown the server

API Endpoints (AI Mode - needs OPENCLAW_API_KEY):
    POST /command     - Send a natural language command (AI decides actions)
"""

import asyncio
import json
import logging
import random
import sys
import warnings
from typing import Any

from aiohttp import web
from playwright.async_api import Page

from config import get_settings
from browser_manager import start_adspower_profile, connect_playwright, stop_adspower_profile, close_playwright
from dom_parser import extract_clean_dom, extract_page_text, extract_snapshot, scroll_and_snapshot, find_by_text, dom_to_json
from ai_controller import AgentLoop, take_screenshot_base64
from human_actions import execute_action

warnings.filterwarnings("ignore", category=ResourceWarning)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


class ClawBridgeServer:
    """HTTP API server that keeps browser open and accepts commands from OpenClaw."""

    def __init__(self, use_vision: bool = False):
        self.use_vision = use_vision
        self.page: Page | None = None
        self.browser = None
        self.context = None
        self.profile_id: str = ""
        self.is_busy = False
        self.last_result: dict[str, Any] = {}

    async def start_browser(self):
        """Initialize AdsPower browser and Playwright connection."""
        settings = get_settings()
        self.profile_id = settings.adspower_profile_id

        ws_endpoint = await start_adspower_profile(self.profile_id)
        self.browser, self.context, self.page = await connect_playwright(ws_endpoint)
        logger.info("Browser ready. Page: %s", self.page.url)

    async def stop_browser(self):
        """Close browser and stop AdsPower profile."""
        if self.browser:
            await self.browser.close()
        if self.profile_id:
            await stop_adspower_profile(self.profile_id)
        logger.info("Browser closed.")

    # -----------------------------------------------------------------------
    # API Handlers
    # -----------------------------------------------------------------------
    async def handle_command(self, request: web.Request) -> web.Response:
        """POST /command - Execute a natural language command.

        Request body:
            {"command": "Lướt facebook và tìm bài viết hỏi về sản phẩm", "max_steps": 20}

        Response:
            {"success": true, "actions": [...], "message": "Completed in 5 steps"}
        """
        if self.is_busy:
            return web.json_response(
                {"success": False, "error": "Agent is busy executing another command."},
                status=429,
            )

        try:
            data = await request.json()
        except Exception:
            return web.json_response(
                {"success": False, "error": "Invalid JSON body."},
                status=400,
            )

        command = data.get("command", "").strip()
        if not command:
            return web.json_response(
                {"success": False, "error": "Missing 'command' field."},
                status=400,
            )

        max_steps = data.get("max_steps", 20)
        use_vision = data.get("use_vision", self.use_vision)

        self.is_busy = True
        logger.info("Received command: %s", command)

        try:
            agent = AgentLoop(self.page, command, max_steps=max_steps, use_vision=use_vision)
            actions = await agent.run()

            result = {
                "success": True,
                "command": command,
                "actions_count": len(actions),
                "actions": actions,
                "current_url": self.page.url,
                "message": f"Completed in {len(actions)} steps.",
            }
            self.last_result = result
            return web.json_response(result)

        except Exception as e:
            logger.error("Command execution error: %s", e)
            return web.json_response(
                {"success": False, "error": str(e)},
                status=500,
            )
        finally:
            self.is_busy = False

    async def handle_navigate(self, request: web.Request) -> web.Response:
        """POST /navigate - Navigate to a specific URL.

        Request body:
            {"url": "https://facebook.com"}
        """
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"success": False, "error": "Invalid JSON."}, status=400)

        url = data.get("url", "").strip()
        if not url:
            return web.json_response({"success": False, "error": "Missing 'url' field."}, status=400)

        if not url.startswith("http"):
            url = "https://" + url

        try:
            await self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
            # Human-like delay after page load (real users wait to see the page)
            await asyncio.sleep(random.uniform(0.8, 2.0))
            title = await self.page.title()
            return web.json_response({
                "success": True,
                "url": self.page.url,
                "title": title,
            })
        except Exception as e:
            return web.json_response({"success": False, "error": str(e)}, status=500)

    async def handle_status(self, request: web.Request) -> web.Response:
        """GET /status - Get current browser state."""
        try:
            title = await self.page.title()
            return web.json_response({
                "success": True,
                "url": self.page.url,
                "title": title,
                "is_busy": self.is_busy,
                "vision_mode": self.use_vision,
                "last_result_summary": {
                    "command": self.last_result.get("command", ""),
                    "actions_count": self.last_result.get("actions_count", 0),
                } if self.last_result else None,
            })
        except Exception as e:
            return web.json_response({"success": False, "error": str(e)}, status=500)

    async def handle_dom(self, request: web.Request) -> web.Response:
        """GET /dom - Get current page DOM extraction."""
        try:
            dom_data = await extract_clean_dom(self.page)
            return web.json_response({
                "success": True,
                "dom": dom_data,
            })
        except Exception as e:
            return web.json_response({"success": False, "error": str(e)}, status=500)

    async def handle_screenshot(self, request: web.Request) -> web.Response:
        """GET /screenshot - Get current page screenshot as base64."""
        try:
            b64 = await take_screenshot_base64(self.page)
            if b64:
                return web.json_response({"success": True, "screenshot_base64": b64})
            return web.json_response({"success": False, "error": "Screenshot failed."}, status=500)
        except Exception as e:
            return web.json_response({"success": False, "error": str(e)}, status=500)

    async def handle_execute(self, request: web.Request) -> web.Response:
        """POST /execute - Execute a specific browser action directly.

        This is the KEY endpoint for OpenClaw proxy mode.
        OpenClaw sends the exact action to perform, NO AI API key needed.

        Request body:
            {"action": "click", "target_id": "cb_5"}
            {"action": "type", "target_id": "cb_3", "value": "Hello world"}
            {"action": "scroll", "value": "down"}
            {"action": "hover", "target_id": "cb_7"}
            {"action": "key_press", "value": "Enter"}
            {"action": "wait"}

        Response:
            {"success": true, "action": "click", "current_url": "..."}
        """
        try:
            data = await request.json()
        except Exception:
            return web.json_response(
                {"success": False, "error": "Invalid JSON body."},
                status=400,
            )

        action_type = data.get("action", "").strip()
        if not action_type:
            return web.json_response(
                {"success": False, "error": "Missing 'action' field. Valid: click, double_click, hover, type, scroll, select, key_press, wait, navigate"},
                status=400,
            )

        # Normalize the action dict
        action = {
            "action": action_type,
            "target_id": data.get("target_id"),
            "value": data.get("value"),
            "reasoning": data.get("reasoning", "OpenClaw direct command"),
        }

        logger.info("Direct execute: %s (target=%s, value=%s)",
                    action_type, action.get("target_id"), action.get("value"))

        try:
            success = await execute_action(self.page, action)
            # After action, get updated DOM snapshot for OpenClaw
            dom_data = await extract_clean_dom(self.page)
            return web.json_response({
                "success": success,
                "action": action_type,
                "current_url": self.page.url,
                "current_title": await self.page.title(),
                "dom": dom_data,
            })
        except Exception as e:
            logger.error("Execute error: %s", e)
            return web.json_response(
                {"success": False, "error": str(e)},
                status=500,
            )

    async def handle_click_xy(self, request: web.Request) -> web.Response:
        """POST /click_xy - Click at specific coordinates on the page.

        Body: {"x": 950, "y": 420}

        Use this when an element doesn't have a cb_id (e.g., extension-injected
        buttons like "COPY AFF LINK"). Get coordinates from /screenshot or
        from the element's bounding box in the DOM.
        """
        if not self.page:
            return web.json_response(
                {"success": False, "error": "Browser not connected."},
                status=503,
            )

        try:
            data = await request.json()
        except Exception:
            return web.json_response(
                {"success": False, "error": "Invalid JSON body. Need {\"x\": number, \"y\": number}"},
                status=400,
            )

        x = data.get("x")
        y = data.get("y")
        if x is None or y is None:
            return web.json_response(
                {"success": False, "error": "Missing 'x' or 'y' in body."},
                status=400,
            )

        try:
            from human_actions import _move_mouse_bezier, _get_mouse_position, _inject_mouse_tracker, _gauss_delay

            x, y = float(x), float(y)
            logger.info("Click at coordinates: (%.0f, %.0f)", x, y)
            # Human-like: bezier curve mouse movement to target, then click
            await _inject_mouse_tracker(self.page)
            from_x, from_y = await _get_mouse_position(self.page)
            target_x = x + random.uniform(-3, 3)
            target_y = y + random.uniform(-3, 3)
            await _move_mouse_bezier(self.page, from_x, from_y, target_x, target_y)
            await asyncio.sleep(_gauss_delay(0.15, 0.08, 0.05))
            await self.page.mouse.click(x, y)
            await asyncio.sleep(_gauss_delay(0.2, 0.1, 0.1))

            return web.json_response({
                "success": True,
                "action": "click_xy",
                "x": x,
                "y": y,
                "current_url": self.page.url,
            })
        except Exception as e:
            logger.error("Click XY error: %s", e)
            return web.json_response(
                {"success": False, "error": str(e)},
                status=500,
            )

    async def handle_copy_aff_link(self, request: web.Request) -> web.Response:
        """GET /copy_aff_link - Click "COPY AFF LINK" button and return the affiliate link.

        This integrates directly with the Shopee Aff Link Generator extension.
        It clicks the #gemini-aff-btn button, waits for the extension to process,
        and reads the resulting affiliate link from the clipboard.

        The extension auto-opens the affiliate tab if needed.
        Requires: Extension installed, logged into affiliate.shopee.vn.
        """
        if not self.page:
            return web.json_response(
                {"success": False, "error": "Browser not connected."},
                status=503,
            )

        try:
            # Check if button exists
            btn = self.page.locator('#gemini-aff-btn')
            if await btn.count() == 0:
                return web.json_response({
                    "success": False,
                    "error": "Button #gemini-aff-btn not found. Make sure you're on a Shopee product page and the extension is installed.",
                })

            # Human-like: small random scroll before clicking (simulate browsing)
            scroll_px = random.randint(-50, 50)
            await self.page.mouse.wheel(0, scroll_px)
            await asyncio.sleep(random.uniform(0.3, 0.8))

            # Click with human-like bezier mouse movement
            logger.info("Clicking COPY AFF LINK button (human-like)...")
            from human_actions import _move_mouse_bezier, _get_mouse_position, _inject_mouse_tracker, _gauss_delay
            box = await btn.bounding_box()
            if box:
                await _inject_mouse_tracker(self.page)
                target_x = box["x"] + box["width"] * random.uniform(0.3, 0.7)
                target_y = box["y"] + box["height"] * random.uniform(0.3, 0.7)
                from_x, from_y = await _get_mouse_position(self.page)
                await _move_mouse_bezier(self.page, from_x, from_y, target_x, target_y)
                await asyncio.sleep(_gauss_delay(0.15, 0.08, 0.05))
                await self.page.mouse.click(target_x, target_y)
            else:
                await btn.click()

            # Wait for extension to process (monitor button text change)
            # Extension changes: "⏳ Đang lấy link..." → "✅ Đã copy!" or "❌ ..."
            # Longer timeout: extension may auto-open affiliate tab (takes ~10s)
            max_wait = 30  # seconds
            for i in range(max_wait * 2):
                await asyncio.sleep(0.5)
                btn_text = await btn.text_content() or ""
                if "Đã copy" in btn_text or "✅" in btn_text:
                    # Success! Try multiple methods to read clipboard
                    clip = ""

                    # Method 1: navigator.clipboard.readText()
                    try:
                        clip = await self.page.evaluate("navigator.clipboard.readText()")
                    except Exception:
                        pass

                    # Method 2: execCommand paste fallback
                    if not clip:
                        try:
                            clip = await self.page.evaluate("""
                                () => {
                                    const ta = document.createElement('textarea');
                                    ta.style.position = 'fixed';
                                    ta.style.opacity = '0';
                                    document.body.appendChild(ta);
                                    ta.focus();
                                    document.execCommand('paste');
                                    const val = ta.value;
                                    document.body.removeChild(ta);
                                    return val;
                                }
                            """)
                        except Exception:
                            pass

                    if clip:
                        logger.info("Aff link copied: %s", clip[:100])
                        return web.json_response({
                            "success": True,
                            "aff_link": clip,
                            "message": "Affiliate link copied successfully",
                        })
                    else:
                        return web.json_response({
                            "success": True,
                            "aff_link": "",
                            "message": "Extension shows success. Clipboard read failed but link is in clipboard - use Ctrl+V to paste.",
                        })
                elif "❌" in btn_text or "Lỗi" in btn_text or "error" in btn_text.lower():
                    return web.json_response({
                        "success": False,
                        "error": f"Extension error: {btn_text}",
                    })

            return web.json_response({
                "success": False,
                "error": f"Timeout after {max_wait}s. Make sure you're logged into affiliate.shopee.vn.",
            })
        except Exception as e:
            logger.error("Copy aff link error: %s", e)
            return web.json_response(
                {"success": False, "error": str(e)},
                status=500,
            )

    async def handle_comment(self, request: web.Request) -> web.Response:
        """POST /comment (also /fb_comment) - Type into any contenteditable/rich-text editor.

        Works universally on: Facebook, Twitter/X, LinkedIn, forums, CMS editors,
        or any website using contenteditable, DraftJS, Lexical, ProseMirror, etc.

        JSON body:
            target_id: cb_id of the text box element (role=textbox or contenteditable)
            text: The text to type
            submit: If true, press Enter to submit after typing (default: false)

        Workflow: Click element → Focus contenteditable → Type text → (optional) Enter
        """
        if not self.page:
            return web.json_response(
                {"success": False, "error": "Browser not connected."},
                status=503,
            )

        try:
            body = await request.json()
        except Exception:
            return web.json_response(
                {"success": False, "error": "Invalid JSON body."},
                status=400,
            )

        target_id = body.get("target_id", "")
        text = body.get("text", "")
        submit = body.get("submit", False)

        if not target_id or not text:
            return web.json_response({
                "success": False,
                "error": "Missing 'target_id' and/or 'text'. Example: {\"target_id\":\"cb_5\",\"text\":\"Great post!\",\"submit\":true}",
            }, status=400)

        try:
            from dom_parser import find_element_by_cb_id
            from human_actions import human_type, human_press_key

            element = await find_element_by_cb_id(self.page, target_id)
            if not element:
                # Fallback: try to find any visible comment box on the page
                logger.info("Element %s not found, searching for comment box...", target_id)
                comment_box = self.page.locator('[role="textbox"][contenteditable="true"]').first
                if await comment_box.count() > 0:
                    element = await comment_box.element_handle()
                    logger.info("Found comment box via fallback selector")

            if not element:
                return web.json_response({
                    "success": False,
                    "error": f"Comment box '{target_id}' not found. Use /snapshot first to get the correct target_id for the comment box.",
                })

            # Type using contenteditable-aware typing (universal)
            logger.info("Typing into contenteditable '%s': '%s'", target_id, text[:50])
            await human_type(self.page, element, text)

            # Wait a moment for React to process
            await asyncio.sleep(random.uniform(0.5, 1.0))

            # Optionally submit by pressing Enter
            if submit:
                await asyncio.sleep(random.uniform(0.8, 1.5))
                await human_press_key(self.page, "Enter")
                logger.info("Comment submitted with Enter key")
                await asyncio.sleep(random.uniform(1.0, 2.0))

            return web.json_response({
                "success": True,
                "message": f"Comment typed{' and submitted' if submit else ''}.",
                "text": text,
                "submitted": submit,
            })
        except Exception as e:
            logger.error("Comment/contenteditable error: %s", e)
            return web.json_response(
                {"success": False, "error": str(e)},
                status=500,
            )

    async def handle_find_text(self, request: web.Request) -> web.Response:
        """GET /find_text?q=COPY AFF LINK - Find elements by visible text.

        Searches the entire DOM including Shadow DOM (extension-injected elements).
        Returns bounding box and center coordinates for each match.
        Use center_x/center_y with /click_xy to click on the found element.

        Query params:
            q: Text to search for (case-insensitive)
        """
        if not self.page:
            return web.json_response(
                {"success": False, "error": "Browser not connected."},
                status=503,
            )

        search_text = request.query.get("q", "").strip()
        if not search_text:
            return web.json_response(
                {"success": False, "error": "Missing 'q' query param. Example: /find_text?q=COPY AFF LINK"},
                status=400,
            )

        try:
            result = await find_by_text(self.page, search_text)
            return web.json_response({
                "success": True,
                **result,
            })
        except Exception as e:
            logger.error("Find text error: %s", e)
            return web.json_response(
                {"success": False, "error": str(e)},
                status=500,
            )

    async def handle_snapshot(self, request: web.Request) -> web.Response:
        """GET /snapshot - Full page snapshot: interactive elements + post text content.

        This is the recommended endpoint for OpenClaw - it returns both
        clickable elements (with cb_id) AND the actual text content of posts.
        """
        if not self.page:
            return web.json_response(
                {"success": False, "error": "Browser not connected."},
                status=503,
            )

        try:
            snapshot_data = await extract_snapshot(self.page)
            return web.json_response({
                "success": True,
                **snapshot_data,
            })
        except Exception as e:
            logger.error("Snapshot error: %s", e)
            return web.json_response(
                {"success": False, "error": str(e)},
                status=500,
            )

    async def handle_page_text(self, request: web.Request) -> web.Response:
        """GET /page_text - Extract text content from posts and articles.

        Returns structured content: posts (author, text, reactions), products (name, price),
        or text blocks. Works universally on any website.
        """
        if not self.page:
            return web.json_response(
                {"success": False, "error": "Browser not connected."},
                status=503,
            )

        try:
            text_data = await extract_page_text(self.page)
            return web.json_response({
                "success": True,
                **text_data,
            })
        except Exception as e:
            logger.error("Page text error: %s", e)
            return web.json_response(
                {"success": False, "error": str(e)},
                status=500,
            )

    async def handle_scroll_and_snapshot(self, request: web.Request) -> web.Response:
        """GET /scroll_and_snapshot - Scroll to load lazy content, then snapshot.

        Query params:
            scrolls: Number of scroll increments (default 3, max 10)

        Solves lazy loading on any JS-heavy site (social media, e-commerce, etc.).
        Scrolls down multiple times, waits for new content to load between
        each scroll, then extracts the full snapshot (elements + text).
        """
        if not self.page:
            return web.json_response(
                {"success": False, "error": "Browser not connected."},
                status=503,
            )

        scroll_count = min(int(request.query.get("scrolls", "3")), 10)

        try:
            data = await scroll_and_snapshot(self.page, scroll_count)
            return web.json_response({
                "success": True,
                **data,
            })
        except Exception as e:
            logger.error("Scroll and snapshot error: %s", e)
            return web.json_response(
                {"success": False, "error": str(e)},
                status=500,
            )

    async def handle_stop(self, request: web.Request) -> web.Response:
        """POST /stop - Shutdown the server."""
        logger.info("Shutdown requested via API.")
        await self.stop_browser()
        # Schedule server shutdown
        asyncio.get_event_loop().call_later(0.5, lambda: sys.exit(0))
        return web.json_response({"success": True, "message": "Server shutting down."})


async def create_app(use_vision: bool = False) -> web.Application:
    """Create and configure the aiohttp web application."""
    server = ClawBridgeServer(use_vision=use_vision)

    # Start browser before handling requests
    await server.start_browser()

    app = web.Application()
    # Proxy mode endpoints (NO AI key needed - OpenClaw controls directly)
    app.router.add_post("/execute", server.handle_execute)
    app.router.add_post("/click_xy", server.handle_click_xy)
    app.router.add_post("/comment", server.handle_comment)
    app.router.add_post("/fb_comment", server.handle_comment)  # backward compat
    app.router.add_get("/find_text", server.handle_find_text)
    app.router.add_get("/copy_aff_link", server.handle_copy_aff_link)
    app.router.add_post("/navigate", server.handle_navigate)
    app.router.add_get("/status", server.handle_status)
    app.router.add_get("/dom", server.handle_dom)
    app.router.add_get("/snapshot", server.handle_snapshot)
    app.router.add_get("/page_text", server.handle_page_text)
    app.router.add_get("/scroll_and_snapshot", server.handle_scroll_and_snapshot)
    app.router.add_get("/screenshot", server.handle_screenshot)
    app.router.add_post("/stop", server.handle_stop)
    # AI mode endpoint (needs OPENCLAW_API_KEY)
    app.router.add_post("/command", server.handle_command)

    # Cleanup on shutdown
    async def on_shutdown(app):
        await server.stop_browser()
        await close_playwright()

    app.on_shutdown.append(on_shutdown)
    return app


async def async_main():
    port = 8899
    use_vision = "--vision" in sys.argv

    # Parse --port argument
    if "--port" in sys.argv:
        idx = sys.argv.index("--port")
        if idx + 1 < len(sys.argv):
            port = int(sys.argv[idx + 1])

    logger.info("=== ClawBridge API Server ===")
    logger.info("Port: %d", port)
    logger.info("Vision: %s", "ON" if use_vision else "OFF")
    logger.info("")
    logger.info("=== Proxy Mode (OpenClaw controls directly, NO AI key needed) ===")
    logger.info("  POST http://localhost:%d/execute      - Execute action (click/type/scroll...)", port)
    logger.info("  POST http://localhost:%d/click_xy     - Click at coordinates (extension buttons)", port)
    logger.info("  POST http://localhost:%d/comment      - Type into contenteditable (any site) ", port)
    logger.info("  GET  http://localhost:%d/find_text    - Find element by text → get coordinates", port)
    logger.info("  POST http://localhost:%d/navigate     - Navigate to URL", port)
    logger.info("  GET  http://localhost:%d/status       - Browser status", port)
    logger.info("  GET  http://localhost:%d/dom          - Interactive elements only", port)
    logger.info("  GET  http://localhost:%d/snapshot     - Full snapshot (elements + post text) ", port)
    logger.info("  GET  http://localhost:%d/page_text    - Post/article text content", port)
    logger.info("  GET  http://localhost:%d/scroll_and_snapshot - Scroll + lazy load + snapshot", port)
    logger.info("  GET  http://localhost:%d/screenshot   - Page screenshot (base64)", port)
    logger.info("  POST http://localhost:%d/stop         - Shutdown server", port)
    logger.info("")
    logger.info("=== AI Mode (needs OPENCLAW_API_KEY in .env) ===")
    logger.info("  POST http://localhost:%d/command      - Natural language command", port)
    logger.info("")
    logger.info("OpenClaw examples:")
    logger.info('  1. Snapshot:  curl http://localhost:%d/snapshot', port)
    logger.info('  2. Click:     curl -X POST http://localhost:%d/execute -d "{\\"action\\":\\"click\\",\\"target_id\\":\\"cb_5\\"}"', port)
    logger.info('  3. Scroll:    curl -X POST http://localhost:%d/execute -d "{\\"action\\":\\"scroll\\",\\"value\\":\\"down\\"}"', port)

    app = await create_app(use_vision)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    logger.info("Server running on http://0.0.0.0:%d  (Ctrl+C to stop)", port)

    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Shutting down...")
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(async_main())
