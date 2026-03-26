# Hướng dẫn kết nối OpenClaw (WSL) ↔ ClawBridge (Windows)

## Kiến trúc

```
OpenClaw (WSL Ubuntu)
    │
    │  exec / web_fetch tool
    │  (curl commands)
    │
    ▼
ClawBridge API (Windows:8899)
    │
    │  Playwright CDP
    │
    ▼
AdsPower Browser (Windows)
    │
    ▼
Facebook / Web (human-like actions)
```

## Bước 1: Chạy ClawBridge trên Windows

Mở PowerShell:
```powershell
cd E:\chaytestsoft\openclaw\ClawBridge
.\venv\Scripts\activate
python api_server.py --port 8899
```

Đợi thấy: `Server running on http://0.0.0.0:8899`

## Bước 2: Tìm Windows Host IP từ WSL

Lệnh chính xác nhất — lấy IP default gateway (= Windows host):
```bash
WIN_HOST=$(ip route show default | awk '{print $3}')
echo "Windows IP: $WIN_HOST"
```

Ví dụ kết quả: `172.29.31.1` hoặc tương tự.

Test kết nối:
```bash
curl -s http://$WIN_HOST:8899/status
```

Nếu thấy `{"success": true, ...}` → OK!

Lưu IP vào biến môi trường cho tiện:
```bash
echo "export CLAWBRIDGE_HOST=$WIN_HOST" >> ~/.bashrc
source ~/.bashrc
```

## Bước 3: Nếu không kết nối được

Mở **PowerShell as Admin** trên Windows và cho phép firewall:
```powershell
New-NetFirewallRule -DisplayName "ClawBridge API" -Direction Inbound -LocalPort 8899 -Protocol TCP -Action Allow
```

Sau đó thử lại từ WSL.

## Bước 4: Cài helper script (tùy chọn)

```bash
# Copy script vào WSL
cp /mnt/e/chaytestsoft/openclaw/ClawBridge/openclaw_integration/clawbridge.sh ~/clawbridge.sh
chmod +x ~/clawbridge.sh

# Test
~/clawbridge.sh status
~/clawbridge.sh dom
~/clawbridge.sh scroll down
```

## Bước 5: Cấu hình OpenClaw

### Cách A: Dùng exec tool (đơn giản nhất)

Trong OpenClaw config (`~/.openclaw/openclaw.json`), đảm bảo exec tool được bật:
```json
{
  "tools": {
    "exec": {
      "enabled": true
    }
  }
}
```

Sau đó nhắn tin cho OpenClaw (qua WhatsApp/Telegram/WebChat):
```
Hãy dùng exec tool để chạy các lệnh curl tới ClawBridge API tại http://localhost:8899

Bước 1: Chạy `curl -s http://localhost:8899/snapshot` để xem trang web (elements + nội dung bài viết)
Bước 2: Đọc posts[] để biết nội dung, dùng elements[] để biết nút bấm
Bước 3: Chạy `curl -s -X POST http://localhost:8899/execute -H "Content-Type: application/json" -d '{"action":"scroll","value":"down"}'` để cuộn
Bước 4: Chạy snapshot lại, tìm bài viết phù hợp, click/type nếu cần
Bước 5: Lặp lại cho đến khi xong

Nhiệm vụ: Lướt Facebook và tìm bài viết hỏi về sản phẩm
```

### Cách B: Dùng web_fetch tool

Trong OpenClaw config:
```json
{
  "tools": {
    "web": {
      "fetch": {
        "enabled": true
      }
    }
  }
}
```

Nhắn tin cho OpenClaw:
```
Dùng web_fetch để GET http://localhost:8899/snapshot → xem trang web (elements + nội dung bài viết)
Dùng exec để POST http://localhost:8899/execute → thực hiện hành động

Nhiệm vụ: Like bài viết đầu tiên trên Facebook
```

### Cách C: Dùng browser tool trực tiếp (không qua ClawBridge)

OpenClaw có sẵn browser tool. Cấu hình nó kết nối tới AdsPower:

```json
{
  "browser": {
    "enabled": true,
    "profiles": {
      "adspower": {
        "cdpUrl": "ws://localhost:54524/devtools/browser/..."
      }
    }
  }
}
```

⚠️ Cách C KHÔNG có human-like behaviors (bezier mouse, anti-detection). Nên dùng Cách A hoặc B.

## Bước 6: Sử dụng

Nhắn tin cho OpenClaw qua bất kỳ kênh nào (WhatsApp, Telegram, WebChat):

```
Lướt Facebook của tôi và tìm những bài viết đang hỏi về sản phẩm
```

OpenClaw sẽ:
1. Gọi `exec` → `curl http://localhost:8899/snapshot` → xem trang (elements + text bài viết)
2. Đọc `posts[]` → biết nội dung, `elements[]` → biết nút bấm
3. Gọi `exec` → `curl -X POST .../execute` → thực hiện scroll/click/type
4. Lặp lại cho đến khi tìm được

## Lệnh nhanh (từ WSL)

```bash
# ★ Xem trang (elements + nội dung bài viết)
~/clawbridge.sh snapshot

# Chỉ xem nội dung text bài viết
~/clawbridge.sh page_text

# Chỉ xem interactive elements
~/clawbridge.sh dom

# Cuộn xuống
~/clawbridge.sh scroll down

# Click phần tử
~/clawbridge.sh click cb_5

# Gõ text
~/clawbridge.sh type cb_3 "Hello world"

# Chuyển trang
~/clawbridge.sh navigate "https://google.com"

# Nhấn Enter
~/clawbridge.sh key Enter
```
