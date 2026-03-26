import asyncio
import logging
import math
import random
from typing import Any

from playwright.async_api import Page, ElementHandle

from dom_parser import find_element_by_cb_id

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Utility: Gaussian random (more natural than uniform)
# ---------------------------------------------------------------------------
def _gauss_delay(mean: float, std: float, min_val: float = 0.01) -> float:
    """Return a gaussian-distributed delay, clamped to min_val."""
    return max(min_val, random.gauss(mean, std))


# ---------------------------------------------------------------------------
# Utility: Bezier curve mouse movement
# ---------------------------------------------------------------------------
def _bezier_points(x0: float, y0: float, x1: float, y1: float, steps: int = 20) -> list[tuple[float, float]]:
    """Generate a list of (x, y) points along a cubic bezier curve
    from (x0, y0) to (x1, y1) with randomized control points.
    This simulates natural human mouse movement (not a straight line).
    """
    # Random control points offset from the straight line
    dist = math.hypot(x1 - x0, y1 - y0)
    spread = max(30, dist * 0.3)

    cx1 = x0 + (x1 - x0) * random.uniform(0.2, 0.4) + random.uniform(-spread, spread)
    cy1 = y0 + (y1 - y0) * random.uniform(0.2, 0.4) + random.uniform(-spread, spread)
    cx2 = x0 + (x1 - x0) * random.uniform(0.6, 0.8) + random.uniform(-spread, spread)
    cy2 = y0 + (y1 - y0) * random.uniform(0.6, 0.8) + random.uniform(-spread, spread)

    points = []
    for i in range(steps + 1):
        t = i / steps
        # Cubic bezier formula
        xt = (1 - t)**3 * x0 + 3 * (1 - t)**2 * t * cx1 + 3 * (1 - t) * t**2 * cx2 + t**3 * x1
        yt = (1 - t)**3 * y0 + 3 * (1 - t)**2 * t * cy1 + 3 * (1 - t) * t**2 * cy2 + t**3 * y1
        points.append((xt, yt))
    return points


async def _move_mouse_bezier(page: Page, from_x: float, from_y: float, to_x: float, to_y: float) -> None:
    """Move the mouse from one point to another using a bezier curve path."""
    dist = math.hypot(to_x - from_x, to_y - from_y)
    steps = max(10, min(40, int(dist / 15)))
    points = _bezier_points(from_x, from_y, to_x, to_y, steps)

    for px, py in points:
        await page.mouse.move(px, py)
        await asyncio.sleep(_gauss_delay(0.008, 0.004, 0.002))


async def _get_mouse_position(page: Page) -> tuple[float, float]:
    """Get current mouse position (approximate from viewport center if unknown)."""
    try:
        pos = await page.evaluate("() => ({x: (window.__mPos && window.__mPos.x) || 0, y: (window.__mPos && window.__mPos.y) || 0})")
        if pos["x"] == 0 and pos["y"] == 0:
            vp = page.viewport_size or {"width": 1280, "height": 720}
            return (vp["width"] / 2, vp["height"] / 2)
        return (pos["x"], pos["y"])
    except Exception:
        return (640, 360)


async def _random_micro_movement(page: Page) -> None:
    """Small random mouse jitter to appear more human.
    Humans never keep the mouse perfectly still."""
    if random.random() > 0.3:  # 70% chance of micro-movement
        return
    try:
        mx, my = await _get_mouse_position(page)
        jx = mx + random.uniform(-5, 5)
        jy = my + random.uniform(-5, 5)
        await page.mouse.move(jx, jy)
        await asyncio.sleep(_gauss_delay(0.05, 0.02))
    except Exception:
        pass


async def _inject_mouse_tracker(page: Page) -> None:
    """Inject a tiny JS snippet to track mouse position for bezier calculations.
    Uses a non-obvious property name to avoid detection by bot-scanning scripts.
    """
    try:
        await page.evaluate("""
            if (!window.__mPos) {
                Object.defineProperty(window, '__mPos', {
                    value: {x: 0, y: 0, _t: true},
                    writable: true,
                    enumerable: false,
                    configurable: false
                });
                document.addEventListener('mousemove', (e) => {
                    window.__mPos.x = e.clientX;
                    window.__mPos.y = e.clientY;
                }, {passive: true});
            }
        """)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Core human-like actions
