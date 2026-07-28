"""
Quản lý tài khoản Zalo — mỗi tài khoản một Chrome user-data-dir riêng.
"""
import base64
import functools
import json
import os
import shutil
import socket
import socketserver
import subprocess
import sys
import threading
import time
import uuid
import winreg
from http.server import BaseHTTPRequestHandler


def find_chrome_executable():
    """Tìm Chrome từ các vị trí phổ biến trên Windows"""
    chrome_paths = [
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    ]
    
    # Kiểm tra các vị trí phổ biến
    for path in chrome_paths:
        if os.path.isfile(path):
            return path
    
    # Tìm từ Registry (Windows)
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Google\Chrome\Binaries") as key:
            path, _ = winreg.QueryValueEx(key, "InstallDir")
            chrome_exe = os.path.join(path, "chrome.exe")
            if os.path.isfile(chrome_exe):
                return chrome_exe
    except Exception:
        pass
    
    # Thử HKEY_CURRENT_USER
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Google\Chrome\Binaries") as key:
            path, _ = winreg.QueryValueEx(key, "InstallDir")
            chrome_exe = os.path.join(path, "chrome.exe")
            if os.path.isfile(chrome_exe):
                return chrome_exe
    except Exception:
        pass
    
    raise FileNotFoundError("Không tìm thấy Chrome trên máy. Vui lòng cài đặt Google Chrome.")


CHROME_EXE = find_chrome_executable()
ZALO_URL = "https://chat.zalo.me/index.html"
DEBUG_PORT_START = 9333

_running_chrome = {}
_proxy_relay_servers = {}  # account_id -> ThreadingTCPServer (local proxy relay)
_used_ports = set()

# Bảo vệ chu trình đọc-sửa-ghi data/accounts.json khỏi race condition khi nhiều
# thread ghi đồng thời (network monitor của từng tài khoản đang chạy + các request
# Flask threaded=True) — nếu không có khóa này, "Chạy tất cả" nhiều tài khoản cùng
# lúc có thể làm mất tài khoản khỏi accounts.json do lost-update (thread A đọc danh
# sách cũ, ghi đè lại đúng lúc thread B vừa thêm/sửa xong).
_accounts_file_lock = threading.RLock()


