"""Danh sách gói kích hoạt (dùng cho giao diện chọn gói).

Toàn bộ logic cấp phép OFFLINE trước đây (tự sinh key, gửi email, ký/xác thực
license trên máy khách) đã được CHUYỂN sang máy chủ license — xem:
  - server/license_server.py        (máy chủ sinh key + gửi email + quản trị)
  - authencation/server_license.py  (client gọi máy chủ để đăng ký/xác thực)

Module này chỉ còn giữ bảng gói để hiển thị lựa chọn thời gian trên trang
Kích hoạt. Việc gửi mail/tạo key không còn nằm ở máy khách.
"""
import os

# Gói: key -> thông tin hiển thị. Phải khớp PLAN_OPTIONS trong license_server.py.
ACTIVATION_DURATION_OPTIONS = {
    "3m": {"key": "3m", "label": "3 Tháng", "days": 90, "icon": "📊", "is_permanent": False},
    "6m": {"key": "6m", "label": "6 Tháng", "days": 180, "icon": "📊", "is_permanent": False},
    "lifetime": {"key": "lifetime", "label": "Vĩnh viễn", "days": None, "icon": "💎", "is_permanent": True},
}

DEFAULT_ACTIVATION_OPTION_KEY = os.getenv("DEFAULT_ACTIVATION_OPTION_KEY", "3m")

_ORDER = ["3m", "6m", "lifetime"]


def get_activation_duration_options():
    """Danh sách gói theo thứ tự hiển thị."""
    return [ACTIVATION_DURATION_OPTIONS[k] for k in _ORDER]


def normalize_requested_duration(duration_key=None):
    key = str(duration_key or "").strip().lower()
    if key in ACTIVATION_DURATION_OPTIONS:
        return dict(ACTIVATION_DURATION_OPTIONS[key])
    if DEFAULT_ACTIVATION_OPTION_KEY in ACTIVATION_DURATION_OPTIONS:
        return dict(ACTIVATION_DURATION_OPTIONS[DEFAULT_ACTIVATION_OPTION_KEY])
    return dict(ACTIVATION_DURATION_OPTIONS["3m"])
