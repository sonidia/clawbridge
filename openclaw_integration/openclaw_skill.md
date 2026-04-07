# ClawBridge - Universal Human-like Browser Automation Skill

You have access to ClawBridge, a browser automation tool that performs actions like a real human (bezier mouse movements, natural typing delays, anti-detection). Use it to browse **any website** through an anti-detect browser.

**Universal**: Content extraction works automatically on any website — social media, e-commerce, news, forums, blogs, etc. No site-specific configuration needed.

## API Base URL

```
http://localhost:8899
```

> WSL2: nếu localhost không hoạt động, dùng `ip route show default | awk '{print $3}'` để lấy IP Windows host.

## Endpoints

### ★ /snapshot (RECOMMENDED)

```bash
curl -s http://localhost:8899/snapshot
```

Returns **everything**: interactive elements (with `cb_id`) AND text content. Auto-detects and extracts:

- **posts[]** — Social media posts, forum threads, blog articles (author, text, timestamp)
- **products[]** — E-commerce product listings (name, price, link)
- **text_blocks[]** — Generic text content for any other page type

### ★★ /scroll_and_snapshot (for lazy-loading sites)

```bash
curl -s "http://localhost:8899/scroll_and_snapshot?scrolls=3"
```

Scrolls down N times, waits for lazy content to load, then takes a full snapshot. **Use this for any site that loads content on scroll** (infinite scroll, pagination, etc.).

### /page_text

```bash
curl -s http://localhost:8899/page_text
```

Text content only (posts/products/text_blocks). Auto-waits for JS-rendered content.

### /dom

```bash
curl -s http://localhost:8899/dom
```

Interactive elements only (buttons, links, inputs with `cb_id`). Lighter, no text content.

### /navigate

```bash
curl -s -X POST http://localhost:8899/navigate -H "Content-Type: application/json" -d '{"url": "https://example.com"}'
```

### /execute (perform browser action)

```bash
curl -s -X POST http://localhost:8899/execute -H "Content-Type: application/json" -d '{"action": "<ACTION>", "target_id": "<CB_ID>", "value": "<VALUE>"}'
```

Actions:

- `click` — Click element (needs target_id)
- `double_click` — Double-click element
- `hover` — Hover over element
- `type` — Type text (needs target_id + value)
- `scroll` — Scroll page (value: "up" or "down")
- `select` — Select dropdown (needs target_id + value)
- `key_press` — Press key (value: "Enter", "Tab", "Escape"...)
- `upload` — Upload file(s). Target can be `<input type="file">`, a wrapper, or a button that opens a file chooser (needs target_id + value as file path or list of paths).
- `download` — Click element to download and save file (needs target_id, optional value as save path)
- `wait` — Wait for page load

### /comment (type into rich-text editors)

```bash
curl -s -X POST http://localhost:8899/comment -H "Content-Type: application/json" \
  -d '{"target_id":"cb_XX","text":"Hello world!","submit":true}'
```

Works on **any** contenteditable/rich-text editor: social media comment boxes, forum reply forms, CMS editors (DraftJS, Lexical, ProseMirror, etc.). `submit: true` presses Enter after typing.

### /find_text (find element by visible text)

```bash
curl -s "http://localhost:8899/find_text?q=Submit"
```

Searches entire page including Shadow DOM. Returns `center_x`/`center_y` for clicking.

### /click_xy (click by coordinates)

```bash
curl -s -X POST http://localhost:8899/click_xy -H "Content-Type: application/json" -d '{"x": 950, "y": 420}'
```

### /copy_aff_link (Shopee affiliate extension)

```bash
curl -s http://localhost:8899/copy_aff_link
```

Clicks the extension button, waits for affiliate link, returns it.

### /screenshot

```bash
curl -s http://localhost:8899/screenshot
```

### /status

```bash
curl -s http://localhost:8899/status
```

## Workflow Pattern (works on ANY site)