def _locked(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with _accounts_file_lock:
            return fn(*args, **kwargs)
    return wrapper


def _base_dir():
    if getattr(sys, "frozen", False):
        # PyInstaller --onedir: exe ở cùng folder với data/ và profiles/
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


BASE_DIR = _base_dir()
DATA_DIR = os.path.join(BASE_DIR, "data")
PROFILES_DIR = os.path.join(BASE_DIR, "profiles")
ACCOUNTS_FILE = os.path.join(DATA_DIR, "accounts.json")


def _ensure_dirs():
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(PROFILES_DIR, exist_ok=True)


def _profile_path(account_id: str) -> str:
    return os.path.join(PROFILES_DIR, account_id)


def normalize_account(acc: dict, default_name: str = None) -> dict:
    """Chuẩn hóa field tài khoản (tương thích account cũ).

    Các trường phiên đang dùng được giữ nguyên. Việc mở lại Chrome chỉ yêu cầu
    monitor bắt phiên mới, tuyệt đối không xóa cookie/khóa phiên cũ trước khi
    thu được bộ thay thế hợp lệ.
    """
    created = acc.get("createdAt") or int(time.time() * 1000)
    name = (acc.get("name") or default_name or "Tài khoản").strip()

    normalized = {
        "accountId": acc.get("accountId", ""),
        "name": name,
        "avatarUrl": acc.get("avatarUrl", "") or "",
        "profilePath": acc.get("profilePath", "") or "",
        "remoteDebugPort": acc.get("remoteDebugPort"),
        "zpwEnk": acc.get("zpwEnk", "") or "",
        "imei": acc.get("imei", "") or "",
        "cookies": acc.get("cookies", "") or "",
        "proxy": acc.get("proxy", "") or "",
        "loginCaptured": bool(acc.get("loginCaptured", False)),
        "userinfoCaptured": bool(acc.get("userinfoCaptured", False)),
        "createdAt": created,
        "updatedAt": acc.get("updatedAt") or created,
        "personalGroups": acc.get("personalGroups") or [],  # Danh sách nhóm cá nhân
        "groupsSyncedAt": int(acc.get("groupsSyncedAt") or 0),
        "groupsSyncStatus": acc.get("groupsSyncStatus", "") or "",
        "sessionRefreshStartedAt": int(acc.get("sessionRefreshStartedAt") or 0),
        "sessionCapturedAt": int(acc.get("sessionCapturedAt") or 0),
    }

    if acc.get("uid"):
        normalized["uid"] = str(acc["uid"])
    if acc.get("phoneNumber"):
        normalized["phoneNumber"] = str(acc["phoneNumber"])

    return normalized


@_locked
def load_accounts():
    """Đọc danh sách tài khoản từ data/accounts.json.

    Nếu file chính bị ghi dở hoặc hỏng JSON, thử phục hồi từ bản sao an toàn
    thay vì trả danh sách rỗng rồi vô tình ghi đè dữ liệu người dùng.
    """
    _ensure_dirs()
    backup_file = ACCOUNTS_FILE + ".bak"
    if not os.path.isfile(ACCOUNTS_FILE):
        if os.path.isfile(backup_file):
            try:
                shutil.copy2(backup_file, ACCOUNTS_FILE)
            except OSError:
                pass
        if not os.path.isfile(ACCOUNTS_FILE):
            save_accounts([])
            return []

    try:
        with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        raw = data.get("accounts", [])
        if not isinstance(raw, list):
            return []

        accounts = []
        global _used_ports
        _used_ports = set()
        changed = False
        for acc in raw:
            if not acc.get("accountId"):
                continue
            norm = normalize_account(acc)
            port = norm.get("remoteDebugPort")
            if port:
                profile_path = norm.get("profilePath") or _profile_path(norm["accountId"])
                if _is_profile_in_use(profile_path, norm["accountId"]):
                    _used_ports.add(int(port))
                else:
                    # Chrome đã bị đóng ngoài ý muốn (tắt cửa sổ trực tiếp) mà app
                    # không kịp ghi nhận qua close_account(). Tự sửa lại trạng thái.
                    norm["remoteDebugPort"] = None
                    changed = True
            accounts.append(norm)
        if changed:
            save_accounts(accounts)
        return accounts
    except (json.JSONDecodeError, OSError) as error:
        backup_file = ACCOUNTS_FILE + ".bak"
        if os.path.isfile(backup_file):
            try:
                with open(backup_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                raw = data.get("accounts", [])
                if isinstance(raw, list):
                    restored = [normalize_account(a) for a in raw if a.get("accountId")]
                    print(f"[account_manager] Phục hồi accounts.json từ backup sau lỗi: {error}")
                    return restored
            except (json.JSONDecodeError, OSError):
                pass
        print(f"[account_manager] Không đọc được accounts.json: {error}")
        return []


@_locked
def save_accounts(accounts):
    """Lưu accounts.json theo kiểu atomic và tạo bản sao dự phòng.

    File tạm được ghi xong rồi mới thay thế file chính, tránh mất cookies khi
    ứng dụng bị tắt đúng lúc đang ghi dữ liệu.
    """
    _ensure_dirs()
    normalized = [normalize_account(a) for a in accounts]
    payload = {"accounts": normalized}
    temp_file = ACCOUNTS_FILE + ".tmp"
    backup_file = ACCOUNTS_FILE + ".bak"

    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass

    if os.path.isfile(ACCOUNTS_FILE):
        try:
            with open(ACCOUNTS_FILE, "r", encoding="utf-8") as current:
                existing = json.load(current)
            if isinstance(existing.get("accounts", []), list):
                shutil.copy2(ACCOUNTS_FILE, backup_file)
        except (json.JSONDecodeError, OSError):
            pass

    os.replace(temp_file, ACCOUNTS_FILE)


def _find_account(accounts, account_id):
    for acc in accounts:
        if acc.get("accountId") == account_id:
            return acc
    return None


def get_account(account_id: str):
    return _find_account(load_accounts(), account_id)


@_locked
def update_account(account_id: str, **fields):
    """Cập nhật một phần thông tin tài khoản."""
    accounts = load_accounts()
    for i, acc in enumerate(accounts):
        if acc.get("accountId") != account_id:
            continue
        acc.update(fields)
        acc["updatedAt"] = int(time.time() * 1000)
        accounts[i] = normalize_account(acc)
        save_accounts(accounts)
        return accounts[i]
    return None


def _cookie_has(cookie_str: str, name: str) -> bool:
    target = str(name or "").strip().lower()
    if not target:
        return False
    for item in str(cookie_str or "").split(";"):
        item = item.strip()
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        if key.strip().lower() == target and value.strip():
            return True
    return False


def account_capture_complete(account_id: str) -> bool:
    """
    Monitor dừng khi TẤT CẢ các điều kiện được thỏa:
    - Đã capture login (loginCaptured = True, zpwEnk có giá trị)
    - Đã capture userinfo (userinfoCaptured = True, name/avatar/etc)
    - Đã lấy được danh sách nhóm cá nhân (personalGroups không rỗng)
    - Đã có cookies đầy đủ (độ dài > 300 ký tự)
    
    Nếu chưa đủ, monitor tiếp tục chạy để lấy thêm dữ liệu.
    """
    acc = get_account(account_id)
    if not acc:
        return False
    
    # Kiểm tra login capture (zpwEnk là dấu hiệu)
    zpw_enk = (acc.get("zpwEnk") or "").strip()
    if not zpw_enk:
        print(f"[account_capture_complete] Missing zpwEnk for {account_id[:8]}...")
        return False
    
    login_captured = acc.get("loginCaptured", False)
    if not login_captured:
        print(f"[account_capture_complete] loginCaptured=False for {account_id[:8]}...")
        return False
    
    # Kiểm tra userinfo capture
    userinfo_captured = acc.get("userinfoCaptured", False)
    if not userinfo_captured:
        print(f"[account_capture_complete] userinfoCaptured=False for {account_id[:8]}...")
        return False
    
    # Khi đang yêu cầu Làm mới nhóm, không được coi dữ liệu cũ là đủ.
    # Monitor phải tiếp tục chạy cho đến khi bắt được getlg/v4 mới và save_account_groups cập nhật lại trạng thái.
    groups_sync_status = str(acc.get("groupsSyncStatus") or "").strip().lower()
    if groups_sync_status in {"opening", "refreshing", "syncing"}:
        print(f"[account_capture_complete] groupsSyncStatus={groups_sync_status} for {account_id[:8]}..., continue capture")
        return False

    # Kiểm tra personalGroups
    personal_groups = acc.get("personalGroups", [])
    has_groups = False
    
    if isinstance(personal_groups, list):
        has_groups = len(personal_groups) > 0
    elif isinstance(personal_groups, dict):
        has_groups = len(personal_groups) > 0
    
    if not has_groups:
        print(f"[account_capture_complete] No personalGroups for {account_id[:8]}...")
        return False
    
    # Cookie dài không đồng nghĩa với phiên hợp lệ. getmg bắt buộc phải có
    # zpw_sek; trước đây cookie 282 ký tự vẫn được lưu và ghi đè cookie đầy đủ.
    cookies = acc.get("cookies", "")
    if not _cookie_has(cookies, "zpw_sek"):
        print(
            f"[account_capture_complete] Missing zpw_sek "
            f"({len(cookies)} chars) for {account_id[:8]}..."
        )
        return False
    
    # TẤT CẢ điều kiện thỏa - dữ liệu đủ
    return True

def account_capture_basic(account_id: str) -> bool:
    """Check if basic account info is captured (without group list)."""
    acc = get_account(account_id)
    if not acc:
        return False
    return bool(
        acc.get("zpwEnk")
        and _cookie_has(acc.get("cookies") or "", "zpw_sek")
        and acc.get("name")
        and acc.get("avatarUrl")
        and acc.get("loginCaptured")
        and acc.get("userinfoCaptured")
    )


def get_system_imei() -> str:
    """
    Lấy IMEI của hệ thống (Windows: serial number ổ cứng).
    """
    try:
        import wmi
        c = wmi.WMI()
        disks = c.Win32_PhysicalMedia()
        if disks:
            return str(disks[0].SerialNumber).strip()
    except Exception as e:
        pass
    
    return "N/A"


def auto_capture_account_imei(account_id: str):
    """
    Không tự tạo IMEI và không lấy serial máy.
    IMEI đúng phải được bắt từ request Zalo Web:
    /api/login/getServerInfo?imei=...
    """
    acc = get_account(account_id)
    current_imei = ""
    if acc:
        current_imei = str(acc.get("imei") or "").strip()

    if current_imei:
        print(f"[auto_capture_account_imei] IMEI đã có từ Zalo Web, giữ nguyên: {current_imei}")
    else:
        print(f"[auto_capture_account_imei] Chưa có IMEI. Chờ bắt từ getServerInfo?imei=...")

    return None


def reset_account_session(account_id: str, clear_groups: bool = True):
    """Yêu cầu monitor bắt lại phiên mà không xóa dữ liệu hiện có.

    Bản cũ đặt cookies/zpwEnk/personalGroups thành chuỗi rỗng ngay khi bấm Mở,
    khiến phiên đang dùng bị mất trước khi Chrome kịp phát sinh cookie mới.
    Từ đây chỉ hạ cờ ``loginCaptured`` và đánh dấu đang đồng bộ. Dữ liệu cũ
    tiếp tục được giữ làm phương án dự phòng cho đến khi phiên mới được bắt đủ.
    """
    fields = {
        "loginCaptured": False,
        "groupsSyncStatus": "opening" if not clear_groups else "refreshing",
        "sessionRefreshStartedAt": int(time.time() * 1000),
    }
    update_account(account_id, **fields)


def rename_account(account_id: str, name: str):
    name = (name or "").strip()
    if not name:
        raise ValueError("Tên không được để trống.")
    return update_account(account_id, name=name)


def _next_account_name(accounts):
    used = {a.get("name", "") for a in accounts}
    n = len(accounts) + 1
    while True:
        label = f"Tài khoản {n}"
        if label not in used:
            return label
        n += 1


def _allocate_debug_port(accounts) -> int:
    used = {int(a["remoteDebugPort"]) for a in accounts if a.get("remoteDebugPort")}
    used |= _used_ports
    port = DEBUG_PORT_START
    while port in used:
        port += 1
    _used_ports.add(port)
    return port


def _is_profile_in_use(profile_path: str, account_id: str = None) -> bool:
    if account_id and account_id in _running_chrome:
        proc = _running_chrome[account_id]
        if proc is not None and proc.poll() is None:
            return True

    if not os.path.isdir(profile_path):
        return False

    for lock_name in ("SingletonLock", "lockfile", "SingletonSocket"):
        if os.path.exists(os.path.join(profile_path, lock_name)):
            return True
    return False

def _parse_proxy(proxy_string: str):
    """
    Parse proxy string thành dict.

    Hỗ trợ:
    - ip:port
    - ip:port:user:pass
    - http://ip:port
    - http://user:pass@ip:port
    - https://user:pass@ip:port
    """
    proxy_string = (proxy_string or "").strip()
    if not proxy_string:
        return None

    if proxy_string.startswith("http://"):
        proxy_string = proxy_string[len("http://"):]
    elif proxy_string.startswith("https://"):
        proxy_string = proxy_string[len("https://"):]

    username = ""
    password = ""
    host_port = proxy_string

    # user:pass@host:port
    if "@" in proxy_string:
        auth, host_port = proxy_string.split("@", 1)
        if ":" in auth:
            username, password = auth.split(":", 1)
        else:
            username = auth

    parts = host_port.split(":")

    # host:port
    if len(parts) == 2:
        host, port = parts

    # host:port:user:pass
    elif len(parts) == 4:
        host, port, username, password = parts

    else:
        raise ValueError("Proxy khong dung dinh dang. Dung ip:port hoac ip:port:user:pass")

    host = host.strip()
    port = int(str(port).strip())
    username = username.strip()
    password = password.strip()

    if not host:
        raise ValueError("Proxy host rong")

    if not port:
        raise ValueError("Proxy port rong")

    return {
        "host": host,
        "port": port,
        "username": username,
        "password": password,
    }


class _ProxyRelayHandler(BaseHTTPRequestHandler):
    """
    Proxy HTTP nội bộ (127.0.0.1) không yêu cầu xác thực — Chrome trỏ vào
    đây thay vì trỏ thẳng vào proxy thật. Mọi kết nối được relay tiếp tới
    proxy thật kèm sẵn header Proxy-Authorization, nên Chrome không bao giờ
    thấy proxy đòi đăng nhập nữa.

    Lý do dùng cách này thay vì Chrome extension (đã thử trước đó):
    Chrome bản mới hạn chế/khóa --load-extension và Manifest V2 trên kênh
    Stable theo từng đợt cập nhật khác nhau tùy máy, nên cách "tự trả
    username/password qua extension" không ổn định. Local relay này không
    phụ thuộc bất kỳ cơ chế extension nào của Chrome nên tránh được hẳn vấn đề.
    """

    protocol_version = "HTTP/1.1"
    upstream_host = None
    upstream_port = None
    upstream_auth_header = None  # "Basic xxxx" hoặc None nếu proxy không cần auth

    def log_message(self, fmt, *args):
        pass  # im lặng, tránh log rác request ra console

    def _connect_upstream(self):
        return socket.create_connection((self.upstream_host, self.upstream_port), timeout=15)

    @staticmethod
    def _pipe(src, dst):
        try:
            while True:
                data = src.recv(65536)
                if not data:
                    break
                dst.sendall(data)
        except Exception:
            pass
        finally:
            for s in (src, dst):
                try:
                    s.shutdown(socket.SHUT_RDWR)
                except Exception:
                    pass

    def do_CONNECT(self):
        try:
            upstream = self._connect_upstream()
        except Exception:
            self.send_error(502, "Khong ket noi duoc proxy that")
            return

        try:
            req = f"CONNECT {self.path} HTTP/1.1\r\nHost: {self.path}\r\n"
            if self.upstream_auth_header:
                req += f"Proxy-Authorization: {self.upstream_auth_header}\r\n"
            req += "Proxy-Connection: Keep-Alive\r\n\r\n"
            upstream.sendall(req.encode("latin-1"))

            resp = b""
            upstream.settimeout(15)
            while b"\r\n\r\n" not in resp and len(resp) < 65536:
                chunk = upstream.recv(4096)
                if not chunk:
                    break
                resp += chunk

            if not (resp.startswith(b"HTTP/1.1 200") or resp.startswith(b"HTTP/1.0 200")):
                self.send_error(502, "Proxy that tu choi ket noi (kiem tra lai user/pass)")
                upstream.close()
                return

            self.send_response(200, "Connection Established")
            self.end_headers()
        except Exception:
            try:
                upstream.close()
            except Exception:
                pass
            return

        self.close_connection = True  # tunnel dùng riêng socket này, không đọc thêm request nữa
        client = self.connection
        client.settimeout(None)
        upstream.settimeout(None)
        t = threading.Thread(target=self._pipe, args=(upstream, client), daemon=True)
        t.start()
        self._pipe(client, upstream)
        t.join(timeout=5)

    def _forward_plain(self):
        self.close_connection = True
        try:
            upstream = self._connect_upstream()
        except Exception:
            self.send_error(502, "Khong ket noi duoc proxy that")
            return

        try:
            header_lines = ""
            for k in self.headers.keys():
                if k.lower() in ("proxy-authorization", "proxy-connection"):
                    continue
                header_lines += f"{k}: {self.headers[k]}\r\n"
            if self.upstream_auth_header:
                header_lines += f"Proxy-Authorization: {self.upstream_auth_header}\r\n"
            raw = f"{self.command} {self.path} HTTP/1.1\r\n{header_lines}\r\n".encode("latin-1")
            upstream.sendall(raw)

            length = int(self.headers.get("Content-Length", 0) or 0)
            if length:
                upstream.sendall(self.rfile.read(length))

            self._pipe(upstream, self.connection)
        except Exception:
            try:
                upstream.close()
            except Exception:
                pass

    do_GET = do_HEAD = do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _forward_plain


def _start_proxy_relay(account_id: str, proxy_info: dict) -> int:
    """Khởi động local proxy relay cho 1 tài khoản, trả về port đã lắng nghe."""
    _stop_proxy_relay(account_id)

    username = proxy_info.get("username", "") or ""
    password = proxy_info.get("password", "") or ""
    auth_header = None
    if username or password:
        token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
        auth_header = f"Basic {token}"

    handler_cls = type("_ProxyRelayHandlerBound", (_ProxyRelayHandler,), {
        "upstream_host": proxy_info["host"],
        "upstream_port": int(proxy_info["port"]),
        "upstream_auth_header": auth_header,
    })

    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler_cls)
    server.daemon_threads = True
    port = server.server_address[1]

    threading.Thread(target=server.serve_forever, daemon=True).start()
    _proxy_relay_servers[account_id] = server

    print(f"[account_manager] Proxy relay 127.0.0.1:{port} -> {proxy_info['host']}:{proxy_info['port']}")
    return port


def _stop_proxy_relay(account_id: str):
    server = _proxy_relay_servers.pop(account_id, None)
    if server:
        try:
            server.shutdown()
            server.server_close()
        except Exception:
            pass


def _launch_chrome(profile_path: str, account_id: str, debug_port: int, proxy: str = ""):
    if not os.path.isfile(CHROME_EXE):
        raise FileNotFoundError(f"Không tìm thấy Chrome tại: {CHROME_EXE}")

    profile_path = os.path.abspath(profile_path)
    os.makedirs(profile_path, exist_ok=True)

    args = [
        CHROME_EXE,
        f"--user-data-dir={profile_path}",
        f"--remote-debugging-port={int(debug_port)}",
        "--remote-allow-origins=*",
        "--no-first-run",
        "--no-default-browser-check",
    ]

    proxy = (proxy or "").strip()

    if proxy:
        try:
            print(f"[account_manager] Parsing proxy for account {account_id}: {proxy}")

            proxy_info = _parse_proxy(proxy)

            print(
                f"[account_manager] Parsed proxy: "
                f"host={proxy_info['host']}, "
                f"port={proxy_info['port']}, "
                f"has_auth={bool(proxy_info.get('username'))}"
            )

            if proxy_info.get("username") or proxy_info.get("password"):
                # Proxy cần đăng nhập: không trỏ Chrome thẳng vào proxy thật
                # nữa (Chrome sẽ luôn tự hỏi lại username/password vì
                # --proxy-server không mang được thông tin đăng nhập, và cách
                # dùng extension để tự trả auth không ổn định giữa các bản
                # Chrome). Thay vào đó chạy 1 proxy relay nội bộ không cần
                # auth, Chrome trỏ vào đó, relay tự thêm Proxy-Authorization
                # khi nối tiếp sang proxy thật.
                local_port = _start_proxy_relay(account_id, proxy_info)
                proxy_server = f"http://127.0.0.1:{local_port}"
                print(f"[account_manager] Dung proxy relay noi bo cho proxy co auth: {proxy_server}")
            else:
                _stop_proxy_relay(account_id)
                proxy_server = f"http://{proxy_info['host']}:{int(proxy_info['port'])}"

            args.extend([
                f"--proxy-server={proxy_server}",
                "--proxy-bypass-list=<-loopback>",
                "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
            ])

            print(f"[account_manager] Proxy server (Chrome se dung): {proxy_server}")

        except Exception as e:
            print(f"[account_manager] ERROR setting up proxy: {e}")
            import traceback
            traceback.print_exc()

    # Mở trang trắng trước để monitor attach Network.enable trước,
    # sau đó monitor sẽ tự điều hướng sang Zalo.
    args.append("about:blank")

    creationflags = 0
    if sys.platform == "win32":
        creationflags = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )

    print("[account_manager] Launching Chrome with args:", " ".join(args))

    proc = subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
        close_fds=True,
    )

    _running_chrome[account_id] = proc
    print(f"[account_manager] Chrome process started (PID: {proc.pid}) for account {account_id}")
    return proc

