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

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "data", "licenses.db")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)


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
# Token bảo vệ dashboard + API admin (đặt trong .env). Rỗng = không chặn (chỉ nên dùng khi chạy nội bộ).
ADMIN_TOKEN = (os.getenv("ADMIN_TOKEN", "") or "").strip()

# Gói: key -> (số ngày, nhãn, is_permanent)
PLAN_OPTIONS = {
    "3d": (3, "3 Ngày", False),
    "7d": (7, "1 Tuần", False),
    "10d": (10, "10 Ngày", False),
    "1m": (30, "1 Tháng", False),
    "2m": (60, "2 Tháng", False),
    "3m": (90, "3 Tháng", False),
    "6m": (180, "6 Tháng", False),
    "lifetime": (0, "Vĩnh viễn", True),
}

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
    body = f"""
    <html><body style="font-family:Arial,sans-serif;background:#f5f5f5;padding:16px">
      <div style="max-width:600px;margin:auto;background:#fff;border-radius:10px;padding:22px">
        <h2 style="color:#2563eb">🔑 Nexus - Key kích hoạt</h2>
        <p><b>Máy:</b> {machine.get('machine_name') or '-'}</p>
        <p><b>MAC:</b> <code>{machine.get('mac')}</code></p>
        <p><b>IP:</b> {machine.get('ip') or '-'}</p>
        <p><b>Gói:</b> {plan_label} &nbsp; | &nbsp; <b>Hết hạn:</b> {expiry}</p>
        <div style="font-size:26px;font-weight:800;letter-spacing:4px;text-align:center;
                    background:#f0f7ff;border:2px solid #2563eb;border-radius:8px;padding:16px;margin:14px 0">
          {key}
        </div>
        <p style="color:#666;font-size:13px">Nhập key này vào phần mềm Nexus trên đúng máy có MAC ở trên để kích hoạt.</p>
      </div>
    </body></html>
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
init_db()


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


def _require_admin():
    """Đã đăng nhập (session) HOẶC token đúng (cho script/API)."""
    if not ADMIN_TOKEN:
        return True
    if session.get("admin") is True:
        return True
    token = request.headers.get("X-Admin-Token") or request.args.get("token") or ""
    if request.is_json:
        token = token or (request.get_json(silent=True) or {}).get("token", "")
    return token == ADMIN_TOKEN


@app.route("/login", methods=["GET", "POST"])
def login():
    """Trang đăng nhập dashboard. Token so khớp ADMIN_TOKEN trong .env."""
    if not ADMIN_TOKEN:
        session["admin"] = True
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        data = request.get_json(silent=True) or request.form or {}
        token = str(data.get("token") or "").strip()
        if token and token == ADMIN_TOKEN:
            session["admin"] = True
            session.permanent = True
            return jsonify({"success": True})
        return jsonify({"success": False, "error": "Mật khẩu quản trị không đúng."}), 401
    if session.get("admin") is True:
        return redirect(url_for("dashboard"))
    return render_template("login.html")


@app.route("/logout", methods=["GET", "POST"])
def logout():
    session.pop("admin", None)
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
    plan_key = str(data.get("plan") or data.get("planKey") or "1m").strip().lower()
    if not mac:
        return jsonify({"success": False, "error": "Thiếu địa chỉ MAC."}), 400
    if plan_key not in PLAN_OPTIONS:
        plan_key = "1m"
    days, plan_label, is_permanent = PLAN_OPTIONS[plan_key]
    key = _gen_key(12)
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
                   email_sent_at=?, note='' WHERE mac=?""",
                (machine_name or existing["machine_name"], ip, plan_key, plan_label,
                 key, 1 if is_permanent else 0, expiry, now, mac),
            )
        else:
            conn.execute(
                """INSERT INTO machines (mac, machine_name, ip, plan_key, plan_label, license_key,
                   status, is_permanent, created_at, expiry, email_sent_at)
                   VALUES (?,?,?,?,?,?,'pending',?,?,?,?)""",
                (mac, machine_name, ip, plan_key, plan_label, key,
                 1 if is_permanent else 0, now, expiry, now),
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
        if not row:
            return jsonify({"valid": False, "error": "Máy chưa đăng ký với máy chủ."}), 404
        m = _row_to_dict(row)
        if m["license_key"] != key:
            _record_verify_fail(ip)  # sai key -> đếm để chống dò
            return jsonify({"valid": False, "error": "Key không đúng cho máy này."}), 403
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
        "defaultEmails": ADMIN_EMAILS,      # 2 gmail env mặc định (không sửa qua UI)
        "extraEmails": get_extra_emails(),  # gmail phụ thêm qua dashboard
        "allRecipients": get_all_recipients(),
    })


@app.route("/api/admin/machines", methods=["GET"])
def api_machines():
    if not _require_admin():
        return jsonify({"success": False, "error": "unauthorized"}), 401
    status = (request.args.get("status") or "").strip()
    q = (request.args.get("q") or "").strip().lower()       # lọc tên máy / mac / ip
    ip = (request.args.get("ip") or "").strip().lower()
    date_from = (request.args.get("from") or "").strip()    # lọc theo created_at
    date_to = (request.args.get("to") or "").strip()

    with closing(_conn()) as conn:
        rows = conn.execute("SELECT * FROM machines ORDER BY created_at DESC").fetchall()
    items = []
    for r in rows:
        m = _row_to_dict(r)
        if status and m["status"] != status:
            continue
        if q and q not in (str(m.get("machine_name", "")).lower()
                            + str(m.get("mac", "")).lower()
                            + str(m.get("ip", "")).lower()):
            continue
        if ip and ip not in str(m.get("ip", "")).lower():
            continue
        created = str(m.get("created_at") or "")[:10]
        if date_from and created and created < date_from:
            continue
        if date_to and created and created > date_to:
            continue
        items.append(m)

    stats = {
        "total": len(rows),
        "active": sum(1 for r in rows if r["status"] == "active"),
        "pending": sum(1 for r in rows if r["status"] == "pending"),
        "disabled": sum(1 for r in rows if r["status"] == "disabled"),
        "expired": sum(1 for r in rows if r["status"] == "expired"),
    }
    return jsonify({"success": True, "machines": items, "stats": stats})


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
        conn.execute("UPDATE machines SET license_key=?, status='pending', email_sent_at=? WHERE id=?",
                     (key, _now(), machine_id))
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
