"""Máy chủ cấp phép (license) cho Nexus — độc lập với app client.

Luồng xác thực mới:
1. Client gửi thông tin máy (MAC, tên máy) lên server: POST /api/register.
2. Server tạo/ghi nhận máy, sinh KEY ngẫu nhiên gắn với máy + gói + hạn dùng,
   gửi KEY qua email admin đã cấu hình.
3. Người dùng nhập KEY vào client; client xác thực với server: POST /api/verify
   (server tra DB, kiểm trạng thái active + hạn dùng). Server có thể HỦY kích
   hoạt bất cứ lúc nào -> lần verify sau client bị khoá.

Server tự sinh key (không nhúng secret trong client) nên người dùng không thể
tự tạo license full — đây là điểm mạnh so với cấp phép offline trước đây.

Dashboard quản trị tại "/" : xem/lọc máy, kích hoạt / hủy kích hoạt, tạo lại key.

Chạy:  python server/license_server.py  (mặc định cổng 5555)
Cấu hình email + admin token qua server/.env (xem .env.example).
"""
import hashlib
import hmac
import os
import sqlite3
import secrets
import smtplib
import string
import threading
from contextlib import closing
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from email.header import Header

from flask import Flask, request, jsonify, render_template, redirect, url_for, session

import shutil

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# DB có thể đặt trên volume bền của host (LICENSE_DB_PATH). Mặc định: data/licenses.db.
DB_PATH = (os.getenv("LICENSE_DB_PATH", "") or "").strip() or os.path.join(BASE_DIR, "data", "licenses.db")
os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)

# Seed DB lần đầu: nếu volume chưa có DB nhưng có bản snapshot kèm theo (server/seed/
# licenses.db) thì nạp vào để "đưa DB hiện tại lên" mà không mất dữ liệu key.
_SEED_DB = os.path.join(BASE_DIR, "seed", "licenses.db")
if not os.path.exists(DB_PATH) and os.path.exists(_SEED_DB):
    try:
        shutil.copyfile(_SEED_DB, DB_PATH)
        print(f"[license_server] Đã seed DB từ snapshot -> {DB_PATH}")
    except Exception as exc:
        print(f"[license_server] Seed DB thất bại: {exc}")


# ─── Cấu hình (.env đơn giản) ────────────────────────────────────────────────
def _load_env():
    env_path = os.path.join(BASE_DIR, ".env")
    if not os.path.exists(env_path):
        return
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except Exception as exc:
        print(f"[license_server] Lỗi đọc .env: {exc}")


_load_env()

SENDMAIL_USER = (os.getenv("SENDMAIL_USER", "") or "").strip()
SENDMAIL_PASS = "".join((os.getenv("SENDMAIL_PASS", "") or "").split())
SMTP_FROM_NAME = (os.getenv("SMTP_FROM_NAME", "Nexus License") or "").strip()
# Nơi nhận key: mail admin đã thiết lập.
ADMIN_EMAILS = [
    e.strip() for e in (os.getenv("ADMIN_EMAILS", os.getenv("RECEIVER_EMAIL_1", "")) or "").split(",")
    if e.strip()
]
# Token API admin (hệ thống tự load từ .env — dùng cho script/tự động, KHÔNG để đăng nhập).
ADMIN_TOKEN = (os.getenv("ADMIN_TOKEN", "") or "").strip()


def _load_admin_credentials():
    """Đăng nhập dashboard bằng EMAIL + MẬT KHẨU. Chỉ 2 admin trong ADMIN_EMAILS.

    Cấu hình mật khẩu qua .env:
    - ADMIN_CREDENTIALS = email1:matkhau1,email2:matkhau2   (ưu tiên)
    - hoặc ADMIN_PASSWORDS = matkhau1,matkhau2  (khớp theo thứ tự ADMIN_EMAILS)
    Trả dict {email_lower: password_plaintext}. UI sẽ gửi SHA-256(password),
    server so với SHA-256(password trong env).
    """
    creds = {}
    raw = (os.getenv("ADMIN_CREDENTIALS", "") or "").strip()
    if raw:
        for pair in raw.split(","):
            pair = pair.strip()
            if ":" in pair:
                email, pwd = pair.split(":", 1)
                email, pwd = email.strip().lower(), pwd.strip()
                if email and pwd:
                    creds[email] = pwd
    else:
        pwds = [p.strip() for p in (os.getenv("ADMIN_PASSWORDS", "") or "").split(",")]
        for email, pwd in zip(ADMIN_EMAILS, pwds):
            if email and pwd:
                creds[email.strip().lower()] = pwd
    return creds


ADMIN_CREDENTIALS = _load_admin_credentials()

# Gói: key -> (số ngày, nhãn, is_permanent)
PLAN_OPTIONS = {
    "1m": (30, "1 Tháng", False),
    "3m": (90, "3 Tháng", False),
    "6m": (180, "6 Tháng", False),
    "lifetime": (0, "Vĩnh viễn", True),
    "enterprise": (0, "Doanh nghiệp (10 máy)", True),
}

# Số máy (MAC) tối đa mà 1 key kích hoạt được. Gói doanh nghiệp = 10, còn lại = 1.
PLAN_MAX_MACHINES = {"enterprise": 10}


def _plan_max_machines(plan_key):
    return int(PLAN_MAX_MACHINES.get(str(plan_key or "").strip().lower(), 1))

_db_lock = threading.Lock()


