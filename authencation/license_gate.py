"""Cổng khóa license dùng chung cho các worker nền.

app.py chạy 1 watchdog định kỳ gọi get_server_plan() rồi cập nhật cờ ở đây.
Các worker (gửi lịch, sao chép nhóm, quét tin chưa đọc) kiểm tra is_active()
mỗi vòng lặp: nếu license bị HỦY/HẾT HẠN -> tạm dừng làm việc (không gửi gì),
và tự chạy lại khi license được kích hoạt lại.

Mặc định True (lạc quan) để không chặn oan lúc mới khởi động; watchdog sẽ chỉnh
lại trong vài giây theo trạng thái thực tế từ máy chủ.
"""
import threading

_lock = threading.Lock()
_active = True
_reason = ""


def set_active(value: bool, reason: str = ""):
    global _active, _reason
    with _lock:
        _active = bool(value)
        _reason = str(reason or "")


def is_active() -> bool:
    with _lock:
        return _active


def reason() -> str:
    with _lock:
        return _reason