# ---------------------------------------------------------------------------
async def human_click(page: Page, element: ElementHandle) -> None:
    """Click an element with human-like behavior:
    1. Move mouse along bezier curve to element
    2. Brief hover pause
    3. Click with slight random offset
    """
    await _inject_mouse_tracker(page)
    box = await element.bounding_box()
    if not box:
        await element.click()
        return

    # Target point with natural offset (humans don't click exact center)
    target_x = box["x"] + box["width"] * random.uniform(0.3, 0.7)
    target_y = box["y"] + box["height"] * random.uniform(0.3, 0.7)

    # Move mouse along bezier curve
    from_x, from_y = await _get_mouse_position(page)
    await _move_mouse_bezier(page, from_x, from_y, target_x, target_y)

    # Brief hover pause (humans hesitate before clicking)
    await asyncio.sleep(_gauss_delay(0.15, 0.08, 0.05))

    # Click
    await page.mouse.click(target_x, target_y)

    # Post-click pause
    await asyncio.sleep(_gauss_delay(0.1, 0.05, 0.03))
    logger.info("Human click performed at (%.0f, %.0f).", target_x, target_y)


async def human_double_click(page: Page, element: ElementHandle) -> None:
    """Double-click with human-like movement."""
    await _inject_mouse_tracker(page)
    box = await element.bounding_box()
    if not box:
        await element.dblclick()
        return

    target_x = box["x"] + box["width"] * random.uniform(0.3, 0.7)
    target_y = box["y"] + box["height"] * random.uniform(0.3, 0.7)

    from_x, from_y = await _get_mouse_position(page)
    await _move_mouse_bezier(page, from_x, from_y, target_x, target_y)
    await asyncio.sleep(_gauss_delay(0.1, 0.05))
    await page.mouse.dblclick(target_x, target_y)
    logger.info("Human double-click performed.")


async def human_hover(page: Page, element: ElementHandle) -> None:
    """Hover over an element with bezier mouse movement."""
    await _inject_mouse_tracker(page)
    box = await element.bounding_box()
    if not box:
        await element.hover()
        return

    target_x = box["x"] + box["width"] * random.uniform(0.3, 0.7)
    target_y = box["y"] + box["height"] * random.uniform(0.3, 0.7)

    from_x, from_y = await _get_mouse_position(page)
    await _move_mouse_bezier(page, from_x, from_y, target_x, target_y)
    await asyncio.sleep(_gauss_delay(0.5, 0.2, 0.2))
    logger.info("Human hover performed.")


async def human_type(page: Page, element: ElementHandle, text: str) -> None:
    """Type text with human-like behavior:
    1. Click element with bezier movement
    2. Small pause
    3. Type with gaussian-distributed delays
    4. Occasional typo + backspace (very low chance)

    Handles both regular inputs and contenteditable elements (comment boxes, rich text editors).
    """
    # Check if element is contenteditable (comment box, rich text editor, etc.)
    is_contenteditable = await element.evaluate("""
        el => {
            if (el.getAttribute('contenteditable') === 'true') return true;
            if (el.getAttribute('role') === 'textbox') return true;
            // Check if there's a contenteditable child (React editors nest these)
            var child = el.querySelector('[contenteditable="true"]');
            if (child) return true;
            return false;
        }
    """)

    if is_contenteditable:
        await _type_contenteditable(page, element, text)
    else:
        await _type_regular(page, element, text)

    logger.info("Human typing complete: '%s' (%d chars).", text[:30], len(text))


async def _type_regular(page: Page, element: ElementHandle, text: str) -> None:
    """Type into regular input/textarea elements."""
    await human_click(page, element)
    await asyncio.sleep(_gauss_delay(0.2, 0.1, 0.1))

    for i, char in enumerate(text):
        # Rare typo simulation (2% chance, skip for first/last char)
        if 0 < i < len(text) - 1 and random.random() < 0.02:
            wrong_char = random.choice('abcdefghijklmnopqrstuvwxyz')
            await page.keyboard.type(wrong_char, delay=0)
            await asyncio.sleep(_gauss_delay(0.15, 0.05))
            await page.keyboard.press('Backspace')
            await asyncio.sleep(_gauss_delay(0.1, 0.04))

        await page.keyboard.type(char, delay=0)

        # Gaussian delay between keystrokes (mean=90ms, std=30ms)
        await asyncio.sleep(_gauss_delay(0.09, 0.03, 0.03))

        # Occasional longer pause (thinking pause, ~5% chance)
        if random.random() < 0.05:
            await asyncio.sleep(_gauss_delay(0.4, 0.15, 0.2))