def _start_network_monitor(account_id: str, debug_port: int):
    def _delayed():
        # Chỉ chờ rất ngắn để Chrome mở cổng remote-debugging
        time.sleep(0.2)
        try:
            try:
                from .account_network_monitor import start_account_network_monitor
            except ImportError:
                from account_network_monitor import start_account_network_monitor
            start_account_network_monitor(account_id, debug_port)
        except Exception as e:
            print(f"[account_manager] Không start monitor: {e}")

    threading.Thread(target=_delayed, daemon=True).start()


def _start_fingerprint_guard(account_id: str, debug_port: int, proxy: str = ""):
    """Ẩn rò rỉ IP thật qua WebRTC + đặt timezone khớp proxy, sống suốt phiên Chrome."""
    def _delayed():
        time.sleep(0.2)
        try:
            try:
                from .account_network_monitor import start_fingerprint_guard
            except ImportError:
                from account_network_monitor import start_fingerprint_guard
            start_fingerprint_guard(account_id, debug_port, proxy)
        except Exception as e:
            print(f"[account_manager] Không start fingerprint guard: {e}")

    threading.Thread(target=_delayed, daemon=True).start()


def _stop_fingerprint_guard(account_id: str):
    try:
        try:
            from .account_network_monitor import stop_fingerprint_guard
        except ImportError:
            from account_network_monitor import stop_fingerprint_guard
        stop_fingerprint_guard(account_id)
    except Exception:
        pass


