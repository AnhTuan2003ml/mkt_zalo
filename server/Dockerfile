# Máy chủ cấp phép Nexus — image chạy trên host (Fly.io / Railway / Render...).
# DB SQLite đặt trên volume bền qua LICENSE_DB_PATH; lần đầu tự seed từ server/seed/.
FROM python:3.12-slim

WORKDIR /app

# Cài phụ thuộc trước để tận dụng cache layer.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Chép toàn bộ mã máy chủ (kèm templates + seed/licenses.db nếu có).
COPY . .

ENV PORT=8080
# Volume bền của host mount vào /data để giữ DB lâu dài.
ENV LICENSE_DB_PATH=/data/licenses.db

EXPOSE 8080

# 1 worker + nhiều thread: phiên đăng nhập & rate-limit lưu in-memory nên phải
# dùng đúng 1 process để nhất quán (không tách qua nhiều worker).
CMD ["gunicorn", "-b", "0.0.0.0:8080", "-w", "1", "--threads", "8", "--timeout", "60", "license_server:app"]