# ─── DB ──────────────────────────────────────────────────────────────────────
def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with closing(_conn()) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS machines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mac TEXT UNIQUE NOT NULL,
                machine_name TEXT DEFAULT '',
                ip TEXT DEFAULT '',
                plan_key TEXT DEFAULT '',
                plan_label TEXT DEFAULT '',
                license_key TEXT DEFAULT '',
                status TEXT DEFAULT 'pending',      -- pending | active | disabled | expired
                is_permanent INTEGER DEFAULT 0,
                created_at TEXT DEFAULT '',
                activated_at TEXT DEFAULT '',
                expiry TEXT DEFAULT '',
                last_seen TEXT DEFAULT '',
                email_sent_at TEXT DEFAULT '',
                note TEXT DEFAULT ''
            )
            """
        )
        # Cấu hình động (email nhận phụ...) lưu key-value.
        conn.execute(
            "CREATE TABLE IF NOT EXISTS settings (k TEXT PRIMARY KEY, v TEXT DEFAULT '')"
        )
        # Key DOANH NGHIỆP: admin tạo sẵn 1 key cho nhiều MAC (mặc định 10). Máy
        # khác nhập key này để kích hoạt (không cần đăng ký từng máy trước). Mỗi MAC
        # kích hoạt sẽ tạo 1 dòng trong bảng machines, kế thừa hạn dùng của key.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS business_keys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                license_key TEXT UNIQUE NOT NULL,
                plan_key TEXT DEFAULT 'enterprise',
                plan_label TEXT DEFAULT '',
                is_permanent INTEGER DEFAULT 0,
                expiry TEXT DEFAULT '',              -- hạn dùng CHUNG cho mọi máy của key
                max_machines INTEGER DEFAULT 10,
                email TEXT DEFAULT '',
                note TEXT DEFAULT '',
                status TEXT DEFAULT 'active',        -- active | disabled
                created_at TEXT DEFAULT '',
                key_issued_at TEXT DEFAULT ''
            )
            """
        )
        # Migration cột mới.
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(machines)").fetchall()}
        if "key_issued_at" not in cols:
            conn.execute("ALTER TABLE machines ADD COLUMN key_issued_at TEXT DEFAULT ''")
        if "max_machines" not in cols:
            # Số MAC tối đa 1 key kích hoạt được (gói doanh nghiệp = 10).
            conn.execute("ALTER TABLE machines ADD COLUMN max_machines INTEGER DEFAULT 1")
        conn.commit()


def get_setting(key, default=""):
    with closing(_conn()) as conn:
        row = conn.execute("SELECT v FROM settings WHERE k=?", (key,)).fetchone()
    return row["v"] if row else default


def set_setting(key, value):
    with _db_lock, closing(_conn()) as conn:
        conn.execute(
            "INSERT INTO settings (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
            (key, value),
        )
        conn.commit()


def get_extra_emails():
    """Danh sách gmail phụ nhận key (cấu hình qua dashboard)."""
    raw = get_setting("extra_emails", "")
    return [e.strip() for e in raw.split(",") if e.strip()]


def _mask_email(email):
    """Che email để không lộ đầy đủ trên dashboard: tu***fdnc@gmail.com."""
    email = str(email or "").strip()
    if "@" not in email:
        return email
    name, domain = email.split("@", 1)
    if len(name) <= 3:
        masked = name[0] + "***"
    else:
        masked = name[:2] + "***" + name[-2:]
    return f"{masked}@{domain}"


def get_all_recipients():
    """Gộp email env mặc định + email phụ trên dashboard, khử trùng."""
    seen, out = set(), []
    for e in list(ADMIN_EMAILS) + get_extra_emails():
        e = e.strip()
        low = e.lower()
        if e and low not in seen:
            seen.add(low)
            out.append(e)
    return out


def _now():
    return datetime.now().isoformat(timespec="seconds")


def _gen_key(n=12):
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(n))


def _plan_caps(plan_key, is_permanent):
    """Quyền theo gói: >=6 tháng hoặc vĩnh viễn = full (không giới hạn TK + multi)."""
    days = PLAN_OPTIONS.get(plan_key, (0, "", False))[0]
    full = bool(is_permanent) or days >= 180
    return {"maxAccounts": 0 if full else 2, "multiAccountExec": bool(full)}


def _row_to_dict(row):
    d = dict(row)
    caps = _plan_caps(d.get("plan_key"), d.get("is_permanent"))
    d["maxAccounts"] = caps["maxAccounts"]
    d["multiAccountExec"] = caps["multiAccountExec"]
    d["max_machines"] = int(d.get("max_machines") or _plan_max_machines(d.get("plan_key")))
    return d


# ─── Email ───────────────────────────────────────────────────────────────────
def _from_header():
    if not SMTP_FROM_NAME:
        return SENDMAIL_USER
    try:
        SMTP_FROM_NAME.encode("ascii")
        name = SMTP_FROM_NAME
    except UnicodeEncodeError:
        name = str(Header(SMTP_FROM_NAME, "utf-8"))
    return formataddr((name, SENDMAIL_USER))


def send_key_email(machine, key):
    recipients = get_all_recipients()
    if not SENDMAIL_USER or not SENDMAIL_PASS or not recipients:
        print("[license_server] Thiếu cấu hình email (SENDMAIL_USER/PASS hoặc email nhận) — bỏ qua gửi mail.")
        return False
    plan_label = machine.get("plan_label") or machine.get("plan_key") or "Không rõ"
    expiry = "Vĩnh viễn" if machine.get("is_permanent") else (machine.get("expiry") or "-")
    max_machines = int(machine.get("max_machines") or 1)
    device_line = (
        f"Kích hoạt được trên tối đa {max_machines} máy khác nhau"
        if max_machines > 1 else "Gắn với đúng thiết bị (máy) có MAC ở trên"
    )
    body = f"""
    <html>
    <head>
        <style>
            body {{ font-family: Arial, sans-serif; background: #f5f5f5; }}
            .container {{ max-width: 600px; margin: 20px auto; background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }}
            h1 {{ color: #333; border-bottom: 2px solid #4CAF50; padding-bottom: 10px; }}
            .info-block {{ background: #f9f9f9; padding: 15px; margin: 10px 0; border-radius: 5px; border-left: 3px solid #4CAF50; }}
            .code-block {{ background: #f0f0f0; padding: 18px; margin: 15px 0; border-radius: 5px; border: 2px solid #4CAF50; font-family: monospace; font-size: 28px; font-weight: bold; letter-spacing: 4px; text-align: center; }}
            .label {{ font-weight: bold; color: #333; }}
            .value {{ color: #555; font-family: monospace; }}
            .warning {{ background: #fff3cd; padding: 12px; border-radius: 5px; border-left: 3px solid #ffc107; margin: 15px 0; }}
            .timestamp {{ color: #999; font-size: 12px; margin-top: 20px; text-align: center; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🔐 Nexus - Mã kích hoạt</h1>

            <div class="info-block">
                <div><span class="label">🖥️ Tên máy:</span> <span class="value">{machine.get('machine_name') or '-'}</span></div>
                <div><span class="label">📱 MAC Address:</span> <span class="value">{machine.get('mac')}</span></div>
                <div><span class="label">🌐 IP:</span> <span class="value">{machine.get('ip') or '-'}</span></div>
            </div>

            <h2 style="color: #4CAF50;">Mã kích hoạt của thiết bị (12 ký tự):</h2>
            <div class="code-block">{key}</div>

            <div class="info-block">
                <div><span class="label">📦 Gói:</span> <span class="value">{plan_label}</span></div>
                <div><span class="label">⏰ Hết hạn:</span> <span class="value">{expiry}</span></div>
            </div>

            <div class="warning">
                <strong>⚠️ Lưu ý:</strong> Mã kích hoạt này:
                <ul>
                    <li>✅ {device_line}</li>
                    <li>✅ Có giới hạn thời gian (theo gói {plan_label})</li>
                    <li>✅ Tối đa 12 ký tự, dễ nhập</li>
                    <li>✅ Không thể chỉnh sửa</li>
                    <li>✅ Được mã hóa và bảo mật</li>
                </ul>
                Không chia sẻ mã này cho người khác.
            </div>

            <p class="timestamp">Tạo lúc: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
        </div>
    </body>
    </html>
    """
    try:
        for to in recipients:
            msg = MIMEMultipart("alternative")
            msg["From"] = _from_header()
            msg["To"] = to
            msg["Subject"] = f"🔑 Nexus Key {plan_label} - {machine.get('machine_name') or machine.get('mac')}"
            msg.attach(MIMEText(body, "html"))
            server = smtplib.SMTP("smtp.gmail.com", 587, timeout=20)
            server.starttls()
            server.login(SENDMAIL_USER, SENDMAIL_PASS)
            server.send_message(msg)
            server.quit()
        return True
    except Exception as exc:
        print(f"[license_server] Gửi email thất bại: {exc}")
        return False


