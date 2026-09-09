# Triển khai máy chủ cấp phép Nexus + tên miền cố định `nexus.is-a.dev`

Mục tiêu: bỏ cloudflared tạm, có tên miền **cố định** `https://nexus.is-a.dev`, luôn online,
giữ nguyên **DB hiện tại** và bạn **toàn quyền sửa DB**.

Kiến trúc: `client (Nexus.exe)` → `https://nexus.is-a.dev` → (DNS is-a.dev) → `nexus-license.fly.dev` (Fly.io) → `license_server` + DB SQLite trên volume.

---

## A. Deploy máy chủ lên Fly.io (miễn phí, có volume giữ DB)

> Cần: tài khoản Fly.io (fly.io) + cài `flyctl`. Fly build từ thư mục local nên **DB trong
> `server/seed/licenses.db` được đóng gói mà KHÔNG cần commit lên git** (an toàn, không lộ key).

1. Cài flyctl và đăng nhập (chạy trong terminal của bạn, thư mục `server/`):
   ```
   # Windows PowerShell:
   iwr https://fly.io/install.ps1 -useb | iex
   fly auth login
   cd server
   ```

2. Khởi tạo app (KHÔNG deploy vội) — đặt tên trùng với `fly.toml` (`nexus-license`);
   nếu tên đã có người dùng, đổi tên trong `fly.toml` **và** trong `deploy/nexus.json`:
   ```
   fly launch --no-deploy --copy-config --name nexus-license --region sin
   ```

3. Tạo volume bền cho DB:
   ```
   fly volumes create licdata --size 1 --region sin
   ```

4. Đặt biến bí mật (KHÔNG để trong image, KHÔNG commit lên git). **Lấy đúng giá trị từ
   `server/.env` của bạn** thay vào các chỗ `<...>` bên dưới (đừng dán mật khẩu vào file này):
   ```
   fly secrets set \
     SENDMAIL_USER="<SENDMAIL_USER trong .env>" \
     SENDMAIL_PASS="<SENDMAIL_PASS - App Password Gmail>" \
     SMTP_FROM_NAME="Nexus" \
     ADMIN_EMAILS="<ADMIN_EMAILS trong .env>" \
     ADMIN_CREDENTIALS="<ADMIN_CREDENTIALS trong .env>" \
     ADMIN_TOKEN="<ADMIN_TOKEN trong .env>" \
     SESSION_SECRET="$(python -c 'import secrets;print(secrets.token_hex(32))')"
   ```
   > Các giá trị này chỉ nằm ở: `server/.env` (đã gitignore) và trong Fly secrets. Tuyệt đối
   > không viết vào file được commit.

5. Deploy (đóng gói kèm `seed/licenses.db` → lần đầu server tự nạp DB hiện tại vào volume):
   ```
   fly deploy
   ```
   Kiểm tra: mở `https://nexus-license.fly.dev/health` phải trả `{"ok": true}`.
   Đăng nhập dashboard `https://nexus-license.fly.dev/login` bằng email + mật khẩu admin.

6. Gắn tên miền + chứng chỉ HTTPS cho `nexus.is-a.dev` (làm SAU khi mục B đã merge):
   ```
   fly certs add nexus.is-a.dev
   ```

---

## B. Đăng ký tên miền `nexus.is-a.dev` (is-a.dev — DNS miễn phí)

> is-a.dev chỉ cấp DNS. File trỏ CNAME sang app Fly ở trên.

1. Fork repo `https://github.com/is-a-dev/register`.
2. Thêm file `domains/nexus.json` với nội dung y hệt `server/deploy/nexus.json`:
   ```json
   {
     "owner": { "username": "AnhTuan2003ml" },
     "records": { "CNAME": "nexus-license.fly.dev" }
   }
   ```
   (Nếu đổi tên app Fly thì sửa CNAME cho khớp. Nếu `nexus` đã bị lấy, đổi tên file, ví dụ
   `nexus-app.json` → tên miền sẽ là `nexus-app.is-a.dev`.)
3. Mở Pull Request, chờ maintainer duyệt/merge (vài giờ–vài ngày). Merge xong DNS chạy sau ít phút.
4. Sau khi DNS chạy, quay lại bước A6 chạy `fly certs add nexus.is-a.dev`.

---

## C. Client đã trỏ sẵn về tên miền mới

`.env` client đã đổi: `LICENSE_SERVER_URL=https://nexus.is-a.dev` và đã build lại `dist/Nexus.exe`.
Sau khi A + B xong, client tự xác thực qua tên miền cố định — không cần cloudflared nữa.

> Tạm thời trong lúc chờ is-a.dev merge, có thể cho client trỏ thẳng
> `LICENSE_SERVER_URL=https://nexus-license.fly.dev` để chạy ngay, rồi đổi lại `nexus.is-a.dev`.

---

## Toàn quyền sửa DB

- **Qua Dashboard**: kích hoạt / hủy / tạo key / đổi gói / xóa máy / thu hồi key cũ / quản lý email.
- **Sửa trực tiếp file DB** trên volume:
  ```
  fly ssh console          # vào máy chủ
  # DB nằm ở /data/licenses.db — có thể dùng sqlite3 để sửa
  ```
  Tải DB về máy: `fly ssh sftp get /data/licenses.db ./licenses.db`
  Đẩy DB đã sửa lên: `fly ssh sftp shell` rồi `put licenses.db /data/licenses.db`.

## Lựa chọn khác (thay Fly.io)
- **Railway**: `railway up` từ thư mục `server/` (cũng build từ local, có Volume). Đặt biến env như bước A4, mount volume vào `/data`, `LICENSE_DB_PATH=/data/licenses.db`.
- **Render**: deploy từ git (cần disk trả phí để giữ DB) — Dockerfile dùng chung.