@_locked
def _open_chrome_for_account(account: dict, resync: bool = True, clear_groups: bool = False):
    account_id = account["accountId"]
    if resync:
        reset_account_session(account_id, clear_groups=clear_groups)
        account = get_account(account_id) or account

    accounts = load_accounts()
    profile_path = account.get("profilePath") or _profile_path(account_id)

    port = account.get("remoteDebugPort")
    if not port:
        port = _allocate_debug_port(accounts)
        account["remoteDebugPort"] = port
        for i, a in enumerate(accounts):
            if a["accountId"] == account_id:
                accounts[i] = normalize_account({**a, **account})
                break
        save_accounts(accounts)

    _launch_chrome(profile_path, account_id, int(port), account.get("proxy", ""))
    _start_network_monitor(account_id, int(port))
    _start_fingerprint_guard(account_id, int(port), account.get("proxy", ""))
    return get_account(account_id) or normalize_account(account)


@_locked
def create_account(name: str = None, proxy: str = "", auto_launch: bool = True):
    """
    Tạo tài khoản mới: accountId, profile, lưu JSON.

    auto_launch=False: chỉ tạo bản ghi tài khoản (áp dụng tên/proxy đã setup),
    KHÔNG mở Chrome. Người dùng tự bấm "Khởi chạy" khi sẵn sàng — lúc đó Chrome
    sẽ mở đúng với proxy đã lưu ngay từ đầu.
    """
    _ensure_dirs()
    accounts = load_accounts()

    account_id = uuid.uuid4().hex
    profile_path = _profile_path(account_id)
    os.makedirs(profile_path, exist_ok=True)

    now = int(time.time() * 1000)

    # Generate IMEI: UUID-based format
    imei = f"{uuid.uuid4().hex[:8]}-{uuid.uuid4().hex[:4]}-{uuid.uuid4().hex[:4]}-{uuid.uuid4().hex[:4]}-{uuid.uuid4().hex[:12]}"

    account = normalize_account(
        {
            "accountId": account_id,
            "name": (name or "").strip() or _next_account_name(accounts),
            "profilePath": os.path.abspath(profile_path),
            "proxy": (proxy or "").strip(),
            "imei": imei,
            "createdAt": now,
            "updatedAt": now,
        }
    )

    accounts.append(account)
    save_accounts(accounts)

    if auto_launch:
        port = _allocate_debug_port(accounts)
        update_account(account_id, remoteDebugPort=port)
        _launch_chrome(profile_path, account_id, port, account.get("proxy", ""))
        _start_network_monitor(account_id, port)
        _start_fingerprint_guard(account_id, port, account.get("proxy", ""))

    return get_account(account_id) or account