1. **Navigate** → `POST /navigate` → Go to any URL
2. **Snapshot** → `GET /snapshot` → See elements + content (auto-detected)
3. **Decide action** → Pick element by `cb_id` from snapshot
4. **Execute** → `POST /execute` → Perform click/type/scroll
5. **Snapshot again** → See updated page state
6. **Repeat** until task done

## Response Format (auto-detected)

```json
{
  "success": true,
  "url": "https://any-website.com/page",
  "elements": [
    {
      "id": "cb_1",
      "tag": "input",
      "placeholder": "Search",
      "bbox": { "x": 300, "y": 10, "w": 250, "h": 36 }
    },
    {
      "id": "cb_5",
      "tag": "a",
      "text": "Read more",
      "bbox": { "x": 100, "y": 415, "w": 80, "h": 20 }
    }
  ],
  "posts": [
    {
      "post_id": "post_0",
      "author": "User Name",
      "timestamp": "2 hours ago",
      "text": "Content of the post or article...",
      "reactions": "",
      "comments": "",
      "link": "/post/123"
    }
  ],
  "products": [
    {
      "product_id": "prod_0",
      "name": "Product Name",
      "price": "₫28.990.000",
      "rating": "",
      "link": "/product/..."
    }
  ],
  "text_blocks": ["Fallback text when no posts/products detected"]
}
```

**Auto-classification**: The engine automatically detects whether content is posts (has authors/long text) or products (has prices) and fills the appropriate array.

## Example: Browse any social media

```bash
# 1. Navigate
curl -s -X POST http://localhost:8899/navigate -d '{"url":"https://any-social-site.com/feed"}'

# 2. Scroll to load more content + snapshot
curl -s "http://localhost:8899/scroll_and_snapshot?scrolls=3"
# Auto-extracts: posts[] with author, text, timestamp

# 3. Interact with a post
curl -s -X POST http://localhost:8899/execute -d '{"action":"click","target_id":"cb_12"}'

# 4. Comment (works on any contenteditable editor)
curl -s -X POST http://localhost:8899/comment \
  -d '{"target_id":"cb_XX","text":"Great post!","submit":true}'
```

## Example: Browse any e-commerce site

```bash
# 1. Navigate to search results
curl -s -X POST http://localhost:8899/navigate -d '{"url":"https://any-shop.com/search?q=iphone"}'

# 2. Scroll for lazy-loaded products
curl -s "http://localhost:8899/scroll_and_snapshot?scrolls=3"
# Auto-extracts: products[] with name, price, link

# 3. Click a product
curl -s -X POST http://localhost:8899/execute -d '{"action":"click","target_id":"cb_15"}'
```

## Example: Full workflow — product → affiliate link → comment

```bash
# 1. Navigate to product page
curl -s -X POST http://localhost:8899/navigate -d '{"url":"https://shopee.vn/product/123/456"}'
# 2. Get affiliate link (extension)
curl -s http://localhost:8899/copy_aff_link
# 3. Navigate to social media post
curl -s -X POST http://localhost:8899/navigate -d '{"url":"https://facebook.com/groups/..."}'
# 4. Snapshot to find comment box
curl -s http://localhost:8899/snapshot
# 5. Post comment with affiliate link
curl -s -X POST http://localhost:8899/comment \
  -d '{"target_id":"cb_XX","text":"Check this out: https://s.shopee.vn/abc123","submit":true}'
```

## Important Notes

- All actions simulate human behavior (bezier mouse movements, gaussian typing delays)
- Browser runs through AdsPower anti-detect profiles
- Content extraction is **universal** — auto-detects posts/products/text on any website
- Auto-waits for JS-rendered dynamic content before extraction
- Use `/snapshot` for most pages, `/scroll_and_snapshot` for infinite scroll
- `/comment` works on any contenteditable editor (not just one specific site)
- `/fb_comment` is kept as backward-compatible alias for `/comment`