# ─── Flask app ───────────────────────────────────────────────────────────────
app = Flask(__name__)
# Khóa ký session (đăng nhập dashboard). Ổn định qua restart nếu có ADMIN_TOKEN.
app.secret_key = os.getenv("SESSION_SECRET", "") or (ADMIN_TOKEN + "::nexus-session") or secrets.token_hex(32)
app.permanent_session_lifetime = timedelta(hours=24)   # phiên đăng nhập sống 24h
init_db()


# ─── Tự xóa key CHƯA kích hoạt quá 24h ───────────────────────────────────────
PENDING_TTL_HOURS = 24


def _purge_stale_pending():
    """Xóa các key còn 'pending' (chưa kích hoạt) được tạo quá PENDING_TTL_HOURS."""
    cutoff = (datetime.now() - timedelta(hours=PENDING_TTL_HOURS)).isoformat(timespec="seconds")
    with _db_lock, closing(_conn()) as conn:
        cur = conn.execute(
            "DELETE FROM machines WHERE status='pending' AND created_at != '' AND created_at < ?",
            (cutoff,),
        )
        conn.commit()
        return cur.rowcount


def _purge_loop():
    import time as _t
    while True:
        try:
            n = _purge_stale_pending()
            if n:
                print(f"[license_server] Đã xóa {n} key chưa kích hoạt quá {PENDING_TTL_HOURS}h", flush=True)
        except Exception as exc:
            print(f"[license_server] purge error: {exc}", flush=True)
        _t.sleep(1800)  # quét mỗi 30 phút


threading.Thread(target=_purge_loop, daemon=True, name="purge-pending").start()

# ─── Đăng nhập bằng EMAIL + MẬT KHẨU (UI hash SHA-256 trước khi gửi) ──────────
MAX_ACTIVE_SESSIONS = 2          # tối đa 2 thiết bị đăng nhập cùng lúc
SESSION_TTL = 24 * 3600          # phiên 24h
_otp_lock = threading.Lock()     # khóa dùng chung cho _active_sessions
_active_sessions = {}            # sid -> {"email", "created"}


def _admin_email_match(email):
    """True nếu email trùng một trong các email admin (ADMIN_EMAILS)."""
    email = str(email or "").strip().lower()
    return email in {e.strip().lower() for e in ADMIN_EMAILS}


def _sha256_hex(text):
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def _check_admin_password(email, password_hash):
    """So khớp SHA-256(password) do UI gửi với SHA-256(mật khẩu trong env).

    Chỉ chấp nhận email nằm trong ADMIN_EMAILS và có cấu hình mật khẩu.
    Dùng so sánh hằng thời gian để tránh dò theo thời gian phản hồi.
    """
    email = str(email or "").strip().lower()
    password_hash = str(password_hash or "").strip().lower()
    if not _admin_email_match(email) or not password_hash:
        return False
    expected_pwd = ADMIN_CREDENTIALS.get(email)
    if not expected_pwd:
        return False
    return hmac.compare_digest(_sha256_hex(expected_pwd), password_hash)


def _prune_sessions():
    now = _now_ts()
    for sid in [s for s, v in _active_sessions.items() if now - v["created"] > SESSION_TTL]:
        _active_sessions.pop(sid, None)


def _now_ts():
    import time as _t
    return _t.time()


def _client_ip():
    # Qua cloudflare tunnel: IP thật nằm ở CF-Connecting-IP.
    cf = request.headers.get("CF-Connecting-IP", "")
    if cf:
        return cf.strip()
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.remote_addr or ""


# ─── Chống brute-force / lạm dụng: rate limit theo IP (in-memory) ────────────
_rate_lock = threading.Lock()
_rate_hits = {}          # ip -> list[timestamp]
_blocked = {}            # ip -> unblock_ts (khoá tạm khi vượt ngưỡng verify sai)
_fail_counts = {}        # ip -> số lần verify SAI liên tiếp

import time as _time


def _rate_ok(ip, limit, window=60, bucket=""):
    """Cho tối đa `limit` request / `window` giây / IP cho từng loại (bucket)."""
    now = _time.time()
    k = f"{ip}:{bucket}"
    with _rate_lock:
        hits = [t for t in _rate_hits.get(k, []) if now - t < window]
        if len(hits) >= limit:
            _rate_hits[k] = hits
            return False
        hits.append(now)
        _rate_hits[k] = hits
        return True


def _is_blocked(ip):
    with _rate_lock:
        until = _blocked.get(ip, 0)
        if until and _time.time() < until:
            return int(until - _time.time())
        return 0


def _record_verify_fail(ip):
    """Sai key nhiều lần liên tiếp -> khoá IP tạm 5 phút (chống dò key)."""
    with _rate_lock:
        n = _fail_counts.get(ip, 0) + 1
        _fail_counts[ip] = n
        if n >= 10:
            _blocked[ip] = _time.time() + 300
            _fail_counts[ip] = 0


