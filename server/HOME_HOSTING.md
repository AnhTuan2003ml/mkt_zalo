# Chạy máy chủ cấp phép TẠI MÁY BẠN với TÊN MIỀN CỐ ĐỊNH (free)

Mục tiêu: code + DB **luôn nằm ở máy bạn**, có **1 URL cố định** (không đổi mỗi lần
chạy), **mất điện bật lại là tự chạy đúng URL cũ** — không phải lấy URL mới.

Cách dùng: **ngrok static domain** (miễn phí, HTTPS, không cần thẻ, chạy được sau CGNAT).
> Vì sao không dùng `nexus.is-a.dev`? is-a.dev **cấm** trỏ về tunnel
> (`.cfargotunnel.com`, `.trycloudflare.com`, `.ts.net`...) — xem `util/disallowed-cnames.json`
> trong repo is-a-dev/register. Máy nhà không có IP tĩnh công khai nên is-a.dev không dùng được.

---

## Bước 1 — Đăng ký ngrok + lấy domain cố định (làm 1 lần)
1. Vào https://dashboard.ngrok.com/signup — đăng ký (miễn phí, **không cần thẻ**).
2. Cài ngrok: https://ngrok.com/download (Windows: tải về, giải nén `ngrok.exe`, để vào
   `C:\Windows` hoặc thêm vào PATH để gõ `ngrok` ở đâu cũng được).
3. Gắn authtoken (chỉ 1 lần): Dashboard → **Your Authtoken** → copy, rồi chạy:
   ```
   ngrok config add-authtoken <authtoken-cua-ban>
   ```
4. Lấy **domain tĩnh miễn phí**: Dashboard → **Domains** → **+ Create Domain** (hoặc
   "New Domain"). Bạn sẽ được 1 domain dạng `nexus-xxxx.ngrok-free.app`. **Copy domain này.**

## Bước 2 — Cấu hình script chạy
Mở file `server/run-license.bat`, sửa dòng:
```
set "NGROK_DOMAIN=YOUR-DOMAIN.ngrok-free.app"
```
thành domain bạn vừa lấy, ví dụ:
```
set "NGROK_DOMAIN=nexus-xxxx.ngrok-free.app"
```

## Bước 3 — Báo domain cho tôi để trỏ client
Gửi tôi domain (`https://nexus-xxxx.ngrok-free.app`). Tôi sẽ đặt
`LICENSE_SERVER_URL=https://nexus-xxxx.ngrok-free.app` trong `.env` client và **build lại
`Nexus.exe`**. Từ đó client luôn xác thực qua URL cố định này.

## Bước 4 — Chạy
Nhấp đúp `server/run-license.bat`. Nó mở 2 cửa sổ: license server (cổng 5555) + ngrok.
Mở thử `https://nexus-xxxx.ngrok-free.app/health` → ra `{"ok": true}` là chạy.

---

## Tự chạy lại khi MẤT ĐIỆN / khởi động máy (quan trọng)
Để mất điện → bật máy là tự chạy, **không thao tác gì**:

**Cách A — Thư mục Startup (đơn giản):**
1. Nhấn `Win + R`, gõ `shell:startup`, Enter → mở thư mục Startup.
2. Chuột phải `run-license.bat` → **Tạo shortcut** → kéo shortcut vào thư mục Startup.
   (Khi bạn đăng nhập Windows, script tự chạy.)

**Cách B — Task Scheduler (chạy cả khi chưa đăng nhập):**
1. Mở **Task Scheduler** → Create Task.
2. Trigger: **At startup**. Action: Start a program → chọn `run-license.bat`.
3. Tick "Run whether user is logged on or not".

> Nếu muốn máy tự bật lại sau khi có điện: vào BIOS bật **"Restore on AC Power Loss = Power On"**.

---

## Hạn mức ngrok free & cách tránh vượt
- Free: **1 domain tĩnh, 1 agent, ~20.000 request/tháng**.
- Client đã được chỉnh **verify mỗi 1 giờ** (thay vì 60 giây) → mỗi máy ~720 request/tháng
  → đủ cho ~27 máy. Nếu nhiều máy hơn, tăng biến `LICENSE_REVERIFY_SECONDS` trong `.env`
  client (vd `21600` = 6 giờ → ~120 request/tháng/máy).
- Server tắt (mất điện) thì client vẫn chạy nhờ **grace 3 ngày** (cache), không bị khóa oan.

## Toàn quyền DB
DB là `server/data/licenses.db` **trên máy bạn** — mở bằng *DB Browser for SQLite* để sửa
bất cứ gì, hoặc quản lý qua Dashboard (`/login`). Không ai đụng vào được.