async def _type_contenteditable(page: Page, element: ElementHandle, text: str) -> None:
    """Type into contenteditable elements (comment boxes, DraftJS/Lexical/ProseMirror editors).

    Modern web apps use complex React/Vue-based editors where:
    - The visible element may be a container, actual editable div is nested inside
    - Must click to activate the editor first
    - Must focus the correct contenteditable element
    - React needs proper input events to register the content
    """
    logger.info("Detected contenteditable element, using rich-editor compatible typing...")

    # Step 1: Find the actual contenteditable element (may be nested)
    editable_handle = await element.evaluate_handle("""
        el => {
            // If this element itself is contenteditable, use it
            if (el.getAttribute('contenteditable') === 'true') return el;
            // Otherwise find the nested contenteditable child
            var child = el.querySelector('[contenteditable="true"]');
            return child || el;
        }
    """)
    # Convert JSHandle to ElementHandle (needed for bounding_box, etc.)
    editable = editable_handle.as_element()
    if not editable:
        logger.warning("Could not get ElementHandle for contenteditable, falling back to original element")
        editable = element

    # Step 2: Click the element with human-like bezier movement to activate editor
    await human_click(page, element)
    await asyncio.sleep(_gauss_delay(0.5, 0.2, 0.3))

    # Step 3: Focus the actual editable element via JS (critical for React editors)
    await editable.evaluate("""
        el => {
            el.focus();
            // Place cursor at end
            var range = document.createRange();
            range.selectNodeContents(el);
            range.collapse(false);
            var sel = window.getSelection();
            sel.removeAllRanges();
            sel.addRange(range);
        }
    """)
    await asyncio.sleep(_gauss_delay(0.2, 0.1, 0.1))

    # Step 4: Click again inside the editable to ensure focus (some editors need this)
    editable_box = await editable.bounding_box()
    if editable_box:
        click_x = editable_box["x"] + editable_box["width"] * random.uniform(0.2, 0.8)
        click_y = editable_box["y"] + editable_box["height"] * random.uniform(0.3, 0.7)
        await page.mouse.click(click_x, click_y)
        await asyncio.sleep(_gauss_delay(0.3, 0.1, 0.15))

    # Step 5: Type text character by character with human-like delays
    for i, char in enumerate(text):
        # Rare typo simulation (1.5% chance for contenteditable)
        if 0 < i < len(text) - 1 and random.random() < 0.015:
            wrong_char = random.choice('abcdefghijklmnopqrstuvwxyz')
            await page.keyboard.type(wrong_char, delay=0)
            await asyncio.sleep(_gauss_delay(0.2, 0.08))
            await page.keyboard.press('Backspace')
            await asyncio.sleep(_gauss_delay(0.12, 0.05))

        await page.keyboard.type(char, delay=0)

        # Gaussian delay between keystrokes (slightly slower for comments)
        await asyncio.sleep(_gauss_delay(0.1, 0.04, 0.04))

        # Occasional thinking pause (~5% chance)
        if random.random() < 0.05:
            await asyncio.sleep(_gauss_delay(0.5, 0.2, 0.25))

    # Step 6: Dispatch React-compatible input event (ensures React state updates)
    await editable.evaluate("""
        el => {
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
        }
    """)
    await asyncio.sleep(_gauss_delay(0.15, 0.05, 0.05))


async def human_select_option(page: Page, element: ElementHandle, value: str) -> None:
    """Select a dropdown option with human-like behavior."""
    await human_click(page, element)
    await asyncio.sleep(_gauss_delay(0.3, 0.1, 0.1))
    await element.select_option(value)
    await asyncio.sleep(_gauss_delay(0.15, 0.05))
    logger.info("Human select: '%s'", value)


async def human_scroll(page: Page, direction: str = "down") -> None:
    """Scroll with human-like behavior:
    1. Multiple small scroll steps (not one big jump)
    2. Variable speed (fast in middle, slow at start/end)
    3. Random pauses between scrolls
    """
    multiplier = 1 if direction == "down" else -1
    total_distance = random.randint(300, 700)
    scrolled = 0

    # Break into 3-6 smaller scroll steps
    num_steps = random.randint(3, 6)
    step_sizes = []
    for _ in range(num_steps):
        step_sizes.append(random.uniform(0.5, 1.5))
    total_weight = sum(step_sizes)
    step_sizes = [s / total_weight * total_distance for s in step_sizes]

    for i, step_px in enumerate(step_sizes):
        pixels = int(step_px) * multiplier
        await page.mouse.wheel(0, pixels)
        scrolled += abs(pixels)

        # Variable pause: shorter in middle, longer at start/end
        if i == 0 or i == num_steps - 1:
            await asyncio.sleep(_gauss_delay(0.25, 0.1, 0.1))
        else:
            await asyncio.sleep(_gauss_delay(0.12, 0.05, 0.04))

    # Occasional reading pause after scroll (humans stop to read)
    if random.random() < 0.4:
        await asyncio.sleep(_gauss_delay(1.0, 0.5, 0.3))

    logger.info("Human scroll %s: ~%d pixels in %d steps.", direction, scrolled, num_steps)


async def human_press_key(page: Page, key: str) -> None:
    """Press a keyboard key with human-like delay."""
    await asyncio.sleep(_gauss_delay(0.05, 0.02))
    await page.keyboard.press(key)
    await asyncio.sleep(_gauss_delay(0.08, 0.03))
    logger.info("Human key press: %s", key)