def _record_verify_ok(ip):
    with _rate_lock:
        _fail_counts.pop(ip, None)
        _blocked.pop(ip, None)


def _session_valid():
    """Phiên hợp lệ: đã đăng nhập, sid còn active và chưa quá 24h."""
    if session.get("admin") is not True:
        return False
    sid = session.get("sid")
    with _otp_lock:
        _prune_sessions()
        info = _active_sessions.get(sid)
    if not info:
        return False
    return (_now_ts() - info["created"]) <= SESSION_TTL


def _require_admin():
    """Đã đăng nhập (phiên mật khẩu) HOẶC token API đúng (cho script tự động)."""
    if _session_valid():
        return True
    if ADMIN_TOKEN:
        token = request.headers.get("X-Admin-Token") or request.args.get("token") or ""
        if request.is_json:
            token = token or (request.get_json(silent=True) or {}).get("token", "")
        if token and token == ADMIN_TOKEN:
            return True
    # Không cấu hình email admin lẫn token -> chạy nội bộ, không chặn.
    if not ADMIN_EMAILS and not ADMIN_TOKEN:
        return True
    return False


@app.route("/login")
def login():
    if _session_valid():
        return redirect(url_for("dashboard"))
    return render_template("login.html")


@app.route("/api/admin/login", methods=["POST"])
def api_admin_login():
    """Đăng nhập bằng email + SHA-256(mật khẩu) do UI gửi (không gửi mật khẩu thô).

    So khớp với SHA-256(mật khẩu trong env) của đúng email admin. Đúng -> tạo
    phiên 24h, tối đa 2 thiết bị (đá phiên cũ nhất). Chống dò: khóa IP khi sai nhiều.
    """
    ip = _client_ip()
    blocked_for = _is_blocked(ip)
    if blocked_for:
        return jsonify({"success": False, "error": f"Tạm khóa do thử sai quá nhiều. Thử lại sau {blocked_for}s."}), 429
    if not _rate_ok(ip, limit=10, window=60, bucket="login"):
        return jsonify({"success": False, "error": "Quá nhiều yêu cầu, thử lại sau ít phút."}), 429
    data = request.get_json(silent=True) or {}
    email = str(data.get("email") or "").strip().lower()
    password_hash = str(data.get("passwordHash") or "").strip()
    if not _check_admin_password(email, password_hash):
        _record_verify_fail(ip)
        return jsonify({"success": False, "error": "Email hoặc mật khẩu không đúng."}), 401
    _record_verify_ok(ip)
    with _otp_lock:
        _prune_sessions()
        sid = secrets.token_hex(16)
        _active_sessions[sid] = {"email": email, "created": _now_ts()}
        # Giới hạn số thiết bị: giữ MAX phiên mới nhất, đá phiên cũ nhất.
        if len(_active_sessions) > MAX_ACTIVE_SESSIONS:
            oldest = sorted(_active_sessions.items(), key=lambda kv: kv[1]["created"])
            for old_sid, _ in oldest[: len(_active_sessions) - MAX_ACTIVE_SESSIONS]:
                _active_sessions.pop(old_sid, None)
    session.permanent = True
    session["admin"] = True
    session["sid"] = sid
    session["email"] = email
    return jsonify({"success": True})


@app.route("/logout", methods=["GET", "POST"])
def logout():
    sid = session.get("sid")
    with _otp_lock:
        _active_sessions.pop(sid, None)
    session.clear()
    return redirect(url_for("login"))


# ── API cho CLIENT ──
@app.route("/api/register", methods=["POST"])
def api_register():
    """Client gửi thông tin máy -> server tạo key + gửi email."""
    ip = _client_ip()
    if not _rate_ok(ip, limit=8, window=60, bucket="register"):
        return jsonify({"success": False, "error": "Quá nhiều yêu cầu, thử lại sau ít phút."}), 429
    data = request.get_json(silent=True) or {}
    mac = str(data.get("mac") or "").strip().upper()
    machine_name = str(data.get("machineName") or data.get("machine_name") or "").strip()
    plan_key = str(data.get("plan") or data.get("planKey") or "3m").strip().lower()
    if not mac:
        return jsonify({"success": False, "error": "Thiếu địa chỉ MAC."}), 400
    if plan_key not in PLAN_OPTIONS:
        plan_key = "3m"
    days, plan_label, is_permanent = PLAN_OPTIONS[plan_key]
    key = _gen_key(12)
    max_machines = _plan_max_machines(plan_key)
    expiry = "" if is_permanent else (datetime.now() + timedelta(days=days)).isoformat(timespec="seconds")
    ip = _client_ip()
    now = _now()

    with _db_lock, closing(_conn()) as conn:
        existing = conn.execute("SELECT * FROM machines WHERE mac=?", (mac,)).fetchone()
        if existing:
            # Máy đã tồn tại: cấp key mới cho gói mới (đổi/gia hạn), đặt pending chờ nhập.
            conn.execute(
                """UPDATE machines SET machine_name=?, ip=?, plan_key=?, plan_label=?,
                   license_key=?, is_permanent=?, expiry=?, status='pending',
                   email_sent_at=?, key_issued_at=?, max_machines=?, note='' WHERE mac=?""",
                (machine_name or existing["machine_name"], ip, plan_key, plan_label,
                 key, 1 if is_permanent else 0, expiry, now, now, max_machines, mac),
            )
        else:
            conn.execute(
                """INSERT INTO machines (mac, machine_name, ip, plan_key, plan_label, license_key,
                   status, is_permanent, created_at, expiry, email_sent_at, key_issued_at, max_machines)
                   VALUES (?,?,?,?,?,?,'pending',?,?,?,?,?,?)""",
                (mac, machine_name, ip, plan_key, plan_label, key,
                 1 if is_permanent else 0, now, expiry, now, now, max_machines),
            )
        conn.commit()
        machine = _row_to_dict(conn.execute("SELECT * FROM machines WHERE mac=?", (mac,)).fetchone())

    sent = send_key_email(machine, key)
    return jsonify({
        "success": True,
        "sent": sent,
        "status": "pending",
        "plan": plan_key,
        "planLabel": plan_label,
        "message": "Đã tạo key và gửi email." if sent else "Đã tạo key nhưng gửi email thất bại — kiểm tra cấu hình email server.",
    })


