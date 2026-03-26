import asyncio
import base64
import json
import logging
from typing import Any

import aiohttp
from playwright.async_api import Page

from config import get_settings
from dom_parser import extract_clean_dom, dom_to_json

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# The strict JSON schema the AI must follow when responding
ACTION_SCHEMA_PROMPT = """You are an AI agent controlling a web browser. You browse the web like a real human.
You receive a JSON representation of the current page's interactive elements (and optionally a screenshot).
Based on the user's instruction, decide the NEXT SINGLE action to perform.

You MUST respond with ONLY a valid JSON object in this exact schema:
{
    "action": "click" | "double_click" | "hover" | "type" | "scroll" | "select" | "key_press" | "wait" | "navigate" | "done",
    "target_id": "the_element_cb_id_or_null",
    "value": "text_if_typing_or_url_if_navigating_or_null",
    "reasoning": "brief_explanation_of_why_this_action"
}

Action rules:
- "click": Click on the element with the given target_id.
- "double_click": Double-click on the element.
- "hover": Hover over the element (useful for revealing menus/tooltips).
- "type": Type the value into the element with the given target_id.
- "scroll": Scroll the page. target_id can be null. value can be "up" or "down".
- "select": Select a dropdown option. target_id is the select element, value is the option value.
- "key_press": Press a keyboard key. value is the key name (e.g. "Enter", "Escape", "Tab").
- "wait": Wait for content to load. target_id and value can be null.
- "navigate": Navigate to a URL. value must be the URL.
- "done": The task is complete. No more actions needed.

Behavior guidelines:
- Act like a real human: scroll to explore, hover before clicking, wait for pages to load.
- If you cannot find a suitable element for the task, use "scroll" to reveal more content.
- If a page just loaded, use "wait" to let dynamic content render.
- NEVER output anything other than the JSON object. No markdown, no explanation outside the JSON.
"""


async def take_screenshot_base64(page: Page) -> str | None:
    """Take a screenshot of the current page and return as base64 string.

    Returns:
        Base64-encoded PNG string, or None on failure.
    """
    try:
        screenshot_bytes = await page.screenshot(type="png", full_page=False)
        return base64.b64encode(screenshot_bytes).decode("utf-8")
    except Exception as e:
        logger.warning("Screenshot failed: %s", e)
        return None


async def send_to_ai(
    dom_data: dict[str, Any],
    user_prompt: str,
    conversation_history: list[dict[str, str]] | None = None,
    screenshot_b64: str | None = None,
) -> dict[str, Any]:
    """Send the minified DOM (+ optional screenshot) and user prompt to the AI API.

    Args:
        dom_data: The extracted DOM data from dom_parser.extract_clean_dom().
        user_prompt: The user's task description.
        conversation_history: Optional list of previous messages for multi-step tasks.
        screenshot_b64: Optional base64-encoded screenshot for AI vision.

    Returns:
        A dict with keys: action, target_id, value, reasoning.

    Raises:
        RuntimeError: If the AI API call fails or returns invalid JSON.
    """
    settings = get_settings()

    dom_json = dom_to_json(dom_data)

    messages = [
        {"role": "system", "content": ACTION_SCHEMA_PROMPT},
    ]

    # Add conversation history for multi-step tasks
    if conversation_history:
        messages.extend(conversation_history)

    # Build the user message with current page state
    text_content = (
        f"## Current Page State\n"
        f"URL: {dom_data.get('url', 'unknown')}\n"
        f"Title: {dom_data.get('title', 'unknown')}\n"
        f"Scroll Y: {dom_data.get('scroll_y', 0)}\n"
        f"Elements on screen: {dom_data.get('element_count', 0)}\n\n"
        f"## Interactive Elements (JSON)\n{dom_json}\n\n"
        f"## User Task\n{user_prompt}\n\n"
        f"Respond with the next single action JSON."
    )

    # If screenshot available, use multimodal message format
    if screenshot_b64:
        user_content = [
            {"type": "text", "text": text_content},
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{screenshot_b64}",
                    "detail": "low",
                },
            },
        ]
        messages.append({"role": "user", "content": user_content})
    else:
        messages.append({"role": "user", "content": text_content})

    logger.info("Sending DOM (%d elements) %s to AI...",
                dom_data.get("element_count", 0),
                "+ screenshot" if screenshot_b64 else "(text only)")

    # Call OpenAI-compatible API
    headers = {
        "Authorization": f"Bearer {settings.openclaw_api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "gpt-4o",
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": 300,
    }

    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(
                "https://api.openai.com/v1/chat/completions",
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    raise RuntimeError(f"AI API error (HTTP {resp.status}): {error_text}")
                response_data = await resp.json()
        except aiohttp.ClientError as e:
            raise RuntimeError(f"Failed to connect to AI API: {e}") from e

    # Extract the AI's response text
    try:
        ai_text = response_data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"Unexpected AI API response format: {response_data}") from e

    logger.info("AI raw response: %s", ai_text)

    # Parse the JSON action
    action = parse_ai_action(ai_text)
    return action


