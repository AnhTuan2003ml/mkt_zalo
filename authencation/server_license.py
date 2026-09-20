"""Xác thực license qua MÁY CHỦ (toàn bộ việc sinh key + gửi email nằm ở server).

Cấu hình LICENSE_SERVER_URL trong .env để trỏ tới máy chủ license.

Luồng:
- register_with_server(plan): gửi MAC + tên máy lên server -> server sinh key,
  gửi email admin.
- verify_with_server(key): server kiểm key -> trả quyền tính năng; lưu cache
  cục bộ để còn dùng được khi tạm mất mạng (grace period).
- get_server_plan(): trả quyền hiện tại (ưu tiên verify online, fallback cache).
"""
import hashlib
import json
import os
import socket
import sys
import uuid
from datetime import datetime

import requests


def _load_client_env():
    """Nạp .env của client vào os.environ (LICENSE_SERVER_URL, ...).

    Hỗ trợ cả khi chạy source lẫn khi đã đóng gói PyInstaller (frozen): .env được
    nhúng và giải nén vào sys._MEIPASS, hoặc đặt cạnh Nexus.exe. Dùng setdefault
    nên không đè biến môi trường đã có sẵn.
    """
    candidates = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            candidates.append(os.path.join(meipass, ".env"))
        candidates.append(os.path.join(os.path.dirname(sys.executable), ".env"))
    # Chạy từ source: .env ở thư mục gốc dự án (cha của thư mục authencation).
    candidates.append(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
    for env_path in candidates:
        if not env_path or not os.path.exists(env_path):
            continue
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
            return env_path
        except Exception as exc:
            print(f"[server_license] Lỗi đọc .env ({env_path}): {exc}")
    return ""


_LOADED_ENV_PATH = _load_client_env()

# Bỏ qua system proxy (tool bắt gói gây lỗi SSL) — gọi thẳng.
NO_PROXY = {"http": None, "https": None}
GRACE_SECONDS = 3 * 24 * 3600     # cho phép dùng cache tối đa 3 ngày khi mất mạng


def _reverify_seconds():
    """Giãn nhịp verify online (giây). Mặc định 5 phút để khi HỦY KÍCH HOẠT trên
    máy chủ thì client khóa trong ~5 phút. Chỉnh qua LICENSE_REVERIFY_SECONDS:
    - Đặt nhỏ (vd 60) = khóa nhanh hơn nhưng nhiều request tới tunnel.
    - Đặt lớn (vd 3600) = ít request (hợp ngrok free 20k/tháng khi nhiều máy)."""
    try:
        v = int(os.getenv("LICENSE_REVERIFY_SECONDS", "300") or 300)
    except (TypeError, ValueError):
        v = 300
    return max(60, v)


REVERIFY_SECONDS = _reverify_seconds()


def get_server_url():
    return (os.getenv("LICENSE_SERVER_URL", "") or "").strip().rstrip("/")


def is_server_mode():
    return bool(get_server_url())


def _cache_path():
    """Nơi lưu cache license: %LOCALAPPDATA%/Nexus/.license (Windows), fallback ~."""
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or os.path.expanduser("~")
    d = os.path.join(base, "Nexus", ".license")
    try:
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, ".server_license_cache")
    except Exception:
        return os.path.join(os.path.expanduser("~"), ".nexus_server_license_cache")