def _resolve_key_template(conn, key):
    """Tìm 'khuôn' của 1 key nhiều máy (doanh nghiệp) để MAC mới kích hoạt kế thừa.

    Ưu tiên: (1) máy đã đăng ký với key có max_machines>1 (tương thích cũ);
    (2) key do admin tạo sẵn trong bảng ``business_keys``. Trả dict hoặc None.
    """
    ent = conn.execute(
        "SELECT * FROM machines WHERE license_key=? AND max_machines>1 ORDER BY id LIMIT 1",
        (key,),
    ).fetchone()
    if ent:
        d = _row_to_dict(ent)
        return {
            "plan_key": d["plan_key"], "plan_label": d["plan_label"],
            "is_permanent": d["is_permanent"], "expiry": d["expiry"],
            "max_machines": int(d["max_machines"]),
            "key_issued_at": d.get("key_issued_at") or "",
            "email_sent_at": d.get("email_sent_at") or "", "status": "active",
        }
    b = conn.execute("SELECT * FROM business_keys WHERE license_key=?", (key,)).fetchone()
    if b:
        b = dict(b)
        return {
            "plan_key": b["plan_key"], "plan_label": b["plan_label"],
            "is_permanent": b["is_permanent"], "expiry": b["expiry"],
            "max_machines": int(b["max_machines"]),
            "key_issued_at": b.get("key_issued_at") or "",
            "email_sent_at": "", "status": b.get("status") or "active",
        }
    return None


@app.route("/api/verify", methods=["POST"])
def api_verify():
    """Client xác thực key. Trả quyền tính năng nếu hợp lệ + còn hạn + active."""
    ip = _client_ip()
    blocked_for = _is_blocked(ip)
    if blocked_for:
        return jsonify({"valid": False, "error": f"Tạm khóa do thử sai quá nhiều. Thử lại sau {blocked_for}s."}), 429
    if not _rate_ok(ip, limit=40, window=60, bucket="verify"):
        return jsonify({"valid": False, "error": "Quá nhiều yêu cầu, thử lại sau ít phút."}), 429
    data = request.get_json(silent=True) or {}
    mac = str(data.get("mac") or "").strip().upper()
    key = str(data.get("key") or "").strip().upper()
    machine_name = str(data.get("machineName") or data.get("machine_name") or "").strip()
    if not mac or not key:
        return jsonify({"valid": False, "error": "Thiếu MAC hoặc key."}), 400

    with _db_lock, closing(_conn()) as conn:
        row = conn.execute("SELECT * FROM machines WHERE mac=?", (mac,)).fetchone()

        # Gói DOANH NGHIỆP: 1 key dùng cho nhiều MAC. Nếu MAC này chưa gắn key đó
        # mà key là key doanh nghiệp còn chỗ (< max_machines) thì gắn thêm MAC.
        if (not row) or (row["license_key"] != key):
            ent = _resolve_key_template(conn, key)
            if ent and int(ent["max_machines"]) > 1:
                if ent.get("status") == "disabled":
                    return jsonify({"valid": False, "error": "Key doanh nghiệp đã bị vô hiệu hóa."}), 403
                bound = conn.execute(
                    "SELECT COUNT(DISTINCT mac) AS c FROM machines WHERE license_key=?", (key,)
                ).fetchone()["c"]
                already = conn.execute(
                    "SELECT 1 FROM machines WHERE license_key=? AND mac=?", (key, mac)
                ).fetchone()
                if not already and bound >= int(ent["max_machines"]):
                    _record_verify_fail(ip)
                    return jsonify({"valid": False, "error": f"Key doanh nghiệp đã đủ {ent['max_machines']} máy."}), 403
                # Gắn MAC này vào key doanh nghiệp (kế thừa gói/HẠN DÙNG CHUNG của key).
                conn.execute(
                    """INSERT INTO machines (mac, machine_name, ip, plan_key, plan_label, license_key,
                       status, is_permanent, created_at, activated_at, expiry, last_seen,
                       email_sent_at, key_issued_at, max_machines)
                       VALUES (?,?,?,?,?,?,'active',?,?,?,?,?,?,?,?)
                       ON CONFLICT(mac) DO UPDATE SET plan_key=excluded.plan_key,
                         plan_label=excluded.plan_label, license_key=excluded.license_key,
                         is_permanent=excluded.is_permanent, expiry=excluded.expiry,
                         status='active', max_machines=excluded.max_machines,
                         key_issued_at=excluded.key_issued_at""",
                    (mac, machine_name, _client_ip(), ent["plan_key"], ent["plan_label"], key,
                     1 if ent["is_permanent"] else 0, _now(), _now(), ent["expiry"], _now(),
                     ent.get("email_sent_at") or "", ent.get("key_issued_at") or "", int(ent["max_machines"])),
                )
                conn.commit()
                row = conn.execute("SELECT * FROM machines WHERE mac=?", (mac,)).fetchone()
            elif not row:
                return jsonify({"valid": False, "error": "Máy chưa đăng ký với máy chủ."}), 404
            else:
                _record_verify_fail(ip)  # sai key -> đếm để chống dò
                return jsonify({"valid": False, "error": "Key không đúng cho máy này."}), 403

        m = _row_to_dict(row)
        if m["license_key"] != key:
            _record_verify_fail(ip)
            return jsonify({"valid": False, "error": "Key không đúng cho máy này."}), 403
        # Kill-switch: mọi key cấp TRƯỚC mốc key_cutoff đều bị vô hiệu (kể cả key
        # chưa có key_issued_at = key cũ trước bản nâng cấp) -> buộc đăng ký lại.
        cutoff = get_setting("key_cutoff", "")
        if cutoff:
            issued = str(m.get("key_issued_at") or "")
            if not issued or issued < cutoff:
                return jsonify({"valid": False, "error": "Key cũ đã bị vô hiệu hóa. Vui lòng đăng ký lại để nhận key mới."}), 403
        if m["status"] == "disabled":
            return jsonify({"valid": False, "error": "License đã bị hủy kích hoạt."}), 403
        # Kiểm hạn dùng
        if not m["is_permanent"] and m["expiry"]:
            try:
                if datetime.now() > datetime.fromisoformat(m["expiry"]):
                    conn.execute("UPDATE machines SET status='expired' WHERE mac=?", (mac,))
                    conn.commit()
                    return jsonify({"valid": False, "error": "License đã hết hạn."}), 403
            except ValueError:
                pass
        # Hợp lệ: đánh dấu active + cập nhật last_seen (lần nhập key đầu = kích hoạt).
        activated_at = m["activated_at"] or _now()
        conn.execute(
            "UPDATE machines SET status='active', activated_at=?, last_seen=?, ip=?, machine_name=? WHERE mac=?",
            (activated_at, _now(), _client_ip(), machine_name or m["machine_name"], mac),
        )
        conn.commit()
        m = _row_to_dict(conn.execute("SELECT * FROM machines WHERE mac=?", (mac,)).fetchone())

    _record_verify_ok(ip)  # xác thực đúng -> reset bộ đếm sai
    days_remaining = 99999
    if not m["is_permanent"] and m["expiry"]:
        try:
            days_remaining = max(0, (datetime.fromisoformat(m["expiry"]) - datetime.now()).days)
        except ValueError:
            days_remaining = 0
    return jsonify({
        "valid": True,
        "plan": m["plan_key"],
        "planLabel": m["plan_label"],
        "isPermanent": bool(m["is_permanent"]),
        "expiry": m["expiry"],
        "daysRemaining": days_remaining,
        "maxAccounts": m["maxAccounts"],
        "multiAccountExec": m["multiAccountExec"],
    })