def parse_ai_action(ai_text: str) -> dict[str, Any]:
    """Parse the AI's response into a structured action dict.

    Handles cases where the AI wraps JSON in markdown code blocks.

    Args:
        ai_text: The raw text response from the AI.

    Returns:
        A dict with keys: action, target_id, value, reasoning.

    Raises:
        RuntimeError: If the response cannot be parsed as valid JSON.
    """
    # Strip markdown code block if present
    cleaned = ai_text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        # Remove first and last lines (``` markers)
        lines = [l for l in lines if not l.strip().startswith("```")]
        cleaned = "\n".join(lines).strip()

    try:
        action = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"AI returned invalid JSON: {e}\nRaw response: {ai_text}"
        ) from e

    # Validate required fields
    valid_actions = {"click", "double_click", "hover", "type", "scroll", "select", "key_press", "wait", "navigate", "done"}
    if action.get("action") not in valid_actions:
        raise RuntimeError(
            f"AI returned invalid action '{action.get('action')}'. "
            f"Must be one of: {valid_actions}"
        )

    # Normalize optional fields
    action.setdefault("target_id", None)
    action.setdefault("value", None)
    action.setdefault("reasoning", "")

    return action


class AgentLoop:
    """Run a multi-step AI agent loop: extract DOM -> ask AI -> execute -> repeat."""

    def __init__(self, page, user_prompt: str, max_steps: int = 20, use_vision: bool = False):
        self.page = page
        self.user_prompt = user_prompt
        self.max_steps = max_steps
        self.use_vision = use_vision
        self.history: list[dict[str, str]] = []
        self.step_count = 0

    async def run(self) -> list[dict[str, Any]]:
        """Execute the agent loop until 'done' or max_steps reached.

        Returns:
            A list of all actions taken.
        """
        actions_taken = []

        for step in range(self.max_steps):
            self.step_count = step + 1
            logger.info("=== Agent Step %d/%d ===", self.step_count, self.max_steps)

            # 1. Extract current DOM state
            dom_data = await extract_clean_dom(self.page)

            # 1b. Take screenshot if vision mode enabled
            screenshot_b64 = None
            if self.use_vision:
                screenshot_b64 = await take_screenshot_base64(self.page)

            # 2. Ask AI for next action
            try:
                action = await send_to_ai(dom_data, self.user_prompt, self.history, screenshot_b64)
            except RuntimeError as e:
                logger.error("AI error at step %d: %s", self.step_count, e)
                break

            logger.info(
                "Step %d action: %s (target=%s, value=%s) - %s",
                self.step_count,
                action["action"],
                action.get("target_id"),
                action.get("value"),
                action.get("reasoning", ""),
            )

            actions_taken.append(action)

            # 3. Check if done
            if action["action"] == "done":
                logger.info("Agent completed task in %d steps.", self.step_count)
                break

            # 4. Execute the action (import here to avoid circular imports)
            from human_actions import execute_action
            success = await execute_action(self.page, action)

            # 5. Record in history for context
            self.history.append({
                "role": "assistant",
                "content": json.dumps(action, ensure_ascii=False),
            })
            result_msg = "Action executed successfully." if success else "Action failed."
            self.history.append({
                "role": "user",
                "content": f"Result: {result_msg} Provide the next action.",
            })

            # Small delay between steps
            await asyncio.sleep(1)

        return actions_taken


if __name__ == "__main__":
    # Quick test: parse a sample AI response
    sample_response = '{"action": "click", "target_id": "cb_5", "value": null, "reasoning": "Clicking the Like button"}'
    parsed = parse_ai_action(sample_response)
    print("Parsed action:", json.dumps(parsed, indent=2))

    # Test markdown-wrapped response
    md_response = '```json\n{"action": "scroll", "target_id": null, "value": "down", "reasoning": "Need to see more posts"}\n```'
    parsed2 = parse_ai_action(md_response)
    print("Parsed markdown action:", json.dumps(parsed2, indent=2))

    print("✅ ai_controller.py self-test passed.")