def open_account(account_id: str, clear_groups: bool = False):
    """Mở Chrome với profile của tài khoản + bật monitor."""
    accounts = load_accounts()
    account = _find_account(accounts, account_id)
    if not account:
        raise ValueError("Không tìm thấy tài khoản.")

    profile_path = account.get("profilePath") or _profile_path(account_id)
    if not os.path.isdir(profile_path):
        os.makedirs(profile_path, exist_ok=True)
        update_account(account_id, profilePath=os.path.abspath(profile_path))
        account = get_account(account_id)

    return _open_chrome_for_account(account, clear_groups=clear_groups)


@_locked
def close_account(account_id: str):
    """Đóng Chrome profile của tài khoản và dừng monitor."""
    try:
        try:
            from .account_network_monitor import stop_account_network_monitor
        except ImportError:
            from account_network_monitor import stop_account_network_monitor
        stop_account_network_monitor(account_id)
    except Exception:
        pass

    # Kill Chrome process if running
    if account_id in _running_chrome:
        try:
            proc = _running_chrome[account_id]
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        except Exception as e:
            print(f"[account_manager] Lỗi kill Chrome: {e}")
        finally:
            del _running_chrome[account_id]

    _stop_proxy_relay(account_id)
    _stop_fingerprint_guard(account_id)

    # Clear remoteDebugPort
    accounts = load_accounts()
    account = _find_account(accounts, account_id)
    if account:
        port = account.get("remoteDebugPort")
        if port:
            _used_ports.discard(int(port))
            account.pop("remoteDebugPort", None)
        save_accounts(accounts)