# ── Dashboard + API admin ──
@app.route("/")
def dashboard():
    if not _require_admin():
        return redirect(url_for("login"))
    return render_template("dashboard.html", plans=PLAN_OPTIONS)


@app.route("/api/admin/settings", methods=["GET", "POST"])
def api_settings():
    """Đọc/lưu cấu hình email nhận key. Env = mặc định (khóa), dashboard thêm phụ."""
    if not _require_admin():
        return jsonify({"success": False, "error": "unauthorized"}), 401
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        raw = data.get("extraEmails", "")
        if isinstance(raw, list):
            emails = [str(e).strip() for e in raw if str(e).strip()]
        else:
            emails = [e.strip() for e in str(raw).replace("\n", ",").split(",") if e.strip()]
        # Chỉ giữ email hợp lệ cơ bản.
        emails = [e for e in emails if "@" in e and "." in e.split("@")[-1]]
        set_setting("extra_emails", ",".join(emails))
    return jsonify({
        "success": True,
        "defaultEmails": [_mask_email(e) for e in ADMIN_EMAILS],  # ẩn 2 gmail env mặc định
        "defaultCount": len(ADMIN_EMAILS),
        "extraEmails": get_extra_emails(),                        # gmail phụ (hiện đầy đủ để sửa)
        "recipientCount": len(get_all_recipients()),
    })


@app.route("/api/admin/machines", methods=["GET"])
def api_machines():
    if not _require_admin():
        return jsonify({"success": False, "error": "unauthorized"}), 401
    _purge_stale_pending()  # dọn key chưa kích hoạt quá 24h mỗi lần mở dashboard
    status = (request.args.get("status") or "").strip()
    q = (request.args.get("q") or "").strip().lower()       # CHỈ lọc theo MAC
    date_from = (request.args.get("from") or "").strip()    # lọc theo created_at
    date_to = (request.args.get("to") or "").strip()

    with closing(_conn()) as conn:
        rows = conn.execute("SELECT * FROM machines ORDER BY created_at DESC").fetchall()
    items = []
    for r in rows:
        m = _row_to_dict(r)
        if status and m["status"] != status:
            continue
        if q and q not in str(m.get("mac", "")).lower():   # lọc theo MAC
            continue
        created = str(m.get("created_at") or "")[:10]
        if date_from and created and created < date_from:
            continue
        if date_to and created and created > date_to:
            continue
        items.append(m)

    # Thống kê theo THÁNG HIỆN TẠI (YYYY-MM).
    month = datetime.now().strftime("%Y-%m")

    def _mon(v):
        return str(v or "")[:7]

    rowd = [_row_to_dict(r) for r in rows]
    new_this_month = sum(1 for m in rowd if _mon(m.get("created_at")) == month)
    # Tái mua / tái kích hoạt: máy ĐÃ đăng ký từ trước, tháng này được cấp key mới.
    reactivated_this_month = sum(
        1 for m in rowd
        if _mon(m.get("key_issued_at")) == month and _mon(m.get("created_at")) < month
    )
    activated_this_month = sum(1 for m in rowd if _mon(m.get("activated_at")) == month)
    expiring_this_month = sum(
        1 for m in rowd
        if not m.get("is_permanent") and _mon(m.get("expiry")) == month
    )

    stats = {
        "total": len(rows),
        "active": sum(1 for r in rows if r["status"] == "active"),
        "pending": sum(1 for r in rows if r["status"] == "pending"),
        "disabled": sum(1 for r in rows if r["status"] == "disabled"),
        "expired": sum(1 for r in rows if r["status"] == "expired"),
        "month": month,
        "newThisMonth": new_this_month,
        "reactivatedThisMonth": reactivated_this_month,
        "activatedThisMonth": activated_this_month,
        "expiringThisMonth": expiring_this_month,
    }
    return jsonify({"success": True, "machines": items, "stats": stats})


@app.route("/api/admin/business-keys", methods=["GET"])
def api_business_keys():
    """Danh sách key doanh nghiệp + số máy đã kích hoạt trên mỗi key.

    Gộp 2 nguồn: (1) key admin tạo qua nút (bảng business_keys); (2) key doanh
    nghiệp phát QUA ĐĂNG KÝ MÁY (machines.max_machines>1) chưa có trong bảng đó.
    """
    if not _require_admin():
        return jsonify({"success": False, "error": "unauthorized"}), 401
    with closing(_conn()) as conn:
        items = []
        seen = set()
        for r in conn.execute("SELECT * FROM business_keys ORDER BY created_at DESC").fetchall():
            d = dict(r)
            d["source"] = "db"
            d["bound"] = conn.execute(
                "SELECT COUNT(DISTINCT mac) AS c FROM machines WHERE license_key=?",
                (d["license_key"],),
            ).fetchone()["c"]
            items.append(d)
            seen.add(d["license_key"])
        # Key doanh nghiệp phát qua đăng ký máy (gói enterprise cũ) — gom theo key.
        mrows = conn.execute(
            """SELECT license_key, plan_key, plan_label, MAX(is_permanent) AS is_permanent,
                      MAX(expiry) AS expiry, MAX(max_machines) AS max_machines,
                      MIN(created_at) AS created_at, COUNT(DISTINCT mac) AS bound,
                      SUM(CASE WHEN status!='disabled' THEN 1 ELSE 0 END) AS active_cnt
               FROM machines WHERE max_machines>1 AND license_key!=''
               GROUP BY license_key ORDER BY MIN(created_at) DESC"""
        ).fetchall()
        for mr in mrows:
            d = dict(mr)
            if d["license_key"] in seen:
                continue
            d["source"] = "machine"
            d["status"] = "active" if int(d.get("active_cnt") or 0) > 0 else "disabled"
            d["note"] = ""
            items.append(d)
    return jsonify({"success": True, "keys": items})


