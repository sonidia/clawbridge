"""ClawBridge - Main entry point.

Usage:
    python main.py interactive                       (interactive mode - keep typing commands)
    python main.py interactive --vision              (interactive + AI vision)
    python main.py "Your task description here"      (single command mode)
    python main.py --vision "Like the first post"    (single command + AI vision)
    python main.py --test                            (test connection only)
"""

import asyncio
import sys
import logging
import warnings

from config import get_settings
from browser_manager import start_adspower_profile, connect_playwright, stop_adspower_profile, close_playwright
from dom_parser import extract_clean_dom, dom_to_json
from ai_controller import AgentLoop

# Suppress harmless asyncio "unclosed transport" warnings on Windows
warnings.filterwarnings("ignore", category=ResourceWarning, message="unclosed transport")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


async def run_agent(user_prompt: str, max_steps: int = 20, use_vision: bool = False):
    """Launch AdsPower browser, connect Playwright, and run the AI agent loop.

    Args:
        user_prompt: The task for the AI to perform on Facebook.
        max_steps: Maximum number of AI action steps.
        use_vision: If True, send screenshots to AI for better understanding.
    """
    settings = get_settings()
    profile_id = settings.adspower_profile_id

    logger.info("=== ClawBridge Starting ===")
    logger.info("Task: %s", user_prompt)
    logger.info("Profile: %s", profile_id)
    logger.info("Vision mode: %s", "ON" if use_vision else "OFF")

    # 1. Start AdsPower profile
    ws_endpoint = await start_adspower_profile(profile_id)

    # 2. Connect Playwright
    browser, context, page = await connect_playwright(ws_endpoint)

    try:
        # 3. Navigate to Facebook if not already there
        current_url = page.url
        if "facebook.com" not in current_url:
            logger.info("Navigating to Facebook...")
            await page.goto("https://facebook.com", wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(3)

        # 4. Run AI agent loop
        agent = AgentLoop(page, user_prompt, max_steps=max_steps, use_vision=use_vision)
        actions = await agent.run()

        logger.info("=== ClawBridge Finished ===")
        logger.info("Total actions taken: %d", len(actions))
        for i, a in enumerate(actions, 1):
            logger.info("  Step %d: %s - %s", i, a["action"], a.get("reasoning", ""))

    except KeyboardInterrupt:
        logger.info("Interrupted by user.")
    except Exception as e:
        logger.error("Error: %s", e)
    finally:
        await browser.close()
        await close_playwright()
        await stop_adspower_profile(profile_id)


async def interactive_mode(use_vision: bool = False):
    """Interactive mode: browser stays open, user types commands continuously."""
    settings = get_settings()
    profile_id = settings.adspower_profile_id

    logger.info("=== ClawBridge Interactive Mode ===")
    logger.info("Vision mode: %s", "ON" if use_vision else "OFF")
    logger.info("Type your commands in natural language. Type 'quit' or 'exit' to stop.")
    logger.info("Examples:")
    logger.info('  > Lướt facebook và tìm bài viết hỏi về sản phẩm')
    logger.info('  > Like bài viết đầu tiên trên feed')
    logger.info('  > Cuộn xuống và đọc các bình luận')
    logger.info('  > Vào trang https://google.com')
    print()

    ws_endpoint = await start_adspower_profile(profile_id)
    browser, context, page = await connect_playwright(ws_endpoint)

    try:
        while True:
            try:
                user_input = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: input("\n🤖 ClawBridge > ")
                )
            except EOFError:
                break

            user_input = user_input.strip()
            if not user_input:
                continue
            if user_input.lower() in ("quit", "exit", "q"):
                logger.info("Exiting interactive mode...")
                break

            # Special commands
            if user_input.lower() == "status":
                logger.info("Current URL: %s", page.url)
                logger.info("Page title: %s", await page.title())
                continue
            if user_input.lower() == "dom":
                dom_data = await extract_clean_dom(page)
                print(dom_to_json(dom_data, indent=True))
                continue
            if user_input.lower().startswith("goto "):
                url = user_input[5:].strip()
                if not url.startswith("http"):
                    url = "https://" + url
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                logger.info("Navigated to: %s", url)
                continue

            # Run AI agent for this command
            logger.info("Executing: %s", user_input)
            agent = AgentLoop(page, user_input, max_steps=20, use_vision=use_vision)
            actions = await agent.run()
            logger.info("Done! %d actions taken.", len(actions))

    except KeyboardInterrupt:
        logger.info("Interrupted.")
    finally:
        await browser.close()
        await close_playwright()
        await stop_adspower_profile(profile_id)
        logger.info("Browser closed. Goodbye!")


async def test_connection():
    """Test: start browser, navigate to Facebook, extract DOM, then close."""
    settings = get_settings()
    profile_id = settings.adspower_profile_id

    logger.info("=== ClawBridge Connection Test ===")

    ws_endpoint = await start_adspower_profile(profile_id)
    browser, context, page = await connect_playwright(ws_endpoint)

    try:
        await page.goto("https://facebook.com", wait_until="domcontentloaded", timeout=30000)
        title = await page.title()
        logger.info("✅ Page title: %s", title)

        await asyncio.sleep(3)
        dom_data = await extract_clean_dom(page)
        logger.info("✅ Extracted %d elements", dom_data.get("element_count", 0))

        # Print first 5 elements as sample
        for el in dom_data.get("elements", [])[:5]:
            logger.info("  - [%s] %s | %s", el.get("tag"), el.get("aria_label", ""), el.get("text", "")[:50])

        logger.info("✅ Connection test passed!")
    finally:
        await browser.close()
        await close_playwright()
        await stop_adspower_profile(profile_id)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage:")
        print('  python main.py interactive                  (interactive mode)')
        print('  python main.py interactive --vision         (interactive + AI vision)')
        print('  python main.py "Your task description"      (single command)')
        print('  python main.py --vision "Your task"         (single command + vision)')
        print('  python main.py --test                       (test connection)')
        sys.exit(1)

    if sys.argv[1] == "--test":
        asyncio.run(test_connection())
    elif sys.argv[1] == "interactive":
        use_vision = "--vision" in sys.argv
        asyncio.run(interactive_mode(use_vision=use_vision))
    else:
        use_vision = "--vision" in sys.argv
        args = [a for a in sys.argv[1:] if a != "--vision"]
        user_prompt = " ".join(args)
        asyncio.run(run_agent(user_prompt, use_vision=use_vision))
