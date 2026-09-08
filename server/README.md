# Nexus License Server

Máy chủ cấp phép (license) độc lập cho phần mềm Nexus. Server tự sinh key nên
người dùng **không thể tự tạo license full** như cấp phép offline trước đây.

## Luồng xác thực

1. Client (Nexus) gửi thông tin máy (MAC, tên máy) lên server: `POST /api/register`.
2. Server tạo bản ghi máy, sinh **key ngẫu nhiên** gắn với máy + gói + hạn dùng,
   gửi key qua **email admin** đã cấu hình.
3. Người dùng nhập key vào Nexus; client xác thực với server: `POST /api/verify`.
   Server tra DB, kiểm trạng thái `active` + còn hạn. Có thể **hủy kích hoạt**
   bất cứ lúc nào — lần verify sau client bị khóa.

## Cài đặt & chạy

```bat
pip install -r server\requirements.txt
:: copy .env.example -> .env và điền cấu hình email + ADMIN_TOKEN
python server\license_server.py --port 5555
```

Dashboard quản trị: `http://<server>:5555/?token=<ADMIN_TOKEN>`

## API

| Endpoint | Mô tả |
|---|---|
| `POST /api/register` `{mac, machineName, plan}` | Tạo key + gửi email |
| `POST /api/verify` `{mac, key}` | Client xác thực key, trả quyền tính năng |
| `GET  /api/admin/machines?q=&ip=&status=&from=&to=` | Danh sách máy + bộ lọc |
| `POST /api/admin/machines/<id>/activate` | Kích hoạt |
| `POST /api/admin/machines/<id>/deactivate` | Hủy kích hoạt |
| `POST /api/admin/machines/<id>/regen` | Tạo lại key + gửi email |
| `DELETE /api/admin/machines/<id>` | Xóa máy |

## Gói & quyền

- Gói đến 3 tháng: tối đa 2 tài khoản Zalo, chạy 1 tài khoản.
- Gói 6 tháng trở lên / vĩnh viễn: không giới hạn tài khoản + chọn nhiều tài khoản.

## Bảo mật

- Đặt `ADMIN_TOKEN` mạnh khi chạy public; dashboard + API admin yêu cầu token.
- Dữ liệu lưu SQLite tại `server/data/licenses.db` (không commit lên git).
