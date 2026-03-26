#!/bin/bash
# ClawBridge CLI wrapper for OpenClaw on WSL
# Usage: ./clawbridge.sh <command> [args]
#
# Commands:
#   status              - Get browser status
#   dom                 - Get page DOM elements
#   screenshot          - Get page screenshot (base64)
#   navigate <url>      - Navigate to URL
#   click <target_id>   - Click element by cb_id
#   type <target_id> <text> - Type text into element
#   scroll <up|down>    - Scroll page
#   hover <target_id>   - Hover over element
#   key <key_name>      - Press keyboard key (Enter, Tab, Escape...)
#   select <target_id> <value> - Select dropdown option
#   wait                - Wait for page load

# Auto-detect Windows host IP from WSL
# Priority: CLAWBRIDGE_HOST env var > default gateway (most reliable) > localhost
if [ -n "$CLAWBRIDGE_HOST" ]; then
    WIN_HOST="$CLAWBRIDGE_HOST"
else
    WIN_HOST=$(ip route show default 2>/dev/null | awk '{print $3}')
    [ -z "$WIN_HOST" ] && WIN_HOST="localhost"
fi

BRIDGE_URL="http://${WIN_HOST}:8899"
CONTENT_TYPE="Content-Type: application/json"

case "$1" in
    status)
        curl -s "$BRIDGE_URL/status"
        ;;
    snapshot)
        curl -s "$BRIDGE_URL/snapshot"
        ;;
    page_text)
        curl -s "$BRIDGE_URL/page_text"
        ;;
    scroll_and_snapshot)
        SCROLLS="${2:-3}"
        curl -s "$BRIDGE_URL/scroll_and_snapshot?scrolls=$SCROLLS"
        ;;
    dom)
        curl -s "$BRIDGE_URL/dom"
        ;;
    screenshot)
        curl -s "$BRIDGE_URL/screenshot"
        ;;
    navigate)
        curl -s -X POST "$BRIDGE_URL/navigate" \
            -H "$CONTENT_TYPE" \
            -d "{\"url\": \"$2\"}"
        ;;
    click)
        curl -s -X POST "$BRIDGE_URL/execute" \
            -H "$CONTENT_TYPE" \
            -d "{\"action\": \"click\", \"target_id\": \"$2\"}"
        ;;
    type)
        curl -s -X POST "$BRIDGE_URL/execute" \
            -H "$CONTENT_TYPE" \
            -d "{\"action\": \"type\", \"target_id\": \"$2\", \"value\": \"$3\"}"
        ;;
    scroll)
        DIR="${2:-down}"
        curl -s -X POST "$BRIDGE_URL/execute" \
            -H "$CONTENT_TYPE" \
            -d "{\"action\": \"scroll\", \"value\": \"$DIR\"}"
        ;;
    hover)
        curl -s -X POST "$BRIDGE_URL/execute" \
            -H "$CONTENT_TYPE" \
            -d "{\"action\": \"hover\", \"target_id\": \"$2\"}"
        ;;
    key)
        curl -s -X POST "$BRIDGE_URL/execute" \
            -H "$CONTENT_TYPE" \
            -d "{\"action\": \"key_press\", \"value\": \"$2\"}"
        ;;
    select)
        curl -s -X POST "$BRIDGE_URL/execute" \
            -H "$CONTENT_TYPE" \
            -d "{\"action\": \"select\", \"target_id\": \"$2\", \"value\": \"$3\"}"
        ;;
    find_text)
        curl -s "$BRIDGE_URL/find_text?q=$(echo "$2" | sed 's/ /%20/g')"
        ;;
    copy_aff_link)
        curl -s "$BRIDGE_URL/copy_aff_link"
        ;;
    comment|fb_comment)
        # Usage: comment <target_id> <text> [submit]
        # submit: true/false (default: false)
        # Works on any contenteditable: Facebook, Twitter, LinkedIn, forums, etc.
        SUBMIT="${4:-false}"
        curl -s -X POST "$BRIDGE_URL/comment" \
            -H "$CONTENT_TYPE" \
            -d "{\"target_id\": \"$2\", \"text\": \"$3\", \"submit\": $SUBMIT}"
        ;;
    click_xy)
        curl -s -X POST "$BRIDGE_URL/click_xy" \
            -H "$CONTENT_TYPE" \
            -d "{\"x\": $2, \"y\": $3}"
        ;;
    wait)
        curl -s -X POST "$BRIDGE_URL/execute" \
            -H "$CONTENT_TYPE" \
            -d "{\"action\": \"wait\"}"
        ;;
    *)
        echo "ClawBridge CLI - Human-like browser automation"
        echo ""
        echo "Usage: $0 <command> [args]"
        echo ""
        echo "Commands:"
        echo "  snapshot                  - ★ Full page (elements + post text)"
        echo "  scroll_and_snapshot [N]   - ★★ Scroll N times + lazy load + snapshot"
        echo "  page_text                 - Post/article text content only"
        echo "  dom                       - Interactive elements only"
        echo "  screenshot                - Page screenshot"
        echo "  status                    - Browser status"
        echo "  navigate <url>            - Go to URL"
        echo "  click <cb_id>             - Click element"
        echo "  find_text <text>          - Find element by text → coordinates"
        echo "  click_xy <x> <y>          - Click at coordinates"
        echo "  copy_aff_link             - Get Shopee affiliate link"
        echo "  comment <cb_id> <text> [true]    - ★ Type into contenteditable (any site)"
        echo "  type <cb_id> <text>       - Type text"
        echo "  scroll [up|down]          - Scroll page"
        echo "  hover <cb_id>             - Hover element"
        echo "  key <key_name>            - Press key"
        echo "  select <cb_id> <value>    - Select option"
        echo "  wait                      - Wait for load"
        ;;
esac