@app.route("/api/admin/business-keys", methods=["POST"])
def api_create_business_key():
    """Tạo 1 KEY DOANH NGHIỆP dùng cho nhiều MAC (mặc định 10). Máy khác chỉ cần
    nhập key này để kích hoạt; hạn dùng tính từ lúc tạo, DÙNG CHUNG cho mọi máy."""
    if not _require_admin():
        return jsonify({"success": False, "error": "unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    try:
        max_machines = int(data.get("maxMachines") or data.get("max_machines") or 10)
    except (TypeError, ValueError):
        max_machines = 10
    max_machines = max(1, min(max_machines, 1000))

    # Thời hạn: nhận days (0 = vĩnh viễn) nếu có, ngược lại theo plan_key.
    plan_key = str(data.get("plan") or data.get("plan_key") or "enterprise").strip().lower()
    if data.get("days") is not None or data.get("permanent") is not None:
        is_permanent = bool(data.get("permanent"))
        try:
            days = int(data.get("days") or 0)
        except (TypeError, ValueError):
            days = 0
        plan_label = str(data.get("planLabel") or "").strip()
    else:
        days, plan_label, is_permanent = PLAN_OPTIONS.get(plan_key, (0, "Doanh nghiệp", True))

    if is_permanent or days <= 0:
        is_permanent, expiry = True, ""
    else:
        is_permanent = False
        expiry = (datetime.now() + timedelta(days=days)).isoformat(timespec="seconds")
    if not plan_label:
        plan_label = f"Doanh nghiệp ({max_machines} máy)" + ("" if is_permanent else f" · {days} ngày")

    email = str(data.get("email") or "").strip()
    note = str(data.get("note") or "").strip()
    now = _now()
    with _db_lock, closing(_conn()) as conn:
        key = ""
        for _ in range(30):  # gen key không trùng (cả business_keys lẫn machines)
            cand = _gen_key(14)
            dup = conn.execute(
                "SELECT 1 FROM business_keys WHERE license_key=? "
                "UNION SELECT 1 FROM machines WHERE license_key=?", (cand, cand)
            ).fetchone()
            if not dup:
                key = cand
                break
        if not key:
            return jsonify({"success": False, "error": "Không tạo được key duy nhất, thử lại."}), 500
        conn.execute(
            """INSERT INTO business_keys (license_key, plan_key, plan_label, is_permanent,
               expiry, max_machines, email, note, status, created_at, key_issued_at)
               VALUES (?,?,?,?,?,?,?,?, 'active', ?, ?)""",
            (key, plan_key, plan_label, 1 if is_permanent else 0, expiry,
             max_machines, email, note, now, now),
        )
        conn.commit()
    return jsonify({
        "success": True, "key": key, "planKey": plan_key, "planLabel": plan_label,
        "isPermanent": is_permanent, "expiry": expiry, "maxMachines": max_machines,
    })


@app.route("/api/admin/business-keys/<int:bid>/status", methods=["POST"])
def api_business_key_status(bid):
    """Bật/tắt (active|disabled) 1 key doanh nghiệp. Tắt sẽ khóa luôn các máy đã
    kích hoạt bằng key đó."""
    if not _require_admin():
        return jsonify({"success": False, "error": "unauthorized"}), 401
    new_status = str((request.get_json(silent=True) or {}).get("status") or "").strip().lower()
    if new_status not in ("active", "disabled"):
        return jsonify({"success": False, "error": "status phải là active hoặc disabled."}), 400
    with _db_lock, closing(_conn()) as conn:
        row = conn.execute("SELECT * FROM business_keys WHERE id=?", (bid,)).fetchone()
        if not row:
            return jsonify({"success": False, "error": "Không tìm thấy key."}), 404
        conn.execute("UPDATE business_keys SET status=? WHERE id=?", (new_status, bid))
        # Đồng bộ trạng thái các máy đã kích hoạt bằng key này.
        conn.execute(
            "UPDATE machines SET status=? WHERE license_key=?",
            ("disabled" if new_status == "disabled" else "active", row["license_key"]),
        )
        conn.commit()
    return jsonify({"success": True, "status": new_status})


@app.route("/api/admin/business-keys/status", methods=["POST"])
def api_business_key_status_by_key():
    """Bật/tắt key doanh nghiệp THEO license_key — dùng cho cả key tạo qua nút lẫn
    key phát qua đăng ký máy. Đồng bộ cả bảng business_keys lẫn machines."""
    if not _require_admin():
        return jsonify({"success": False, "error": "unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    key = str(data.get("key") or "").strip().upper()
    new_status = str(data.get("status") or "").strip().lower()
    if not key:
        return jsonify({"success": False, "error": "Thiếu key."}), 400
    if new_status not in ("active", "disabled"):
        return jsonify({"success": False, "error": "status phải là active hoặc disabled."}), 400
    with _db_lock, closing(_conn()) as conn:
        conn.execute("UPDATE business_keys SET status=? WHERE license_key=?", (new_status, key))
        conn.execute("UPDATE machines SET status=? WHERE license_key=?", (new_status, key))
        conn.commit()
    return jsonify({"success": True, "status": new_status})


@app.route("/api/admin/business-keys/<int:bid>", methods=["DELETE"])
def api_delete_business_key(bid):
    """Xóa key doanh nghiệp. Không xóa các máy đã kích hoạt (giữ lịch sử); chỉ gỡ
    khuôn key nên không thể gắn thêm MAC mới bằng key này nữa."""
    if not _require_admin():
        return jsonify({"success": False, "error": "unauthorized"}), 401
    with _db_lock, closing(_conn()) as conn:
        conn.execute("DELETE FROM business_keys WHERE id=?", (bid,))
        conn.commit()
    return jsonify({"success": True})


def _set_status(machine_id, new_status):
    with _db_lock, closing(_conn()) as conn:
        row = conn.execute("SELECT * FROM machines WHERE id=?", (machine_id,)).fetchone()
        if not row:
            return None
        conn.execute("UPDATE machines SET status=? WHERE id=?", (new_status, machine_id))
        conn.commit()
        return _row_to_dict(conn.execute("SELECT * FROM machines WHERE id=?", (machine_id,)).fetchone())


@app.route("/api/admin/machines/<int:machine_id>/activate", methods=["POST"])
def api_activate(machine_id):
    if not _require_admin():
        return jsonify({"success": False, "error": "unauthorized"}), 401
    m = _set_status(machine_id, "active")
    if not m:
        return jsonify({"success": False, "error": "Không tìm thấy máy."}), 404
    return jsonify({"success": True, "machine": m})


@app.route("/api/admin/machines/<int:machine_id>/deactivate", methods=["POST"])
def api_deactivate(machine_id):
    if not _require_admin():
        return jsonify({"success": False, "error": "unauthorized"}), 401
    m = _set_status(machine_id, "disabled")
    if not m:
        return jsonify({"success": False, "error": "Không tìm thấy máy."}), 404
    return jsonify({"success": True, "machine": m})


@app.route("/api/admin/machines/<int:machine_id>/plan", methods=["POST"])
def api_change_plan(machine_id):
    """Đổi gói của một máy (tăng/hạ). Tính lại hạn dùng từ thời điểm đổi."""
    if not _require_admin():
        return jsonify({"success": False, "error": "unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    plan_key = str(data.get("plan") or "").strip().lower()
    if plan_key not in PLAN_OPTIONS:
        return jsonify({"success": False, "error": "Gói không hợp lệ."}), 400
    days, plan_label, is_permanent = PLAN_OPTIONS[plan_key]
    expiry = "" if is_permanent else (datetime.now() + timedelta(days=days)).isoformat(timespec="seconds")
    with _db_lock, closing(_conn()) as conn:
        row = conn.execute("SELECT * FROM machines WHERE id=?", (machine_id,)).fetchone()
        if not row:
            return jsonify({"success": False, "error": "Không tìm thấy máy."}), 404
        conn.execute(
            "UPDATE machines SET plan_key=?, plan_label=?, is_permanent=?, expiry=? WHERE id=?",
            (plan_key, plan_label, 1 if is_permanent else 0, expiry, machine_id),
        )
        conn.commit()
        m = _row_to_dict(conn.execute("SELECT * FROM machines WHERE id=?", (machine_id,)).fetchone())
    return jsonify({"success": True, "machine": m})


@app.route("/api/admin/machines/<int:machine_id>/regen", methods=["POST"])
def api_regen(machine_id):
    """Tạo lại key cho máy + gửi email lại."""
    if not _require_admin():
        return jsonify({"success": False, "error": "unauthorized"}), 401
    key = _gen_key(12)
    with _db_lock, closing(_conn()) as conn:
        row = conn.execute("SELECT * FROM machines WHERE id=?", (machine_id,)).fetchone()
        if not row:
            return jsonify({"success": False, "error": "Không tìm thấy máy."}), 404
        conn.execute("UPDATE machines SET license_key=?, status='pending', email_sent_at=?, key_issued_at=? WHERE id=?",
                     (key, _now(), _now(), machine_id))
        conn.commit()
        m = _row_to_dict(conn.execute("SELECT * FROM machines WHERE id=?", (machine_id,)).fetchone())
    sent = send_key_email(m, key)
    return jsonify({"success": True, "sent": sent, "machine": m})


@app.route("/api/admin/machines/<int:machine_id>", methods=["DELETE"])
def api_delete(machine_id):
    if not _require_admin():
        return jsonify({"success": False, "error": "unauthorized"}), 401
    with _db_lock, closing(_conn()) as conn:
        conn.execute("DELETE FROM machines WHERE id=?", (machine_id,))
        conn.commit()
    return jsonify({"success": True})


@app.route("/api/admin/key-cutoff", methods=["GET"])
def api_key_cutoff_status():
    """Trạng thái kill-switch: mốc cắt hiện tại + số máy đang bị coi là key cũ."""
    if not _require_admin():
        return jsonify({"success": False, "error": "unauthorized"}), 401
    cutoff = get_setting("key_cutoff", "")
    affected = 0
    if cutoff:
        with closing(_conn()) as conn:
            rows = conn.execute("SELECT key_issued_at FROM machines").fetchall()
        affected = sum(1 for r in rows if not (r["key_issued_at"] or "") or str(r["key_issued_at"]) < cutoff)
    return jsonify({"success": True, "cutoff": cutoff, "affected": affected})


@app.route("/api/admin/invalidate-old-keys", methods=["POST"])
def api_invalidate_old_keys():
    """Vô hiệu hóa TẤT CẢ key cấp trước thời điểm này (buộc đăng ký lại).

    Gửi {"clear": true} để bỏ mốc cắt (khôi phục hiệu lực key cũ).
    """
    if not _require_admin():
        return jsonify({"success": False, "error": "unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    if data.get("clear"):
        set_setting("key_cutoff", "")
        return jsonify({"success": True, "cutoff": "", "message": "Đã bỏ mốc cắt — key cũ có hiệu lực trở lại."})
    cutoff = _now()
    with closing(_conn()) as conn:
        rows = conn.execute("SELECT key_issued_at FROM machines").fetchall()
    affected = sum(1 for r in rows if not (r["key_issued_at"] or "") or str(r["key_issued_at"]) < cutoff)
    set_setting("key_cutoff", cutoff)
    return jsonify({
        "success": True,
        "cutoff": cutoff,
        "affected": affected,
        "message": f"Đã vô hiệu hóa {affected} key cũ. Các máy này phải đăng ký lại để nhận key mới.",
    })


@app.route("/health")
def health():
    return jsonify({"ok": True, "service": "nexus-license-server"})


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "5555")))
    parser.add_argument("--host", default=os.getenv("HOST", "0.0.0.0"))
    args = parser.parse_args()
    print(f"[license_server] Chạy tại http://{args.host}:{args.port}  (dashboard: /?token=...)")
    app.run(host=args.host, port=args.port, threaded=True)


if __name__ == "__main__":
    main()