@_locked
def delete_account(account_id: str):
    """Xóa tài khoản, dừng monitor, xóa profile."""
    try:
        try:
            from .account_network_monitor import stop_account_network_monitor
        except ImportError:
            from account_network_monitor import stop_account_network_monitor
        stop_account_network_monitor(account_id)
    except Exception:
        pass

    _stop_proxy_relay(account_id)
    _stop_fingerprint_guard(account_id)

    accounts = load_accounts()
    account = _find_account(accounts, account_id)
    if not account:
        raise ValueError("Không tìm thấy tài khoản.")

    profile_path = account.get("profilePath") or _profile_path(account_id)

    if _is_profile_in_use(profile_path, account_id):
        raise RuntimeError(
            "Profile đang được Chrome sử dụng. "
            "Vui lòng đóng cửa sổ Chrome của tài khoản này rồi thử lại."
        )

    port = account.get("remoteDebugPort")
    if port:
        _used_ports.discard(int(port))

    accounts = [a for a in accounts if a.get("accountId") != account_id]
    save_accounts(accounts)

    if account_id in _running_chrome:
        del _running_chrome[account_id]

    if os.path.isdir(profile_path):
        try:
            shutil.rmtree(profile_path)
        except PermissionError as e:
            raise RuntimeError(
                "Không thể xóa thư mục profile (có thể Chrome vẫn đang mở). "
                "Đóng Chrome và thử lại."
            ) from e
        except OSError as e:
            raise RuntimeError(f"Không thể xóa thư mục profile: {e}") from e

    return True