def _read_cache():
    try:
        with open(_cache_path(), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _write_cache(data):
    try:
        with open(_cache_path(), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as exc:
        print(f"[server_license] Không ghi được cache: {exc}")


def _stable_machine_id():
    """ID máy ỔN ĐỊNH, KHÔNG đổi theo card mạng/VPN/khởi động lại.

    Ưu tiên Windows MachineGuid (registry) — cố định theo cài đặt Windows. Trả ""
    nếu không đọc được (sẽ fallback sang MAC phần cứng)."""
    try:
        import winreg  # chỉ có trên Windows
        access = winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0)
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SOFTWARE\Microsoft\Cryptography", 0, access) as k:
            guid, _ = winreg.QueryValueEx(k, "MachineGuid")
            g = str(guid or "").strip()
            if g:
                return g
    except Exception:
        pass
    return ""


def _device_mac():
    """Chuỗi định danh dạng MAC, ỔN ĐỊNH theo máy.

    Nếu lấy được MachineGuid -> dựng MAC tất định từ nó (không đổi theo card mạng
    /VPN) -> hết cảnh "kích hoạt xong lại đòi nhập key". Không có thì mới fallback
    sang MAC phần cứng ``uuid.getnode()`` (có thể không ổn định)."""
    sid = _stable_machine_id()
    if sid:
        h = hashlib.md5(("nexus:" + sid).encode("utf-8")).hexdigest()[:12].upper()
        # Byte đầu: đặt bit locally-administered (|0x02), bỏ bit multicast (&0xFE).
        first = (int(h[:2], 16) | 0x02) & 0xFE
        h = f"{first:02X}" + h[2:]
        return ":".join(h[i:i + 2] for i in range(0, 12, 2))
    try:
        return _format_mac(uuid.getnode())
    except Exception:
        return ""


def get_machine_info():
    """MAC (định danh ổn định) + tên máy + IP nội bộ của thiết bị hiện tại."""
    try:
        mac = _device_mac()
    except Exception:
        mac = ""
    try:
        name = socket.gethostname()
    except Exception:
        name = ""
    ip = ""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
    except Exception:
        ip = "127.0.0.1"
    return {"mac": mac, "machineName": name, "ip": ip}


def _format_mac(mac_int):
    mac_hex = f"{mac_int:012X}"
    return ":".join(mac_hex[i:i + 2] for i in range(0, 12, 2))


def register_with_server(plan_key="1m", timeout=20, confirm_change=False):
    """Gửi thông tin máy lên server để nhận key qua email.

    ``confirm_change=True``: xác nhận ĐỔI gói khi máy đang có key còn hiệu lực
    (server sẽ thay key hiện tại thay vì hỏi lại)."""
    url = get_server_url()
    if not url:
        return {"success": False, "error": "Chưa cấu hình LICENSE_SERVER_URL."}
    info = get_machine_info()
    try:
        resp = requests.post(
            f"{url}/api/register",
            json={"mac": info["mac"], "machineName": info["machineName"], "plan": plan_key,
                  "confirmChange": bool(confirm_change)},
            timeout=timeout, proxies=NO_PROXY,
        )
        return resp.json()
    except Exception as exc:
        return {"success": False, "error": f"Không kết nối được máy chủ cấp phép: {exc}"}


def verify_with_server(key, timeout=20):
    """Xác thực key với server; lưu cache quyền nếu hợp lệ."""
    url = get_server_url()
    if not url:
        return {"valid": False, "error": "Chưa cấu hình LICENSE_SERVER_URL."}
    info = get_machine_info()
    # QUY ĐỔI KEY CŨ: nếu cache còn MAC cũ (bản trước dùng uuid.getnode) khác MAC
    # ổn định hiện tại -> gửi kèm prevMac để server DỜI kích hoạt cũ sang MAC mới,
    # KHÔNG bắt nhập lại key.
    payload = {"mac": info["mac"], "key": key, "machineName": info["machineName"]}
    prev_mac = str((_read_cache() or {}).get("mac") or "").strip()
    if prev_mac and prev_mac.upper() != str(info["mac"]).upper():
        payload["prevMac"] = prev_mac
    try:
        resp = requests.post(
            f"{url}/api/verify",
            json=payload,
            timeout=timeout, proxies=NO_PROXY,
        )
        try:
            data = resp.json()
        except Exception:
            # Phản hồi KHÔNG phải JSON (ngrok/proxy/tunnel trả trang HTML, hoặc máy
            # chủ lỗi) = lỗi TẠM, KHÔNG phải license sai -> đánh dấu softError để
            # phía trên dùng cache, tránh đòi nhập lại key oan.
            data = {"valid": False, "error": f"HTTP {resp.status_code}"}
            data["softError"] = True
        # 429 = bị rate-limit/tạm khóa (nhiều máy chung IP / verify quá dày). 5xx =
        # máy chủ lỗi. CẢ HAI là lỗi TẠM -> dùng cache, KHÔNG khóa.
        _status = getattr(resp, "status_code", 200)
        if _status == 429 or _status >= 500:
            data["softError"] = True
    except Exception as exc:
        return {"valid": False, "error": f"Không kết nối được máy chủ cấp phép: {exc}"}

    if data.get("valid"):
        _write_cache({
            "mac": info["mac"],
            "key": key,
            "plan": data.get("plan"),
            "planLabel": data.get("planLabel"),
            "isPermanent": bool(data.get("isPermanent")),
            "expiry": data.get("expiry"),
            "daysRemaining": data.get("daysRemaining"),
            "maxAccounts": data.get("maxAccounts", 2),
            "multiAccountExec": bool(data.get("multiAccountExec")),
            "lastVerifiedAt": datetime.now().timestamp(),
        })
    return data


def get_server_plan():
    """Quyền tính năng hiện tại theo server (online ưu tiên, fallback cache).

    Trả dict giống get_license_plan offline: activated, planKey, planLabel,
    isPermanent, daysRemaining, maxAccounts, multiAccountExec.
    """
    cache = _read_cache()
    key = cache.get("key")
    # Không có key đã lưu -> chưa kích hoạt.
    if not key:
        return _plan_result(False, cache, reason="chưa kích hoạt")

    # Vừa verify online gần đây -> dùng cache, không gọi server (giảm tải + nhanh).
    last = float(cache.get("lastVerifiedAt") or 0)
    if last and (datetime.now().timestamp() - last) < REVERIFY_SECONDS and _cache_not_expired(cache):
        return _plan_result(True, cache, offline=True)

    # Thử verify online để phản ánh hủy kích hoạt / hết hạn kịp thời.
    data = verify_with_server(key, timeout=8)
    if data.get("valid"):
        cache = _read_cache()  # đã được verify_with_server cập nhật
        return _plan_result(True, cache)
    # CHỈ khóa khi server nói license SAI RÕ RÀNG. Lỗi mạng HOẶC rate-limit/tạm khóa
    # (429, "quá nhiều yêu cầu", "tạm khóa") là lỗi TẠM -> dùng cache, KHÔNG khóa
    # (tránh cảnh nhiều máy chung IP bị đòi nhập lại key oan).
    err = str(data.get("error") or "")
    low = err.lower()
    soft_error = (
        bool(data.get("softError"))
        or "kết nối" in low or "connect" in low
        or "quá nhiều" in low or "qua nhieu" in low
        or "tạm khóa" in low or "tam khoa" in low
        or low.startswith("http ")  # phản hồi không phải JSON từ tunnel/proxy
    )
    if not soft_error:
        return _plan_result(False, cache, reason=err or "license không hợp lệ")

    # Lỗi tạm (mạng / rate-limit / tunnel chết): dùng cache.
    # - Gói VĨNH VIỄN: tin cache (không có hạn để hết) -> không đòi nhập lại key.
    # - Gói có hạn: cho dùng trong grace period kể từ lần verify online cuối.
    if _cache_not_expired(cache):
        if cache.get("isPermanent"):
            return _plan_result(True, cache, offline=True)
        last = float(cache.get("lastVerifiedAt") or 0)
        if last and (datetime.now().timestamp() - last) <= GRACE_SECONDS:
            return _plan_result(True, cache, offline=True)
    return _plan_result(False, cache, reason="mất kết nối máy chủ quá lâu")


def _cache_not_expired(cache):
    if cache.get("isPermanent"):
        return True
    expiry = cache.get("expiry")
    if not expiry:
        return True
    try:
        return datetime.now() <= datetime.fromisoformat(expiry)
    except ValueError:
        return True


def _plan_result(activated, cache, reason="", offline=False):
    days = cache.get("daysRemaining", 0)
    return {
        "activated": bool(activated),
        "planKey": cache.get("plan") or "",
        "planLabel": cache.get("planLabel") or "",
        "isPermanent": bool(cache.get("isPermanent")),
        "daysRemaining": int(days or 0),
        # Chưa kích hoạt -> khóa an toàn (gói cơ bản).
        "maxAccounts": int(cache.get("maxAccounts", 2)) if activated else 2,
        "multiAccountExec": bool(cache.get("multiAccountExec")) if activated else False,
        "offline": bool(offline),
        "reason": reason,
    }


def clear_cache():
    try:
        os.remove(_cache_path())
    except Exception:
        pass