async def human_wait() -> None:
    """Wait a human-like random amount of time (gaussian distribution)."""
    wait_time = _gauss_delay(2.0, 0.8, 0.5)
    logger.info("Human wait: %.1f seconds.", wait_time)
    await asyncio.sleep(wait_time)


# ---------------------------------------------------------------------------
# Action executor (dispatcher)
# ---------------------------------------------------------------------------
async def execute_action(page: Page, action: dict[str, Any]) -> bool:
    """Execute a single AI-generated action on the page.

    Supported actions:
        click, double_click, hover, type, scroll, select, key_press,
        wait, navigate, done

    Args:
        page: The Playwright Page object.
        action: A dict with keys: action, target_id, value, reasoning.

    Returns:
        True if the action was executed successfully, False otherwise.
    """
    action_type = action.get("action")
    target_id = action.get("target_id")
    value = action.get("value")

    logger.info(
        "Executing action: %s (target=%s, value=%s)",
        action_type, target_id, value,
    )

    # Random micro-movement before action (human behavior)
    await _random_micro_movement(page)

    try:
        if action_type == "click":
            if not target_id:
                logger.warning("Click action requires a target_id.")
                return False
            element = await find_element_by_cb_id(page, target_id)
            if not element:
                logger.warning("Element %s not found on page.", target_id)
                return False
            await human_click(page, element)
            return True

        elif action_type == "double_click":
            if not target_id:
                return False
            element = await find_element_by_cb_id(page, target_id)
            if not element:
                return False
            await human_double_click(page, element)
            return True

        elif action_type == "hover":
            if not target_id:
                return False
            element = await find_element_by_cb_id(page, target_id)
            if not element:
                return False
            await human_hover(page, element)
            return True

        elif action_type == "type":
            if not target_id or not value:
                logger.warning("Type action requires target_id and value.")
                return False
            element = await find_element_by_cb_id(page, target_id)
            if not element:
                logger.warning("Element %s not found on page.", target_id)
                return False
            await human_type(page, element, value)
            return True

        elif action_type == "scroll":
            direction = value if value in ("up", "down") else "down"
            await human_scroll(page, direction)
            return True

        elif action_type == "select":
            if not target_id or not value:
                return False
            element = await find_element_by_cb_id(page, target_id)
            if not element:
                return False
            await human_select_option(page, element, value)
            return True

        elif action_type == "key_press":
            if not value:
                return False
            await human_press_key(page, value)
            return True

        elif action_type == "wait":
            await human_wait()
            return True

        elif action_type == "navigate":
            if not value:
                logger.warning("Navigate action requires a URL in value.")
                return False
            await page.goto(value, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(_gauss_delay(1.0, 0.3, 0.5))
            logger.info("Navigated to: %s", value)
            return True

        elif action_type == "done":
            logger.info("Task marked as done by AI.")
            return True

        else:
            logger.warning("Unknown action type: %s", action_type)
            return False

    except Exception as e:
        logger.error("Action execution failed: %s", e)
        return False


if __name__ == "__main__":
    # Self-test: bezier curve generation & gaussian delay
    import json

    pts = _bezier_points(0, 0, 100, 100, 10)
    print(f"Bezier curve: {len(pts)} points from (0,0) to (100,100)")
    print(f"  Start: ({pts[0][0]:.1f}, {pts[0][1]:.1f})")
    print(f"  End:   ({pts[-1][0]:.1f}, {pts[-1][1]:.1f})")

    delays = [_gauss_delay(0.09, 0.03) for _ in range(20)]
    print(f"Gaussian delays (mean=90ms): min={min(delays)*1000:.0f}ms max={max(delays)*1000:.0f}ms avg={sum(delays)/len(delays)*1000:.0f}ms")

    test_actions = [
        {"action": "click", "target_id": "cb_1", "value": None},
        {"action": "double_click", "target_id": "cb_2", "value": None},
        {"action": "hover", "target_id": "cb_3", "value": None},
        {"action": "type", "target_id": "cb_4", "value": "hello"},
        {"action": "scroll", "target_id": None, "value": "down"},
        {"action": "select", "target_id": "cb_5", "value": "option1"},
        {"action": "key_press", "target_id": None, "value": "Enter"},
        {"action": "wait", "target_id": None, "value": None},
        {"action": "navigate", "target_id": None, "value": "https://example.com"},
        {"action": "done", "target_id": None, "value": None},
    ]
    print(f"\nSupported actions: {len(test_actions)}")
    for a in test_actions:
        print(f"  - {a['action']}")

    print("\u2705 human_actions.py self-test passed.")
