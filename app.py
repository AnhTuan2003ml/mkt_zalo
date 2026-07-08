from flask import Flask, render_template, request, jsonify, Response, redirect, url_for
import sys
import os
import json
import time
import queue
import threading
import requests
import re
import uuid
from datetime import datetime, timedelta

# ─── Get App Root (PyInstaller compatible) ─────────────────────────────────────
def get_app_root():
    """Get application root directory (works both in source and PyInstaller frozen mode)."""
    if getattr(sys, "frozen", False):
        # PyInstaller --onefile: data/profiles tạo cùng folder exe gốc, không trong temp folder
        # Nên dùng sys.executable directory (nơi exe được chạy)
        return os.path.dirname(sys.executable)
    else:
        # Running as Python script
        return os.path.dirname(os.path.abspath(__file__))

def get_base_path():
    """Get base path for Flask resources (templates, static)."""
    if getattr(sys, "frozen", False):
        # PyInstaller --onefile: templates, static extract vào sys._MEIPASS (temp folder)
        return sys._MEIPASS
    else:
        # Running as Python script
        return os.path.dirname(os.path.abspath(__file__))

app_root = get_app_root()
base_path = get_base_path()
template_path = os.path.join(base_path, 'templates')
static_path = os.path.join(base_path, 'static')

app = Flask(__name__, 
            template_folder=template_path,
            static_folder=static_path)
app.secret_key = "zalo-tool-secret-key"
app.config['JSON_AS_ASCII'] = False  # Allow non-ASCII characters in JSON
app.config['JSONIFY_PRETTYPRINT_REGULAR'] = False

# ─── Setup sys.path for source/packaged modules ───────────────────────────────
if getattr(sys, "frozen", False):
    # PyInstaller: packages are extracted in sys._MEIPASS
    module_root = sys._MEIPASS
    auth_path = os.path.join(sys._MEIPASS, "authencation")
else:
    # Source mode: app root contains core/, features/, authencation/
    module_root = os.path.dirname(__file__)
    auth_path = os.path.join(module_root, "authencation")

if module_root not in sys.path:
    sys.path.insert(0, module_root)
if auth_path not in sys.path:
    sys.path.insert(0, auth_path)

from features.members.group_member_service import fetch_group_members_by_input, format_group_info
from features.profiles.profile_service import fetch_profiles_with_single_fallback, build_member_rows_from_uids
from features.messaging.send_sms import send_sms
from features.messaging.add_friend import send_friend_request
from features.groups.add_group import create_group as _create_group
from features.groups.invite_group import invite_group as _invite_group
from features.groups.group_join_leave import join_group_by_link as _join_group_by_link, leave_group as _leave_group
from features.profiles.get_single_profile import get_single_profile, ProfileRateLimitError
from features.accounts.account_manager import (
    load_accounts,
    create_account,
    open_account,
    delete_account,
    rename_account,
    update_account,
    get_account,
    _ensure_dirs,
)
from features.profiles.fetch_userinfo import (
    get_userinfo_quick,
    get_userinfo_batch,
    get_userinfo_v2,
)
from features.schedules.schedule_manager import (
    load_schedules as load_scheds,
    get_schedule,
    create_schedule,
    update_schedule,
    delete_schedule,
    cancel_schedule,
    get_all_schedules,
)
from features.schedules.schedule_worker import start_schedule_worker
from features.tasks.task_manager import (
    create_task,
    get_task,
    list_tasks,
    run_task_in_background,
    set_sse_broadcast_func,
)
from features.messages.message_manager import (
    list_conversations as list_message_conversations,
    get_conversation as get_message_conversation,
    create_conversation as create_message_conversation,
    add_message as add_conversation_message,
    update_conversation as update_message_conversation,
    mark_read as mark_message_read,
    get_stats as get_message_stats,
    get_settings as get_message_settings,
    update_settings as update_message_settings,
    log_check_attempt as log_message_check_attempt,
)
from core.zalo.zalo_config import get_zpw_ver


# ─── Friend request planning storage ─────────────────────────────────────────
FRIEND_PLANS_FILE = os.path.join(app_root, "data", "friend_invite_plans.json")
INVITE_GROUP_PLANS_FILE = os.path.join(app_root, "data", "group_invite_plans.json")


def _friend_now_ms():
    return int(time.time() * 1000)


def _ensure_friend_plan_store():
    os.makedirs(os.path.dirname(FRIEND_PLANS_FILE), exist_ok=True)


def _load_friend_plans():
    _ensure_friend_plan_store()
    if not os.path.isfile(FRIEND_PLANS_FILE):
        return []
    try:
        with open(FRIEND_PLANS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        plans = data.get("plans", data if isinstance(data, list) else [])
        return plans if isinstance(plans, list) else []
    except Exception as e:
        print("[_load_friend_plans] error=", e)
        return []


def _save_friend_plans(plans):
    _ensure_friend_plan_store()
    with open(FRIEND_PLANS_FILE, "w", encoding="utf-8") as f:
        json.dump({"plans": plans}, f, ensure_ascii=False, indent=2)


def _ensure_group_invite_plan_store():
    os.makedirs(os.path.dirname(INVITE_GROUP_PLANS_FILE), exist_ok=True)


def _load_group_invite_plans():
    _ensure_group_invite_plan_store()
    if not os.path.isfile(INVITE_GROUP_PLANS_FILE):
        return []
    try:
        with open(INVITE_GROUP_PLANS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        plans = data.get("plans", data if isinstance(data, list) else [])
        return plans if isinstance(plans, list) else []
    except Exception as e:
        print("[_load_group_invite_plans] error=", e)
        return []


def _save_group_invite_plans(plans):
    _ensure_group_invite_plan_store()
    with open(INVITE_GROUP_PLANS_FILE, "w", encoding="utf-8") as f:
        json.dump({"plans": plans}, f, ensure_ascii=False, indent=2)


def _normalize_plan_group(item):
    if isinstance(item, str):
        gid = item.strip()
        return {"groupId": gid, "name": gid, "avatar": "", "memberCount": 0} if gid else None
    if not isinstance(item, dict):
        return None
    gid = str(
        item.get("groupId")
        or item.get("grid")
        or item.get("gridId")
        or item.get("id")
        or ""
    ).strip()
    if not gid:
        return None
    return {
        "groupId": gid,
        "name": str(item.get("name") or item.get("groupName") or gid).strip(),
        "avatar": str(item.get("avatar") or item.get("fullAvt") or item.get("avt") or "").strip(),
        "memberCount": item.get("memberCount", item.get("totalMember", item.get("total", 0))),
    }


def _find_account_name(account_id: str) -> str:
    for acc in load_accounts():
        aid = str(acc.get("accountId") or acc.get("id") or acc.get("account_id") or "").strip()
        if aid == str(account_id).strip():
            return acc.get("name") or aid
    return str(account_id).strip()


def _normalize_plan_member(item):
    if isinstance(item, str):
        uid = item.strip()
        return {"userId": uid, "name": uid, "avatar": ""} if uid else None
    if not isinstance(item, dict):
        return None
    uid = str(
        item.get("userId")
        or item.get("uid")
        or item.get("id")
        or item.get("toId")
        or ""
    ).strip()
    if not uid:
        return None
    return {
        "userId": uid,
        "name": str(item.get("zaloName") or item.get("name") or item.get("displayName") or uid).strip(),
        "avatar": str(item.get("avatar") or item.get("avt") or item.get("avatarUrl") or "").strip(),
        "phone": str(item.get("phone") or "").strip(),
        "isFriend": item.get("isFr", item.get("isFriend", "")),
    }


def _build_friend_plan_batches(members, daily_limit: int, start_date: str):
    try:
        start = datetime.strptime(start_date, "%Y-%m-%d").date()
    except Exception:
        start = datetime.now().date()
        start_date = start.isoformat()

    daily_limit = max(1, min(int(daily_limit or 1), 500))
    batches = []
    for index in range(0, len(members), daily_limit):
        day_no = index // daily_limit
        date = (start + timedelta(days=day_no)).isoformat()
        chunk = members[index:index + daily_limit]
        batches.append({
            "day": day_no + 1,
            "date": date,
            "status": "pending_manual",
            "count": len(chunk),
            "members": chunk,
        })
    return batches

# ─── Friend/group invite plan progress helpers ───────────────────────────────
_PLAN_PENDING_STATUSES = {"", "pending", "pending_manual", "planned", "waiting"}
_PLAN_RUNNING_STATUSES = {"running", "processing", "in_progress"}
_PLAN_DONE_STATUSES = {"done", "completed", "success", "sent", "invited", "friend_sent"}
_PLAN_FAILED_STATUSES = {"failed", "error"}
_PLAN_SKIPPED_STATUSES = {"skipped", "cancelled", "canceled"}
_PLAN_TERMINAL_STATUSES = _PLAN_DONE_STATUSES | _PLAN_FAILED_STATUSES | _PLAN_SKIPPED_STATUSES
_ALLOWED_BATCH_STATUS = {"pending_manual", "running", "done", "failed", "skipped"}


def _to_int(value, default=0):
    try:
        if value is None or value == "":
            return default
        return int(value)
    except Exception:
        return default


def _normalize_plan_batch_status(status):
    status = str(status or "pending_manual").strip().lower()
    if status in ("complete", "completed", "success", "sent", "invited", "friend_sent"):
        return "done"
    if status in ("error",):
        return "failed"
    if status in ("cancelled", "canceled"):
        return "skipped"
    if status in ("processing", "in_progress"):
        return "running"
    if status not in _ALLOWED_BATCH_STATUS:
        return "pending_manual"
    return status


def _attach_action_plan_progress(plan: dict) -> dict:
    """Gắn progress tổng hợp cho plan. Không gọi API Zalo, chỉ đọc trạng thái batch đã lưu."""
    if not isinstance(plan, dict):
        return plan

    batches = plan.get("batches") or []
    if not isinstance(batches, list):
        batches = []
        plan["batches"] = batches

    total_members = _to_int(plan.get("totalMembers"), 0)
    if total_members <= 0:
        total_members = sum(_to_int(b.get("count"), len(b.get("members") or [])) for b in batches if isinstance(b, dict))

    total_batches = len(batches)
    done_batches = failed_batches = skipped_batches = running_batches = pending_batches = 0
    done_members = failed_members = skipped_members = 0

    for b in batches:
        if not isinstance(b, dict):
            continue
        status = _normalize_plan_batch_status(b.get("status"))
        b["status"] = status
        count = _to_int(b.get("count"), len(b.get("members") or []))

        if status == "done":
            done_batches += 1
            done_members += _to_int(b.get("successCount"), count)
            failed_members += _to_int(b.get("failedCount"), 0)
        elif status == "failed":
            failed_batches += 1
            failed_members += _to_int(b.get("failedCount"), count)
            done_members += _to_int(b.get("successCount"), 0)
        elif status == "skipped":
            skipped_batches += 1
            skipped_members += count
        elif status == "running":
            running_batches += 1
            done_members += _to_int(b.get("successCount"), 0)
            failed_members += _to_int(b.get("failedCount"), 0)
        else:
            pending_batches += 1
            done_members += _to_int(b.get("successCount"), 0)
            failed_members += _to_int(b.get("failedCount"), 0)

    processed_members = max(0, min(total_members, done_members + failed_members + skipped_members))
    progress_percent = round((processed_members * 100 / total_members), 1) if total_members else 0

    if total_batches and done_batches == total_batches:
        display_status = "completed"
    elif total_batches and (done_batches + failed_batches + skipped_batches) == total_batches:
        display_status = "partial" if done_batches else ("failed" if failed_batches else "cancelled")
    elif running_batches:
        display_status = "running"
    else:
        display_status = plan.get("status") or "planned"

    plan["progress"] = {
        "totalMembers": total_members,
        "processedMembers": processed_members,
        "doneMembers": done_members,
        "failedMembers": failed_members,
        "skippedMembers": skipped_members,
        "pendingMembers": max(0, total_members - processed_members),
        "totalBatches": total_batches,
        "doneBatches": done_batches,
        "failedBatches": failed_batches,
        "skippedBatches": skipped_batches,
        "runningBatches": running_batches,
        "pendingBatches": pending_batches,
        "percent": progress_percent,
        "displayStatus": display_status,
    }
    return plan


def _attach_action_plans_progress(plans):
    return [_attach_action_plan_progress(p) for p in (plans or []) if isinstance(p, dict)]


def _load_action_plan_store(plan_type: str):
    if plan_type == "friend":
        return _load_friend_plans()
    if plan_type == "group_invite":
        return _load_group_invite_plans()
    raise ValueError("Loại kế hoạch không hợp lệ")


def _save_action_plan_store(plan_type: str, plans):
    if plan_type == "friend":
        return _save_friend_plans(plans)
    if plan_type == "group_invite":
        return _save_group_invite_plans(plans)
    raise ValueError("Loại kế hoạch không hợp lệ")


def _update_action_plan_batch_status(plan_type: str, plan_id: str, batch_day, payload: dict):
    plans = _load_action_plan_store(plan_type)
    plan = None
    for item in plans:
        if str(item.get("id") or "") == str(plan_id):
            plan = item
            break
    if not plan:
        raise ValueError("Không tìm thấy kế hoạch")

    try:
        batch_day = int(batch_day)
    except Exception:
        raise ValueError("Ngày/batch không hợp lệ")

    batches = plan.get("batches") or []
    batch = None
    for item in batches:
        if isinstance(item, dict) and _to_int(item.get("day"), -1) == batch_day:
            batch = item
            break
    if not batch:
        raise ValueError("Không tìm thấy batch")

    status = _normalize_plan_batch_status(payload.get("status"))
    if status not in _ALLOWED_BATCH_STATUS:
        raise ValueError("Trạng thái batch không hợp lệ")

    now = _friend_now_ms()
    batch["status"] = status
    batch["updatedAt"] = now

    for key in ("note", "error", "result"):
        if key in payload:
            batch[key] = payload.get(key)

    if "successCount" in payload:
        batch["successCount"] = max(0, _to_int(payload.get("successCount"), 0))
    elif status == "done" and "successCount" not in batch:
        batch["successCount"] = _to_int(batch.get("count"), len(batch.get("members") or []))

    if "failedCount" in payload:
        batch["failedCount"] = max(0, _to_int(payload.get("failedCount"), 0))
    elif status in ("done", "skipped") and "failedCount" not in batch:
        batch["failedCount"] = 0

    if status == "failed" and "failedCount" not in batch:
        batch["failedCount"] = _to_int(batch.get("count"), len(batch.get("members") or []))

    plan["updatedAt"] = now
    _attach_action_plan_progress(plan)
    progress = plan.get("progress") or {}
    plan["status"] = progress.get("displayStatus") or plan.get("status") or "planned"

    _save_action_plan_store(plan_type, plans)
    return plan, batch

# ─── Device Activation / Info (optional - for device management) ─────────────
try:
    from authencation.send_info_device import (
        register_device_on_startup,
        register_device_with_activation,
        check_activation_on_startup,
        validate_activation_code,
        save_user_activation_code,
        send_device_log,
        get_activation_duration_options,
    )
    DEVICE_TRACKING_ENABLED = True
except Exception as e:
    print(f"⚠️  Device activation disabled: {str(e)}")
    DEVICE_TRACKING_ENABLED = False

    def validate_activation_code():
        return True, 9999, "Activation module disabled"

    def save_user_activation_code(code_text):
        return False, "Activation module disabled"

    def register_device_on_startup(*args, **kwargs):
        return False

    def register_device_with_activation(*args, **kwargs):
        return False

    def check_activation_on_startup():
        return True

    def send_device_log(action, details=None):
        return False

    def get_activation_duration_options():
        return [
            {"key": "3d", "label": "3 Ngày", "icon": "📅"},
            {"key": "7d", "label": "1 Tuần", "icon": "📈"},
            {"key": "10d", "label": "10 Ngày", "icon": "📈"},
            {"key": "1m", "label": "1 Tháng", "icon": "📊"},
            {"key": "2m", "label": "2 Tháng", "icon": "📊"},
            {"key": "3m", "label": "3 Tháng", "icon": "📊"},
            {"key": "6m", "label": "6 Tháng", "icon": "📊"},
            {"key": "lifetime", "label": "Vĩnh viễn", "icon": "💎"},
        ]

# ─── Global log queue for SSE streaming ───────────────────────────────────────
_log_queue = queue.Queue()
_sse_clients = []

def sse_broadcast(msg, msg_type="info"):
    """Broadcast message to all SSE clients."""
    payload = json.dumps({"msg": msg, "type": msg_type}, ensure_ascii=False)
    for client_queue in _sse_clients[:]:
        try:
            client_queue.put_nowait(payload)
        except:
            _sse_clients.remove(client_queue)


# Setup task manager to use SSE broadcast
set_sse_broadcast_func(sse_broadcast)

# ─── Ensure data directories exist on startup ─────────────────────────────────
_ensure_dirs()

# ─── Activation Gate ──────────────────────────────────────────────────────────
SCHEDULE_WORKER_STARTED = False
SCHEDULE_WORKER_LOCK = threading.Lock()


def ensure_schedule_worker_started():
    """Start schedule worker once, only after activation is valid."""
    global SCHEDULE_WORKER_STARTED
    with SCHEDULE_WORKER_LOCK:
        if SCHEDULE_WORKER_STARTED:
            return False
        start_schedule_worker()
        SCHEDULE_WORKER_STARTED = True
        print("✅ Schedule worker đã khởi động", flush=True)
        return True


def _activation_status_payload():
    try:
        is_valid, days_remaining, message = validate_activation_code()
    except Exception as e:
        is_valid, days_remaining, message = False, 0, f"Lỗi kiểm tra kích hoạt: {e}"
    return {
        "success": True,
        "activated": bool(is_valid),
        "days_remaining": int(days_remaining or 0),
        "message": message,
    }


@app.before_request
def require_activation_before_use():
    """Block normal pages/APIs until a valid activation code is saved."""
    if not DEVICE_TRACKING_ENABLED:
        return None

    path = request.path or ""
    endpoint = request.endpoint or ""
    allowed_endpoints = {
        "activation_page",
        "api_activation_status",
        "api_activation_save",
        "api_activation_resend",
        "api_device_register",
        "api_device_log",
        "static",
    }

    if endpoint in allowed_endpoints or path.startswith("/static/") or path.startswith("/api/activation/"):
        return None

    status = _activation_status_payload()
    if status.get("activated"):
        return None

    if path.startswith("/api/") or request.accept_mimetypes.best == "application/json":
        return jsonify({
            "success": False,
            "activation_required": True,
            "error": "Phần mềm chưa được kích hoạt",
            "message": status.get("message", "Vui lòng nhập mã kích hoạt."),
        }), 403

    return redirect(url_for("activation_page"))


@app.route("/activation", methods=["GET"])
def activation_page():
    status = _activation_status_payload()
    return render_template(
        "activation.html",
        active_page="activation",
        activated=status.get("activated"),
        days_remaining=status.get("days_remaining", 0),
        activation_message=status.get("message", ""),
        activation_options=get_activation_duration_options(),
    )


@app.route("/api/activation/status", methods=["GET"])
def api_activation_status():
    return jsonify(_activation_status_payload())


@app.route("/api/activation/save", methods=["POST"])
def api_activation_save():
    data = request.get_json(silent=True) or request.form or {}
    code_text = (data.get("code") or data.get("activation_code") or "").strip()
    ok, message = save_user_activation_code(code_text)
    status = _activation_status_payload()
    if ok and status.get("activated"):
        ensure_schedule_worker_started()
    return jsonify({
        "success": bool(ok),
        "message": message,
        "activated": bool(status.get("activated")),
        "days_remaining": status.get("days_remaining", 0),
        "status_message": status.get("message", ""),
    }), 200 if ok else 400


@app.route("/api/activation/resend", methods=["POST"])
def api_activation_resend():
    try:
        data = request.get_json(silent=True) or request.form or {}
        duration_key = (data.get("duration_key") or data.get("plan") or "1m").strip()
        options_map = {item["key"]: item for item in get_activation_duration_options()}
        selected = options_map.get(duration_key, options_map.get("1m", {"key": duration_key, "label": duration_key}))
        sent = register_device_with_activation(verbose=True, duration_key=duration_key, force=True)
        status = _activation_status_payload()
        if not sent:
            return jsonify({
                "success": False,
                "sent": False,
                "activated": bool(status.get("activated")),
                "selected_duration": selected,
                "message": (
                    "Không gửi được mã kích hoạt. Kiểm tra cấu hình email trong .env: "
                    "SENDMAIL_USER phải đúng Gmail gửi, SENDMAIL_PASS phải là App Password 16 ký tự "
                    "chứ không phải mật khẩu Gmail thường. Nếu App Password có dấu cách, hệ thống đã tự bỏ khoảng trắng khi đăng nhập SMTP."
                ),
                "status_message": status.get("message", ""),
            }), 400
        return jsonify({
            "success": True,
            "sent": True,
            "activated": bool(status.get("activated")),
            "selected_duration": selected,
            "message": (
                f"Đã gửi mã kích hoạt 12 ký tự cho gói {selected.get('label', duration_key)}. "
                "Lưu ý: mã mới sẽ làm mã cũ hết hiệu lực."
            ),
            "status_message": status.get("message", ""),
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/")
def index():
    from flask import redirect
    return redirect("/guide")


@app.route("/members")
def members_page():
    return render_template("members.html", active_page="members")


@app.route("/get_members")
def get_members_page():
    return render_template("members.html", active_page="members")


@app.route("/accounts")
def accounts_page():
    return render_template("accounts.html", active_page="accounts")


@app.route("/guide")
def guide_page():
    return render_template("guide.html", active_page="guide")


@app.route("/messages")
def messages_page():
    return render_template("messages.html", active_page="messages")


@app.route("/groups")
def groups_page():
    return render_template("groups.html", active_page="groups")


@app.route("/schedules")
def schedules_page():
    return render_template("schedules.html", active_page="marketing_group", initial_tab="group", page_title="Chiến dịch theo nhóm")


@app.route("/marketing/group")
def marketing_group_page():
    return render_template("schedules.html", active_page="marketing_group", initial_tab="group", page_title="Chiến dịch theo nhóm")


@app.route("/marketing/phone")
def marketing_phone_page():
    return render_template("schedules.html", active_page="marketing_phone", initial_tab="phone", page_title="Chiến dịch theo SĐT")


@app.route("/marketing/personal-groups")
def marketing_personal_groups_page():
    return render_template("schedules.html", active_page="personal_groups", initial_tab="personal-groups", page_title="Nhóm cá nhân")


@app.route("/marketing/schedules")
def marketing_schedules_page():
    return render_template("schedules.html", active_page="schedules", initial_tab="schedules-list", page_title="Quản lý lịch chạy")



@app.route("/api/messages/conversations", methods=["GET"])
def api_messages_list_conversations():
    """API: danh sách hội thoại đã lưu."""
    try:
        account_id = (request.args.get("account_id") or "").strip() or None
        status = (request.args.get("status") or "all").strip() or "all"
        q = (request.args.get("q") or "").strip() or None
        conversations = list_message_conversations(account_id=account_id, status=status, q=q)
        stats = get_message_stats(account_id=account_id)
        return jsonify({"success": True, "conversations": conversations, "stats": stats})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/messages/conversations", methods=["POST"])
def api_messages_create_conversation():
    """API: tạo hội thoại thủ công để test UI hoặc import dữ liệu sau này."""
    try:
        data = request.get_json(silent=True) or request.form or {}
        conversation = create_message_conversation(
            account_id=(data.get("account_id") or "").strip(),
            peer_id=(data.get("peer_id") or data.get("uid") or "").strip(),
            peer_name=(data.get("peer_name") or data.get("name") or "").strip(),
            peer_avatar=(data.get("peer_avatar") or data.get("avatar") or "").strip(),
            source=(data.get("source") or "manual").strip(),
            first_message=(data.get("first_message") or data.get("message") or "").strip(),
            direction=(data.get("direction") or "in").strip(),
        )
        return jsonify({"success": True, "conversation": conversation})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400


@app.route("/api/messages/conversations/<conversation_id>", methods=["GET", "PATCH"])
def api_messages_conversation_detail(conversation_id):
    """API: xem/cập nhật hội thoại."""
    try:
        if request.method == "GET":
            conversation = get_message_conversation(conversation_id)
            if not conversation:
                return jsonify({"success": False, "error": "Không tìm thấy hội thoại"}), 404
            return jsonify({"success": True, "conversation": conversation})

        data = request.get_json(silent=True) or request.form or {}
        conversation = update_message_conversation(conversation_id, dict(data))
        return jsonify({"success": True, "conversation": conversation})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400


@app.route("/api/messages/conversations/<conversation_id>/messages", methods=["POST"])
def api_messages_add_message(conversation_id):
    """API: lưu thêm tin nhắn vào hội thoại. Hiện chỉ lưu lịch sử, chưa tự gửi Zalo."""
    try:
        data = request.get_json(silent=True) or request.form or {}
        conversation = add_conversation_message(
            conversation_id,
            text=(data.get("text") or data.get("message") or "").strip(),
            direction=(data.get("direction") or "in").strip(),
            raw=data.get("raw") if isinstance(data.get("raw"), dict) else {},
        )
        return jsonify({"success": True, "conversation": conversation})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400


@app.route("/api/messages/conversations/<conversation_id>/read", methods=["POST"])
def api_messages_mark_read(conversation_id):
    """API: đánh dấu hội thoại đã đọc."""
    try:
        conversation = mark_message_read(conversation_id)
        return jsonify({"success": True, "conversation": conversation})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400


@app.route("/api/messages/settings", methods=["GET", "PATCH"])
def api_messages_settings():
    """API: thiết lập khung tự động kiểm tra/phản hồi tin nhắn."""
    try:
        if request.method == "GET":
            return jsonify({"success": True, "settings": get_message_settings()})
        data = request.get_json(silent=True) or request.form or {}
        settings = update_message_settings(dict(data))
        return jsonify({"success": True, "settings": settings})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400


@app.route("/api/messages/check", methods=["POST"])
def api_messages_check():
    """API placeholder: ghi nhận yêu cầu check tin nhắn để sau này nối API Zalo."""
    try:
        data = request.get_json(silent=True) or request.form or {}
        account_id = (data.get("account_id") or "").strip()
        message = "Đã ghi nhận yêu cầu kiểm tra tin nhắn. Bản vá này mới tạo khung UI/lưu trữ, chưa nối API đồng bộ tin nhắn Zalo thật."
        settings = log_message_check_attempt(account_id=account_id, message=message)
        return jsonify({"success": True, "synced_count": 0, "settings": settings, "message": message})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/accounts", methods=["GET"])
def api_list_accounts():
    """API: Danh sách tài khoản Zalo."""
    try:
        accounts = load_accounts()
        return jsonify({"success": True, "accounts": accounts})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/accounts", methods=["POST"])
def api_create_account():
    """API: Thêm tài khoản mới + mở Chrome."""
    try:
        account = create_account()
        return jsonify({"success": True, "account": account})
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/accounts/<account_id>/open", methods=["POST"])
def api_open_account(account_id):
    """API: Mở Chrome với profile tài khoản."""
    try:
        account = open_account(account_id)
        return jsonify({"success": True, "account": account})
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/accounts/<account_id>/close", methods=["POST"])
def api_close_account(account_id):
    """API: Đóng Chrome profile của tài khoản."""
    try:
        from features.accounts.account_manager import close_account
        close_account(account_id)
        return jsonify({"success": True})
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/accounts/<account_id>", methods=["PATCH"])
def api_update_account(account_id):
    """API: Cập nhật tài khoản: name/proxy."""
    data = request.get_json(silent=True) or {}

    try:
        # Đổi tên tài khoản
        if "name" in data:
            name = str(data.get("name") or "").strip()
            if not name:
                return jsonify({"error": "Tên không được để trống."}), 400
            account = rename_account(account_id, name)
            return jsonify({"success": True, "account": account})

        # Cập nhật proxy
        if "proxy" in data:
            proxy = str(data.get("proxy") or "").strip()
            account = update_account(account_id, proxy=proxy)
            if not account:
                return jsonify({"error": "Không tìm thấy tài khoản."}), 404
            return jsonify({"success": True, "account": account})

        return jsonify({"error": "Không có dữ liệu hợp lệ để cập nhật."}), 400

    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/accounts/<account_id>", methods=["DELETE"])
def api_delete_account(account_id):
    """API: Xóa tài khoản và thư mục profile."""
    try:
        delete_account(account_id)
        return jsonify({"success": True, "message": "Đã xóa tài khoản."})
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 409
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500





@app.route("/api/log-stream")
def log_stream():
    """SSE endpoint: stream log messages to UI in real-time."""
    client_q = queue.Queue()
    _sse_clients.append(client_q)

    def event_stream():
        while True:
            try:
                msg = client_q.get(timeout=60)
                yield f"data: {msg}\n\n"
            except queue.Empty:
                yield f"data: {json.dumps({'msg':'','type':'heartbeat'}, ensure_ascii=False)}\n\n"

    resp = Response(event_stream(), mimetype='text/event-stream')
    resp.headers['Cache-Control'] = 'no-cache'
    resp.headers['X-Accel-Buffering'] = 'no'
    return resp




def _get_account_credentials(account_id: str, require_imei: bool = False):
    """
    Lấy cookies / zpwEnk / imei theo account đang chọn.
    """
    account_id = (account_id or "").strip()
    if not account_id:
        raise ValueError("Chưa chọn tài khoản.")

    account = get_account(account_id)
    if not account:
        raise ValueError("Không tìm thấy tài khoản.")

    cookies = (account.get("cookies") or "").strip()
    zpw_enk = (account.get("zpwEnk") or "").strip()
    imei = (account.get("imei") or "").strip()

    missing = []
    if not cookies:
        missing.append("cookies")
    if not zpw_enk:
        missing.append("zpwEnk")
    if require_imei and not imei:
        missing.append("imei")

    if missing:
        raise ValueError(
            "Tài khoản chưa đủ dữ liệu: " + ", ".join(missing) +
            ". Hãy mở tài khoản để monitor lấy phiên, hoặc bổ sung IMEI cho account."
        )

    return account, cookies, zpw_enk, imei




@app.route("/run", methods=["POST"])
def run():
    account_id = request.form.get("account_id", "").strip()

    raw = request.form.get("group_link", "").strip()
    if not raw:
        raw = request.form.get("group_id", "").strip()

    # Tự động fallback: nếu account chưa là thành viên và input là link nhóm,
    # hệ thống sẽ tự join ngầm, lấy thành viên, rồi rời nhóm. Không cần checkbox UI.
    auto_join_when_not_member = True

    if not raw:
        return jsonify({"error": "Thiếu Group Link hoặc Group ID!"}), 400

    try:
        account, cookies, zpw_enk, imei = _get_account_credentials(account_id, require_imei=False)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    try:
        zpw_ver = get_zpw_ver()
        member_payload = fetch_group_members_by_input(
            raw,
            zpw_enk,
            cookies,
            callback=sse_broadcast,
            zpw_ver=zpw_ver,
            imei=imei,
            auto_join_when_not_member=auto_join_when_not_member,
            leave_after_auto_join=False,
        )

        uid_list = member_payload["uidList"]
        group_info = member_payload["groupInfo"]
        group_id = member_payload["groupId"]
        member_map = member_payload.get("memberMap") or {}

        sse_broadcast(f"Lấy được {len(uid_list)} UID. Đang lấy thông tin profile...", "loading")
        profiles = fetch_profiles_with_single_fallback(
            uid_list,
            zpw_enk,
            cookies,
            imei=imei,
            log_func=sse_broadcast,
            zpw_ver=zpw_ver,
        )
        auto_joined = bool(member_payload.get("autoJoined"))
        auto_left = False
        leave_result = None

        result = build_member_rows_from_uids(uid_list, profiles, member_map=member_map)

        # Nếu hệ thống tự join ngầm thì chỉ rời sau khi đã lấy xong UID + mini profile
        # và build xong bảng kết quả. Không gọi profile chi tiết hàng loạt trong /run.
        if auto_joined and uid_list:
            leave_result = _leave_group([group_id], imei=imei, zpw_enk=zpw_enk, cookies=cookies, zpw_ver=zpw_ver)
            auto_left = bool(isinstance(leave_result, dict) and leave_result.get("ok"))

        sse_broadcast(f"Hoàn thành! Tổng cộng {len(result)} thành viên.", "success")
        return jsonify({
            "success": True,
            "total": len(result),
            "data": result,
            "groupInfo": format_group_info(group_info, group_id=group_id, fallback_total=len(result)),
            "autoJoined": auto_joined,
            "autoLeft": auto_left,
            "leaveResult": leave_result,
        })

    except ValueError as e:
        sse_broadcast(str(e), "error")
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        import traceback
        traceback.print_exc()
        sse_broadcast(f"Lỗi: {str(e)}", "error")
        return jsonify({"error": str(e)}), 500



@app.route("/api/get-avatar", methods=["POST"])
def api_get_avatar():
    """
    API: Lấy avatar full size từ Zalo.
    Gọi features/profiles/get_avata.py -> get_avatar()
    Trả về bk_full_avatar để frontend hiển thị khi click avatar.
    """
    data = request.get_json(silent=True) or {}

    account_id = str(
        data.get("accountId")
        or data.get("account_id")
        or ""
    ).strip()

    fid = str(
        data.get("fid")
        or data.get("uid")
        or data.get("userId")
        or data.get("toid")
        or ""
    ).strip()

    # Fix nếu UI đang hiển thị dạng "ID: 439..."
    if fid.lower().startswith("id:"):
        fid = fid.split(":", 1)[1].strip()

    import re
    m = re.search(r"\d{8,}", fid)
    if m:
        fid = m.group(0)

    if not account_id:
        return jsonify({"success": False, "error": "Chưa chọn tài khoản."}), 400

    if not fid:
        return jsonify({"success": False, "error": "Thiếu fid/userId."}), 400

    try:
        from features.profiles.get_avata import get_avatar

        account, cookies, zpw_enk, imei = _get_account_credentials(account_id, require_imei=True)

        print("[api_get_avatar] call features.profiles.get_avata.get_avatar")
        print("[api_get_avatar] account_id=", account_id)
        print("[api_get_avatar] fid=", fid)
        print("[api_get_avatar] has_cookies=", bool(cookies))
        print("[api_get_avatar] has_zpw_enk=", bool(zpw_enk))
        print("[api_get_avatar] imei=", imei)

        response_json, decoded_data, avatar_data = get_avatar(
            fid=fid,
            imei=imei,
            zpw_enk=zpw_enk,
            cookies=cookies,
            proxy=None,
            zpw_ver=get_zpw_ver()
        )

        bk_full_avatar = ""
        full_avatar = ""

        if isinstance(avatar_data, dict):
            bk_full_avatar = avatar_data.get("bk_full_avatar") or ""
            full_avatar = avatar_data.get("full_avatar") or ""

        avatar_url = bk_full_avatar or full_avatar

        print("[api_get_avatar] bk_full_avatar=", bk_full_avatar)
        print("[api_get_avatar] full_avatar=", full_avatar)

        if not avatar_url:
            return jsonify({
                "success": False,
                "error": "Không lấy được bk_full_avatar.",
                "response": response_json,
                "decoded": decoded_data,
                "avatar_data": avatar_data
            }), 400

        return jsonify({
            "success": True,
            "fid": fid,
            "bk_full_avatar": bk_full_avatar,
            "full_avatar": full_avatar,
            "avatar_url": avatar_url,
            "data": avatar_data,
            "decoded": decoded_data
        })

    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500



@app.route("/api/single-profile", methods=["POST"])
def api_single_profile():
    """API: Lấy profile đầy đủ của 1 UID hoặc nhiều UID qua getprofiles/v2."""
    data = request.get_json(silent=True) or {}
    if not data:
        return jsonify({"error": "No data provided"}), 400

    account_id = str(data.get("accountId", data.get("account_id", "")) or "").strip()
    uid = str(data.get("uid", "") or "").strip()
    uids = data.get("uids") or data.get("uid_list") or []

    # Cho phép frontend gửi {uid: "..."} hoặc {uids: ["...", "..."]}
    if uid:
        uid_input = uid
        is_batch = False
    else:
        if isinstance(uids, str):
            uids = [x.strip() for x in uids.split(",") if x.strip()]
        elif isinstance(uids, (list, tuple)):
            uids = [str(x).strip() for x in uids if str(x).strip()]
        else:
            uids = []

        if not uids:
            return jsonify({"error": "Thiếu UID hoặc danh sách UIDs!"}), 400

        uid_input = uids
        is_batch = True

    try:
        _, cookies, zpw_enk, imei = _get_account_credentials(account_id, require_imei=True)
        result = get_single_profile(uid_input, zpw_enk, cookies, imei=imei, zpw_ver=get_zpw_ver(), raise_on_rate_limit=True)

        if is_batch:
            # result dạng {uid: profile hoặc None}
            profiles = result if isinstance(result, dict) else {}
            profiles = {str(k): v for k, v in profiles.items() if v}
            return jsonify({
                "success": True,
                "profiles": profiles,
                "total": len(profiles)
            })

        if not result:
            return jsonify({"error": "Không lấy được profile của UID này."}), 400
        return jsonify({"success": True, "profile": result})
    except ProfileRateLimitError as e:
        return jsonify({"error": str(e) or "Zalo đang giới hạn request profile. Vui lòng chờ rồi thử lại.", "error_code": getattr(e, "code", 221)}), 429
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route("/api/friend-request-plans", methods=["GET"])
def api_list_friend_request_plans():
    """API: danh sách kế hoạch kết bạn/mời nhóm đã lưu."""
    try:
        account_id = str(request.args.get("accountId") or request.args.get("account_id") or "").strip()
        status = str(request.args.get("status") or "").strip()
        plans = _load_friend_plans()
        if account_id:
            plans = [p for p in plans if str(p.get("accountId") or "") == account_id]
        plans = _attach_action_plans_progress(plans)
        if status:
            plans = [p for p in plans if str(p.get("status") or "") == status or str((p.get("progress") or {}).get("displayStatus") or "") == status]
        plans = sorted(plans, key=lambda x: int(x.get("createdAt") or 0), reverse=True)
        return jsonify({"success": True, "plans": plans, "total": len(plans)})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e), "plans": []}), 500


@app.route("/api/friend-request-plans", methods=["POST"])
def api_create_friend_request_plan():
    """
    API: tạo kế hoạch chia batch theo ngày cho danh sách người được chọn.
    Lưu ý: route này chỉ lưu kế hoạch pending_manual, không tự động gửi lời mời/kéo người vào nhóm.
    """
    data = request.get_json(silent=True) or {}

    account_id = str(data.get("accountId") or data.get("account_id") or "").strip()
    message = str(data.get("message") or data.get("msg") or "").strip()
    plan_name = str(data.get("name") or data.get("planName") or "Kế hoạch kết bạn").strip()
    start_date = str(data.get("startDate") or "").strip() or datetime.now().date().isoformat()
    after_action = str(data.get("afterAction") or "none").strip()
    target_group_ids = data.get("targetGroupIds") or data.get("groupIds") or []
    target_groups = data.get("targetGroups") or []
    new_group_name = str(data.get("newGroupName") or "").strip()
    confirm_consent = bool(data.get("confirmConsent") or data.get("consentConfirmed"))

    try:
        daily_limit = int(data.get("dailyLimit") or data.get("perDay") or 1)
    except Exception:
        daily_limit = 1
    daily_limit = max(1, min(daily_limit, 500))

    if isinstance(target_group_ids, str):
        target_group_ids = [target_group_ids]
    target_group_ids = [str(x).strip() for x in target_group_ids if str(x).strip()]

    members_raw = data.get("members") or data.get("selectedMembers") or data.get("userIds") or data.get("uids") or []
    if isinstance(members_raw, str):
        members_raw = [x.strip() for x in members_raw.split(",") if x.strip()]

    seen = set()
    members = []
    for item in members_raw if isinstance(members_raw, list) else []:
        m = _normalize_plan_member(item)
        if not m:
            continue
        uid = m["userId"]
        if uid in seen:
            continue
        seen.add(uid)
        members.append(m)

    if not account_id:
        return jsonify({"success": False, "error": "Chưa chọn tài khoản lập lịch."}), 400
    if not get_account(account_id):
        return jsonify({"success": False, "error": "Không tìm thấy tài khoản."}), 404
    if not members:
        return jsonify({"success": False, "error": "Danh sách người được chọn đang trống."}), 400
    if not message:
        return jsonify({"success": False, "error": "Vui lòng nhập nội dung lời mời kết bạn."}), 400
    if not confirm_consent:
        return jsonify({
            "success": False,
            "error": "Cần xác nhận danh sách người nhận hợp lệ/được phép liên hệ trước khi lưu kế hoạch."
        }), 400

    if after_action not in ("none", "invite_existing_group", "create_new_group"):
        after_action = "none"
    if after_action == "invite_existing_group" and not target_group_ids:
        return jsonify({"success": False, "error": "Vui lòng chọn nhóm đích."}), 400
    if after_action == "create_new_group" and not new_group_name:
        return jsonify({"success": False, "error": "Vui lòng nhập tên nhóm mới."}), 400

    batches = _build_friend_plan_batches(members, daily_limit, start_date)
    now = _friend_now_ms()
    plan = {
        "id": uuid.uuid4().hex,
        "name": plan_name,
        "status": "planned",
        "mode": "manual_review",
        "accountId": account_id,
        "accountName": _find_account_name(account_id),
        "message": message,
        "dailyLimit": daily_limit,
        "startDate": start_date,
        "createdAt": now,
        "updatedAt": now,
        "totalMembers": len(members),
        "afterAction": after_action,
        "targetGroupIds": target_group_ids,
        "targetGroups": target_groups if isinstance(target_groups, list) else [],
        "newGroupName": new_group_name,
        "consentConfirmed": True,
        "members": members,
        "batches": batches,
        "note": "Kế hoạch chỉ chia lịch và chờ xác nhận thủ công từng batch; hệ thống không tự động gửi hàng loạt.",
    }

    _attach_action_plan_progress(plan)
    plans = _load_friend_plans()
    plans.append(plan)
    _save_friend_plans(plans)

    return jsonify({
        "success": True,
        "message": f"Đã lưu kế hoạch {len(members)} người, {daily_limit} người/ngày.",
        "plan": plan,
    })


@app.route("/api/group-invite-plans", methods=["GET"])
def api_list_group_invite_plans():
    """API: danh sách kế hoạch mời vào nhóm đã lưu."""
    try:
        account_id = str(request.args.get("accountId") or request.args.get("account_id") or "").strip()
        status = str(request.args.get("status") or "").strip()
        plans = _load_group_invite_plans()
        if account_id:
            plans = [p for p in plans if str(p.get("accountId") or "") == account_id]
        plans = _attach_action_plans_progress(plans)
        if status:
            plans = [p for p in plans if str(p.get("status") or "") == status or str((p.get("progress") or {}).get("displayStatus") or "") == status]
        plans = sorted(plans, key=lambda x: int(x.get("createdAt") or 0), reverse=True)
        return jsonify({"success": True, "plans": plans, "total": len(plans)})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e), "plans": []}), 500


@app.route("/api/group-invite-plans", methods=["POST"])
def api_create_group_invite_plan():
    """
    API: tạo kế hoạch chia batch theo ngày để mời thành viên vào nhóm đã chọn.
    Lưu ý: route này chỉ lưu kế hoạch pending_manual, không tự động mời hàng loạt.
    """
    data = request.get_json(silent=True) or {}

    account_id = str(data.get("accountId") or data.get("account_id") or "").strip()
    plan_name = str(data.get("name") or data.get("planName") or "Kế hoạch mời vào nhóm").strip()
    start_date = str(data.get("startDate") or "").strip() or datetime.now().date().isoformat()
    confirm_consent = bool(data.get("confirmConsent") or data.get("consentConfirmed"))

    try:
        daily_limit = int(data.get("dailyLimit") or data.get("perDay") or 1)
    except Exception:
        daily_limit = 1
    daily_limit = max(1, min(daily_limit, 500))

    members_raw = data.get("members") or data.get("selectedMembers") or data.get("userIds") or data.get("uids") or []
    if isinstance(members_raw, str):
        members_raw = [x.strip() for x in members_raw.split(",") if x.strip()]

    seen_members = set()
    members = []
    for item in members_raw if isinstance(members_raw, list) else []:
        m = _normalize_plan_member(item)
        if not m:
            continue
        uid = m["userId"]
        if uid in seen_members:
            continue
        seen_members.add(uid)
        members.append(m)

    target_group_ids = data.get("targetGroupIds") or data.get("groupIds") or []
    if isinstance(target_group_ids, str):
        target_group_ids = [target_group_ids]
    target_group_ids = [str(x).strip() for x in target_group_ids if str(x).strip()]

    groups_raw = data.get("targetGroups") or data.get("groups") or []
    groups = []
    seen_groups = set()
    for item in groups_raw if isinstance(groups_raw, list) else []:
        g = _normalize_plan_group(item)
        if not g:
            continue
        gid = g["groupId"]
        if gid in seen_groups:
            continue
        seen_groups.add(gid)
        groups.append(g)

    # Nếu frontend chỉ gửi ID thì vẫn lưu được metadata tối thiểu. Đúng là máy móc cũng cần được nhắc cách thở.
    for gid in target_group_ids:
        if gid not in seen_groups:
            groups.append({"groupId": gid, "name": gid, "avatar": "", "memberCount": 0})
            seen_groups.add(gid)

    if not account_id:
        return jsonify({"success": False, "error": "Chưa chọn tài khoản lập lịch."}), 400
    if not get_account(account_id):
        return jsonify({"success": False, "error": "Không tìm thấy tài khoản."}), 404
    if not members:
        return jsonify({"success": False, "error": "Danh sách người cần mời đang trống."}), 400
    if not target_group_ids:
        return jsonify({"success": False, "error": "Vui lòng chọn ít nhất 1 nhóm đích."}), 400
    if not confirm_consent:
        return jsonify({
            "success": False,
            "error": "Cần xác nhận danh sách người nhận hợp lệ/được phép mời trước khi lưu kế hoạch."
        }), 400

    batches = _build_friend_plan_batches(members, daily_limit, start_date)
    now = _friend_now_ms()
    plan = {
        "id": uuid.uuid4().hex,
        "name": plan_name,
        "status": "planned",
        "mode": "manual_review",
        "accountId": account_id,
        "accountName": _find_account_name(account_id),
        "dailyLimit": daily_limit,
        "startDate": start_date,
        "createdAt": now,
        "updatedAt": now,
        "totalMembers": len(members),
        "targetGroupIds": target_group_ids,
        "targetGroups": groups,
        "consentConfirmed": True,
        "members": members,
        "batches": batches,
        "note": "Kế hoạch chỉ chia lịch và chờ xác nhận thủ công từng batch; hệ thống không tự động mời hàng loạt.",
    }

    _attach_action_plan_progress(plan)
    plans = _load_group_invite_plans()
    plans.append(plan)
    _save_group_invite_plans(plans)

    return jsonify({
        "success": True,
        "message": f"Đã lưu kế hoạch mời {len(members)} người vào {len(target_group_ids)} nhóm, {daily_limit} người/ngày.",
        "plan": plan,
    })


@app.route("/api/friend-request-plans/<plan_id>/batches/<int:batch_day>", methods=["PATCH", "POST"])
def api_update_friend_request_plan_batch(plan_id, batch_day):
    """Cập nhật tiến độ một batch trong kế hoạch gửi kết bạn."""
    data = request.get_json(silent=True) or {}
    try:
        plan, batch = _update_action_plan_batch_status("friend", plan_id, batch_day, data)
        return jsonify({
            "success": True,
            "message": "Đã cập nhật tiến độ batch kết bạn.",
            "plan": plan,
            "batch": batch,
        })
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/group-invite-plans/<plan_id>/batches/<int:batch_day>", methods=["PATCH", "POST"])
def api_update_group_invite_plan_batch(plan_id, batch_day):
    """Cập nhật tiến độ một batch trong kế hoạch mời vào nhóm."""
    data = request.get_json(silent=True) or {}
    try:
        plan, batch = _update_action_plan_batch_status("group_invite", plan_id, batch_day, data)
        return jsonify({
            "success": True,
            "message": "Đã cập nhật tiến độ batch mời vào nhóm.",
            "plan": plan,
            "batch": batch,
        })
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/action-plans", methods=["GET"])
def api_list_action_plans():
    """API tổng hợp để UI xem lịch kết bạn và lịch mời vào nhóm trong một màn."""
    try:
        account_id = str(request.args.get("accountId") or request.args.get("account_id") or "").strip()
        plan_type = str(request.args.get("type") or "all").strip()

        items = []
        if plan_type in ("all", "friend", "friend_request"):
            friend_plans = _attach_action_plans_progress(_load_friend_plans())
            for p in friend_plans:
                p = dict(p)
                p["planType"] = "friend"
                p["planTypeLabel"] = "Gửi kết bạn"
                items.append(p)

        if plan_type in ("all", "group", "group_invite"):
            group_plans = _attach_action_plans_progress(_load_group_invite_plans())
            for p in group_plans:
                p = dict(p)
                p["planType"] = "group_invite"
                p["planTypeLabel"] = "Mời vào nhóm"
                items.append(p)

        if account_id:
            items = [p for p in items if str(p.get("accountId") or "") == account_id]

        items = sorted(items, key=lambda x: int(x.get("createdAt") or 0), reverse=True)
        return jsonify({"success": True, "plans": items, "total": len(items)})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e), "plans": []}), 500

@app.route("/api/send-friend-request", methods=["POST"])
def api_send_friend_request():
    """
    API: Gửi lời mời kết bạn.
    Route này lấy account credentials rồi gọi trực tiếp features/messaging/add_friend.py -> send_friend_request().
    """
    data = request.get_json(silent=True) or {}

    account_id = str(data.get("accountId", data.get("account_id", "")) or "").strip()
    toid = str(
        data.get("toid",
        data.get("toId",
        data.get("uid",
        data.get("userId",
        data.get("to_uid", "")))))
        or ""
    ).strip()
    message = str(data.get("message", data.get("msg", "")) or "").strip()

    if not account_id:
        return jsonify({"error": "Chưa chọn tài khoản gửi kết bạn."}), 400

    if not toid:
        return jsonify({"error": "Thiếu ID người nhận kết bạn."}), 400

    if not message:
        message = "Xin chào, mình muốn kết bạn với bạn."

    try:
        account, cookies, zpw_enk, imei = _get_account_credentials(account_id, require_imei=True)

        proxy = None  # Không dùng proxy khi gửi kết bạn

        print("[api_send_friend_request] call features.messaging.add_friend.send_friend_request")
        print("[api_send_friend_request] account_id=", account_id)
        print("[api_send_friend_request] toid=", toid)
        print("[api_send_friend_request] has_cookies=", bool(cookies))
        print("[api_send_friend_request] has_zpw_enk=", bool(zpw_enk))
        print("[api_send_friend_request] imei=", imei)
        print("[api_send_friend_request] proxy= DISABLED")

        response_json, decoded_data = send_friend_request(
            toid=toid,
            msg=message,
            imei=imei,
            zpw_enk=zpw_enk,
            cookies=cookies,
            proxy=None,
            zpw_ver=get_zpw_ver()
        )

        print("[api_send_friend_request] response_json=", response_json)
        print("[api_send_friend_request] decoded_data=", decoded_data)

        error_code = None
        error_message = ""

        if isinstance(decoded_data, dict):
            error_code = decoded_data.get("error_code", decoded_data.get("errorCode"))
            error_message = str(decoded_data.get("error_message", decoded_data.get("errorMessage", decoded_data.get("message", ""))) or "")
        elif isinstance(response_json, dict):
            error_code = response_json.get("error_code", response_json.get("errorCode"))
            error_message = str(response_json.get("error_message", response_json.get("errorMessage", response_json.get("message", ""))) or "")

        # Chỉ coi là thành công khi Zalo trả error_code = 0 rõ ràng.
        if str(error_code) == "0":
            return jsonify({
                "success": True,
                "message": "Zalo đã nhận request gửi kết bạn.",
                "data": decoded_data,
                "raw": response_json
            })

        return jsonify({
            "success": False,
            "error": "Zalo không xác nhận gửi kết bạn thành công.",
            "code": error_code,
            "message": error_message,
            "detail": decoded_data,
            "raw": response_json
        }), 400

    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/send-sms", methods=["POST"])
def api_send_sms():
    """API: Gui tin nhan Zalo toi user ID theo account duoc chon."""
    data = request.get_json(silent=True) or {}

    account_id = str(data.get("accountId", data.get("account_id", "")) or "").strip()

    to_uid = str(
        data.get("to_uid",
        data.get("toid",
        data.get("toId",
        data.get("uid",
        data.get("userId", "")))))
        or ""
    ).strip()

    # Fix: frontend có thể gửi dạng "ID: 336411663026086078"
    # Zalo API chỉ nhận số UID, không nhận kèm chữ "ID:"
    if to_uid.lower().startswith("id:"):
        to_uid = to_uid.split(":", 1)[1].strip()

    # Lọc tiếp phòng trường hợp có text thừa
    import re
    m = re.search(r"\d{8,}", to_uid)
    if m:
        to_uid = m.group(0)

    message = str(data.get("message", data.get("msg", "")) or "").strip()

    print("[api_send_sms] data=", data)
    print("[api_send_sms] account_id=", account_id)
    print("[api_send_sms] to_uid=", to_uid)
    print("[api_send_sms] message=", message)

    if not account_id:
        return jsonify({"success": False, "error": "Chua chon tai khoan gui tin nhan."}), 400

    if not to_uid:
        return jsonify({"success": False, "error": "Thieu nguoi nhan."}), 400

    if not message:
        return jsonify({"success": False, "error": "Thieu noi dung tin nhan."}), 400

    try:
        _, cookies, zpw_enk, imei = _get_account_credentials(account_id, require_imei=True)

        print("[api_send_sms] has_cookies=", bool(cookies))
        print("[api_send_sms] has_zpw_enk=", bool(zpw_enk))
        print("[api_send_sms] imei=", imei)

        response_json, decoded_data = send_sms(cookies, zpw_enk, to_uid, imei, message, zpw_ver=get_zpw_ver())

        print("[api_send_sms] response_json=", response_json)
        print("[api_send_sms] decoded_data=", decoded_data)

        error_code = None
        error_message = ""

        if isinstance(decoded_data, dict):
            error_code = decoded_data.get("error_code", decoded_data.get("errorCode"))
            error_message = str(
                decoded_data.get(
                    "error_message",
                    decoded_data.get("errorMessage", decoded_data.get("message", ""))
                ) or ""
            )
        elif isinstance(response_json, dict):
            error_code = response_json.get("error_code", response_json.get("errorCode"))
            error_message = str(
                response_json.get(
                    "error_message",
                    response_json.get("errorMessage", response_json.get("message", ""))
                ) or ""
            )

        if str(error_code) == "0":
            return jsonify({
                "success": True,
                "message": "Da gui tin nhan.",
                "data": decoded_data,
                "raw": response_json
            })

        return jsonify({
            "success": False,
            "error": "Loi gui SMS tu Zalo.",
            "code": error_code,
            "message": error_message,
            "detail": decoded_data,
            "raw": response_json
        }), 400

    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/create-group", methods=["POST"])
def api_create_group():
    """API: Tạo nhóm mới từ danh sách thành viên theo account được chọn."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    account_id = data.get("accountId", data.get("account_id", "")).strip()
    name = data.get("name", "").strip()
    desc = data.get("desc", "").strip()
    user_ids = data.get("userIds", [])

    if not name:
        return jsonify({"error": "Tên nhóm không được để trống!"}), 400
    if not user_ids:
        return jsonify({"error": "Danh sách thành viên trống!"}), 400

    try:
        _, cookies, zpw_enk, imei = _get_account_credentials(account_id, require_imei=True)
        _, decoded = _create_group(name, user_ids, zpw_enk, cookies, imei, zpw_ver=get_zpw_ver())

        error_code = decoded.get("error_code", -1) if isinstance(decoded, dict) else -1
        if error_code == 0:
            group_id = decoded.get("data", {}).get("groupId", "")
            group_url = f"https://zalo.me/g/{group_id}" if group_id else ""
            return jsonify({
                "success": True,
                "groupId": group_id,
                "groupUrl": group_url,
                "message": f"Tạo nhóm thành công! Group ID: {group_id}"
            })

        return jsonify({
            "error": f"Lỗi tạo nhóm (code: {error_code})",
            "detail": decoded
        }), 400

    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500



@app.route("/api/userinfo", methods=["POST"])
def api_get_userinfo():
    """
    API: Lấy user info từ jr.chat.zalo.me theo account được chọn.

    Body:
    {
        "accountId": "...",
        "uid_list": []
    }
    """
    data = request.get_json() or {}

    account_id = data.get("accountId", data.get("account_id", "")).strip()
    uid_list = data.get("uid_list", [])

    try:
        account, cookies, _, _ = _get_account_credentials(account_id, require_imei=False)

        sse_broadcast("📡 Đang lấy user info...", "loading")

        if uid_list:
            result = get_userinfo_batch(cookies, uid_list)
            sse_broadcast(f"✅ Hoàn thành! Lấy được {len(result)} profiles.", "success")
            return jsonify({
                "success": True,
                "total": len(result),
                "data": result
            })

        userinfo = get_userinfo_quick(cookies)
        if userinfo:
            return jsonify({
                "success": True,
                "userinfo": userinfo
            })

        return jsonify({"error": "Không lấy được user info."}), 400

    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        import traceback
        traceback.print_exc()
        sse_broadcast(f"❌ Lỗi: {str(e)}", "error")
        return jsonify({"error": str(e)}), 500


@app.route("/api/userinfo/batch", methods=["POST"])
def api_get_userinfo_batch():
    """
    API: Batch lấy thông tin profile của nhiều UID.
    
    Body:
    {
        "cookies": "...",
        "uid_list": ["uid1", "uid2", ...],
        "batch_size": 20  // optional
    }
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400
    
    cookies = data.get("cookies", "").strip()
    uid_list = data.get("uid_list", [])
    batch_size = data.get("batch_size", 20)
    
    if not cookies or not uid_list:
        return jsonify({"error": "Thiếu Cookies hoặc UID list!"}), 400
    
    try:
        sse_broadcast(f"📡 Đang batch fetch {len(uid_list)} profiles...", "loading")
        
        profiles = get_userinfo_batch(cookies, uid_list, batch_size)
        
        sse_broadcast(f"✅ Hoàn thành! Lấy được {len(profiles)} profiles.", "success")
        return jsonify({
            "success": True,
            "total": len(profiles),
            "profiles": profiles
        })
    
    except Exception as e:
        import traceback
        traceback.print_exc()
        sse_broadcast(f"❌ Lỗi: {str(e)}", "error")
        return jsonify({"error": str(e)}), 500


# ─── SCHEDULE APIs ─────────────────────────────────────────────────────────


def _fetch_group_members_worker(task, account_id: str, group_input: str, auto_join_when_not_member: bool = True):
    """Worker lấy thành viên nhóm dùng chung service với /run để tránh trùng logic."""
    try:
        account = get_account(account_id)
        if not account:
            raise ValueError("Không tìm thấy tài khoản")

        cookies = account.get("cookies", "").strip()
        zpw_enk = account.get("zpwEnk", "").strip()
        imei = account.get("imei", "").strip()
        zpw_ver = get_zpw_ver()

        if not cookies or not zpw_enk:
            raise ValueError("Tài khoản chưa có cookies hoặc zpwEnk")

        def task_log(msg, typ="info"):
            task.log(msg)

        task.log(f"Đang xử lý: {group_input}")
        task.set_progress(10)

        member_payload = fetch_group_members_by_input(
            group_input,
            zpw_enk,
            cookies,
            callback=task_log,
            zpw_ver=zpw_ver,
            imei=imei,
            auto_join_when_not_member=auto_join_when_not_member,
            leave_after_auto_join=False,
        )
        uid_list = member_payload["uidList"]
        group_info = member_payload["groupInfo"]
        group_id = member_payload["groupId"]
        member_map = member_payload.get("memberMap") or {}

        task.set_progress(30)
        task.log(f"Lấy được {len(uid_list)} UID. Đang lấy profile...")
        profiles = fetch_profiles_with_single_fallback(
            uid_list,
            zpw_enk,
            cookies,
            imei=imei,
            log_func=task_log,
            zpw_ver=zpw_ver,
        )
        auto_joined = bool(member_payload.get("autoJoined"))
        auto_left = False
        leave_result = None

        task.set_progress(70)
        result = build_member_rows_from_uids(uid_list, profiles, member_map=member_map)

        # Nếu tự join ngầm thì rời sau khi đã có UID + mini profile + bảng kết quả.
        if auto_joined and uid_list:
            leave_result = _leave_group([group_id], imei=imei, zpw_enk=zpw_enk, cookies=cookies, zpw_ver=zpw_ver)
            auto_left = bool(isinstance(leave_result, dict) and leave_result.get("ok"))

        task.set_progress(90)
        task.log(f"Hoàn thành! Tổng {len(result)} thành viên")

        result_data = {
            "total": len(result),
            "data": result,
            "groupInfo": format_group_info(group_info, group_id=group_id, fallback_total=len(result)),
            "autoJoined": auto_joined,
            "autoLeft": auto_left,
            "leaveResult": leave_result,
        }

        task.set_completed(result_data)
        return result_data

    except Exception as e:
        import traceback
        error_msg = str(e)
        task.set_failed(error_msg)
        print(f"[_fetch_group_members_worker] Error: {error_msg}")
        print(traceback.format_exc())
        raise


@app.route("/api/schedules/group-members", methods=["POST"])
def api_schedules_group_members():
    """API: Lấy thành viên nhóm cho lập lịch gửi tin (chạy async, không block)."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    account_id = data.get("accountId", data.get("account_id", "")).strip()
    group_input = data.get("groupInput", data.get("group_id", data.get("group_link", ""))).strip()
    # Lập lịch cũng tự fallback ngầm giống /run.
    auto_join_when_not_member = True

    if not account_id or not group_input:
        return jsonify({"error": "Thiếu thông tin!"}), 400

    try:
        # Validate account exists and has credentials
        account, cookies, zpw_enk, imei = _get_account_credentials(account_id, require_imei=False)
        
        # Start background task
        task = run_task_in_background(
            _fetch_group_members_worker,
            f"Fetch members: {group_input[:30]}",
            f"Lấy danh sách thành viên từ: {group_input}",
            account_id,
            group_input,
            auto_join_when_not_member
        )
        
        # Return task info immediately
        return jsonify({
            "success": True,
            "taskId": task.task_id,
            "status": task.status,
            "message": f"Đang xử lý... Dùng /api/tasks/{task.task_id} để check progress"
        }), 202  # 202 Accepted
        
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/schedules", methods=["GET"])
def api_get_schedules():
    """API: Danh sách lịch gửi."""
    try:
        schedules = get_all_schedules()
        return jsonify({
            "success": True,
            "schedules": schedules
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/schedules", methods=["POST"])
def api_create_schedule_api():
    """API: Tạo lịch gửi mới."""
    data = request.get_json(silent=True) or {}
    if not data:
        return jsonify({"error": "No data provided"}), 400

    try:
        # Gắn thêm tên tài khoản gửi vào lịch để UI không hiện N/A
        account_id = str(data.get("accountId") or data.get("account_id") or "").strip()
        if account_id:
            try:
                accounts = load_accounts()
                if isinstance(accounts, dict):
                    iterable = accounts.values()
                else:
                    iterable = accounts

                for acc in iterable:
                    if not isinstance(acc, dict):
                        continue

                    aid = str(
                        acc.get("accountId")
                        or acc.get("id")
                        or acc.get("account_id")
                        or ""
                    ).strip()

                    if aid == account_id:
                        account_name = (
                            acc.get("name")
                            or acc.get("displayName")
                            or acc.get("zaloName")
                            or acc.get("phone")
                            or account_id[:8]
                        )
                        data["accountName"] = account_name
                        data["senderName"] = account_name
                        break
            except Exception as e:
                print("[api_create_schedule_api] attach accountName failed:", e)

        schedule = create_schedule(data)
        return jsonify({
            "success": True,
            "schedule": schedule
        })
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/schedules/<schedule_id>", methods=["GET", "PUT", "PATCH", "DELETE"])
def api_schedule_handler(schedule_id):
    """API: Xử lý lịch gửi (GET/PUT/PATCH/DELETE)."""
    try:
        if request.method == "GET":
            # Chi tiết lịch gửi
            schedule = get_schedule(schedule_id)
            if not schedule:
                return jsonify({"error": "Không tìm thấy lịch"}), 404
            return jsonify({
                "success": True,
                "schedule": schedule
            })
        
        elif request.method in ["PUT", "PATCH"]:
            # Cập nhật lịch gửi (PUT = full update, PATCH = partial update, both do same thing here)
            data = request.get_json()
            if not data:
                return jsonify({"error": "Dữ liệu không hợp lệ"}), 400
            
            schedule = get_schedule(schedule_id)
            if not schedule:
                return jsonify({"error": "Không tìm thấy lịch"}), 404
            
            # Update fields
            if "title" in data:
                schedule["title"] = data["title"]
            if "message" in data:
                schedule["message"] = data["message"]
            if "runAt" in data:
                schedule["runAt"] = data["runAt"]
                # Reset status to pending when runAt is updated
                schedule["status"] = "pending"
            
            schedule["updatedAt"] = int(time.time() * 1000)
            
            # Save
            update_schedule(schedule_id, schedule)
            
            return jsonify({
                "success": True,
                "schedule": schedule,
                "message": "Đã cập nhật lịch"
            })
        
        elif request.method == "DELETE":
            # Xóa lịch gửi
            delete_schedule(schedule_id)
            return jsonify({"success": True, "message": "Đã xóa lịch"})
        
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500




@app.route("/api/schedules/<schedule_id>/cancel", methods=["POST"])
def api_cancel_schedule_api(schedule_id):
    """API: Hủy lịch gửi."""
    try:
        schedule = cancel_schedule(schedule_id)
        return jsonify({
            "success": True,
            "schedule": schedule,
            "message": "Đã hủy lịch"
        })
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/schedules/<schedule_id>/run-now", methods=["POST"])
def api_run_schedule_now(schedule_id):
    """API: Chạy lịch ngay bây giờ."""
    try:
        schedule = get_schedule(schedule_id)
        if not schedule:
            return jsonify({"error": "Không tìm thấy lịch"}), 404
        
        if schedule.get("status") != "pending":
            return jsonify({"error": f"Lịch đang {schedule.get('status')}, không thể chạy"}), 400
        
        # Update runAt to now
        import datetime
        now = datetime.datetime.now().isoformat()
        schedule = update_schedule(schedule_id, {"runAt": now})
        
        return jsonify({
            "success": True,
            "schedule": schedule,
            "message": "Lịch sẽ chạy trong vòng 5 giây"
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


def _lookup_phones_worker(task, account_id: str, phones: list):
    """Worker lookup số điện thoại: chạy nền + chạy song song từng số."""
    try:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from features.profiles.search_info_from_phone import (
            get_profile_by_phone,
            normalize_vn_phone
        )

        account = get_account(account_id)
        if not account:
            raise ValueError("Không tìm thấy account")

        cookies = account.get("cookies", "")
        zpw_enk = account.get("zpwEnk", "")
        imei = account.get("imei", "")
        zpw_ver = get_zpw_ver()

        missing = []
        if not cookies:
            missing.append("cookies")
        if not zpw_enk:
            missing.append("zpwEnk")
        if not imei:
            missing.append("imei")

        if missing:
            raise ValueError("Account thiếu: " + ", ".join(missing))

        max_lookup = 500
        max_workers = 8
        normalized_map = {}

        for raw_phone in phones:
            normalized = normalize_vn_phone(raw_phone)
            if normalized and normalized not in normalized_map:
                normalized_map[normalized] = raw_phone

        normalized_items = list(normalized_map.items())

        if len(normalized_items) > max_lookup:
            raise ValueError(f"Chỉ được tra tối đa {max_lookup} số mỗi lần")

        total = len(normalized_items)
        if total == 0:
            raise ValueError("Không có số điện thoại hợp lệ")

        workers = max(1, min(max_workers, total))
        task.log(f"Bắt đầu tra {total} số điện thoại song song với {workers} workers...")
        results = [None] * total

        def _lookup_one(idx, normalized_phone, raw_phone):
            try:
                profile = get_profile_by_phone(
                    phone=normalized_phone,
                    zpw_enk=zpw_enk,
                    cookies=cookies,
                    imei=imei,
                    avatar_size=240,
                    language="vi",
                    req_src=85,
                    zpw_ver=zpw_ver
                )

                if profile and profile.get("userId"):
                    return idx, {
                        "phone": raw_phone,
                        "normalizedPhone": normalized_phone,
                        "success": True,
                        "profile": profile
                    }, None

                return idx, {
                    "phone": raw_phone,
                    "normalizedPhone": normalized_phone,
                    "success": False,
                    "error": "Không lấy được profile hoặc thiếu userId"
                }, None

            except Exception as e:
                return idx, {
                    "phone": raw_phone,
                    "normalizedPhone": normalized_phone,
                    "success": False,
                    "error": str(e)
                }, str(e)

        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_map = {
                executor.submit(_lookup_one, idx, normalized_phone, raw_phone): (idx, normalized_phone, raw_phone)
                for idx, (normalized_phone, raw_phone) in enumerate(normalized_items)
            }

            done = 0
            for future in as_completed(future_map):
                idx, normalized_phone, raw_phone = future_map[future]
                _, item, err = future.result()
                results[idx] = item
                done += 1
                task.set_progress(int(done / total * 100))

                if item.get("success"):
                    name = (item.get("profile") or {}).get("displayName") or (item.get("profile") or {}).get("zaloName") or "N/A"
                    task.log(f"✅ ({done}/{total}) {raw_phone}: {name}")
                else:
                    task.log(f"⚠️ ({done}/{total}) {raw_phone}: {item.get('error')}")

        results = [r for r in results if r is not None]
        success_count = len([r for r in results if r.get("success")])
        task.set_progress(100)
        task.log(f"Hoàn thành! {success_count}/{len(results)} số có profile")

        result_data = {
            "results": results,
            "total": len(results),
            "success_count": success_count,
            "parallel": True,
            "max_workers": workers,
            "max_lookup": max_lookup
        }

        task.set_completed(result_data)
        return result_data

    except Exception as e:
        import traceback
        error_msg = str(e)
        task.set_failed(error_msg)
        print(f"[_lookup_phones_worker] Error: {error_msg}")
        traceback.print_exc()
        return {"error": error_msg}


@app.route("/api/schedules/lookup-phones", methods=["POST"])
def api_lookup_phones_for_schedule():
    """API: Tra thông tin số điện thoại để lập lịch gửi (chạy async, không block)."""
    try:
        body = request.get_json(force=True) or {}

        account_id = body.get("account_id") or body.get("accountId") or ""
        phones = body.get("phones") or []
        phones_text = body.get("phonesText") or ""

        if phones_text and not phones:
            phones = [
                line.strip()
                for line in phones_text.replace(",", "\n").splitlines()
                if line.strip()
            ]

        if not account_id:
            return jsonify({
                "success": False,
                "error": "Thiếu account_id"
            }), 400

        if not phones:
            return jsonify({
                "success": False,
                "error": "Chưa nhập số điện thoại"
            }), 400

        # Validate account has credentials
        account = get_account(account_id)
        if not account:
            return jsonify({
                "success": False,
                "error": "Không tìm thấy account"
            }), 404
        
        # Start background task
        task = run_task_in_background(
            _lookup_phones_worker,
            f"Lookup {len(phones)} phones",
            f"Tra thông tin {len(phones)} số điện thoại",
            account_id,
            phones
        )
        
        # Return task info immediately
        return jsonify({
            "success": True,
            "taskId": task.task_id,
            "status": task.status,
            "phonesCount": len(phones),
            "message": f"Đang tra {len(phones)} số... Dùng /api/tasks/{task.task_id} để check progress"
        }), 202  # 202 Accepted

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@app.route("/api/groups/invite", methods=["POST"])
def api_invite_members_to_groups():
    """API: Mời danh sách thành viên đã chọn vào một hoặc nhiều nhóm cá nhân."""
    data = request.get_json(silent=True) or {}

    account_id = str(data.get("accountId", data.get("account_id", "")) or "").strip()
    group_ids = data.get("groupIds") or data.get("groups") or data.get("gridIds") or []
    user_ids = data.get("userIds") or data.get("members") or data.get("uids") or []

    try:
        batch_size = int(data.get("batchSize") or 50)
    except Exception:
        batch_size = 50
    batch_size = max(1, min(batch_size, 100))

    if isinstance(group_ids, str):
        group_ids = [group_ids]
    if isinstance(user_ids, str):
        user_ids = [user_ids]

    # Chuẩn hóa và bỏ trùng nhưng giữ thứ tự.
    seen_groups = set()
    clean_group_ids = []
    for gid in group_ids:
        gid = str(gid or "").strip()
        if gid and gid not in seen_groups:
            seen_groups.add(gid)
            clean_group_ids.append(gid)

    seen_users = set()
    clean_user_ids = []
    for uid in user_ids:
        uid = str(uid or "").strip()
        if uid and uid not in seen_users:
            seen_users.add(uid)
            clean_user_ids.append(uid)

    if not account_id:
        return jsonify({"success": False, "error": "Chưa chọn tài khoản thực hiện."}), 400
    if not clean_group_ids:
        return jsonify({"success": False, "error": "Chưa chọn nhóm để mời."}), 400
    if not clean_user_ids:
        return jsonify({"success": False, "error": "Danh sách thành viên cần mời đang trống."}), 400

    try:
        _, cookies, zpw_enk, imei = _get_account_credentials(account_id, require_imei=True)
        zpw_ver = get_zpw_ver()

        def _chunks(items, size):
            for i in range(0, len(items), size):
                yield items[i:i + size]

        results = []
        total_invited = 0
        total_failed = 0

        for group_id in clean_group_ids:
            group_result = {
                "groupId": group_id,
                "success": True,
                "invited": 0,
                "failed": 0,
                "errorMembers": [],
                "batches": [],
            }

            for batch_index, member_batch in enumerate(_chunks(clean_user_ids, batch_size), start=1):
                response_json, decoded = _invite_group(
                    group_id,
                    member_batch,
                    imei,
                    zpw_enk,
                    cookies,
                    zpw_ver=zpw_ver,
                )

                if isinstance(decoded, str):
                    try:
                        decoded = json.loads(decoded)
                    except Exception:
                        decoded = {"raw": decoded}

                decoded = decoded if isinstance(decoded, dict) else {}
                data_obj = decoded.get("data") if isinstance(decoded.get("data"), dict) else {}
                error_code = decoded.get("error_code", response_json.get("error_code", -1))
                error_message = decoded.get("error_message", response_json.get("error_message", ""))
                error_members = data_obj.get("errorMembers") or []
                if not isinstance(error_members, list):
                    error_members = [error_members]

                try:
                    error_code_int = int(error_code or 0)
                except Exception:
                    error_code_int = -1

                ok = error_code_int == 0
                batch_failed = len(error_members) if ok else len(member_batch)
                batch_invited = max(0, len(member_batch) - batch_failed) if ok else 0

                if (not ok) or batch_failed:
                    group_result["success"] = False

                group_result["invited"] += batch_invited
                group_result["failed"] += batch_failed
                group_result["errorMembers"].extend([str(x) for x in error_members])
                group_result["batches"].append({
                    "batch": batch_index,
                    "total": len(member_batch),
                    "success": ok and batch_failed == 0,
                    "errorCode": error_code,
                    "errorMessage": error_message,
                    "invited": batch_invited,
                    "failed": batch_failed,
                    "errorMembers": error_members,
                })

            total_invited += group_result["invited"]
            total_failed += group_result["failed"]
            results.append(group_result)

        all_success = all(r.get("success") for r in results)
        return jsonify({
            "success": all_success,
            "partial": not all_success and total_invited > 0,
            "message": (
                f"Mời thành công {total_invited} lượt."
                if all_success else
                f"Đã mời {total_invited} lượt, lỗi {total_failed} lượt."
            ),
            "totalGroups": len(clean_group_ids),
            "totalMembers": len(clean_user_ids),
            "totalInvited": total_invited,
            "totalFailed": total_failed,
            "results": results,
        }), 200 if total_invited > 0 or all_success else 400

    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


# ─── GROUP APIs ─────────────────────────────────────────────────────────


def _chrome_debug_port_alive(port) -> bool:
    """Kiểm tra Chrome remote-debugging port còn truy cập được không."""
    try:
        port = int(port)
    except Exception:
        return False
    try:
        resp = requests.get(f"http://127.0.0.1:{port}/json/version", timeout=1.2)
        return resp.status_code == 200
    except Exception:
        return False


@app.route("/api/groups/personal", methods=["GET"])
def api_get_account_groups():
    """API: Lay danh sach personalGroups da luu trong data/accounts.json theo account dang chon."""
    account_id = (
        request.args.get("accountId")
        or request.args.get("account_id")
        or request.args.get("id")
        or ""
    ).strip()

    if not account_id:
        return jsonify({"success": False, "error": "Thieu accountId", "groups": []}), 400

    try:
        accounts = load_accounts()

        account = None
        for acc in accounts:
            aid = str(acc.get("accountId") or acc.get("id") or acc.get("account_id") or "").strip()
            if aid == account_id:
                account = acc
                break

        if not account:
            return jsonify({
                "success": False,
                "error": "Khong tim thay tai khoan",
                "accountId": account_id,
                "groups": []
            }), 404

        raw_groups = account.get("personalGroups") or []

        # personalGroups co the la list hoac dict
        if isinstance(raw_groups, dict):
            raw_groups = list(raw_groups.values())

        groups = []
        for g in raw_groups:
            if not isinstance(g, dict):
                continue

            gid = str(
                g.get("groupId")
                or g.get("gridId")
                or g.get("id")
                or g.get("gid")
                or ""
            ).strip()

            if not gid:
                continue

            name = (
                g.get("name")
                or g.get("grid_name")
                or g.get("groupName")
                or g.get("title")
                or ("Group " + gid[:8])
            )

            groups.append({
                "groupId": gid,
                "name": name,
                "avatar": g.get("avatar") or g.get("grid_avatar") or g.get("avt") or "",
                "fullAvt": g.get("fullAvt") or g.get("grid_fullAvt") or g.get("fullAvatar") or "",
                "memberCount": g.get("memberCount") or g.get("grid_totalMember") or g.get("totalMember") or g.get("total") or 0,
                "fetchStatus": g.get("fetchStatus") or "",
                "error": g.get("error") or "",
            })

        print("[api_get_account_groups] account_id=", account_id, "groups=", len(groups))

        return jsonify({
            "success": True,
            "accountId": account_id,
            "groups": groups,
            "total": len(groups),
            "groupsSyncedAt": int(account.get("groupsSyncedAt") or 0),
            "groupsSyncStatus": account.get("groupsSyncStatus") or "",
            "accountUpdatedAt": int(account.get("updatedAt") or 0),
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e), "groups": []}), 500


@app.route("/api/groups/refresh-account", methods=["POST"])
def api_refresh_account_groups():
    """
    Mở hoặc kích hoạt lại tài khoản Zalo đang chọn để monitor bắt getlg/v4,
    giải mã danh sách nhóm và cập nhật personalGroups.
    """
    data = request.get_json(silent=True) or request.form or {}
    account_id = str(
        data.get("accountId")
        or data.get("account_id")
        or data.get("id")
        or ""
    ).strip()

    if not account_id:
        return jsonify({"success": False, "error": "Chưa chọn tài khoản."}), 400

    try:
        account = get_account(account_id)
        if not account:
            return jsonify({"success": False, "error": "Không tìm thấy tài khoản."}), 404

        started_at = int(time.time() * 1000)
        port = account.get("remoteDebugPort")
        reused_running_browser = False

        # Nếu Chrome của account đang mở, không mở thêm profile trùng nữa.
        # Chỉ reset trạng thái capture và start monitor để điều hướng/reload Zalo Web.
        if port and _chrome_debug_port_alive(port):
            update_account(
                account_id,
                loginCaptured=False,
                userinfoCaptured=False,
                cookies="",
                personalGroups=[],
                groupsSyncedAt=0,
                groupsSyncStatus="refreshing",
            )
            from features.accounts.account_network_monitor import start_account_network_monitor
            start_account_network_monitor(account_id, int(port))
            account = get_account(account_id) or account
            reused_running_browser = True
        else:
            account = open_account(account_id, clear_groups=True)

        return jsonify({
            "success": True,
            "accountId": account_id,
            "startedAt": started_at,
            "reusedRunningBrowser": reused_running_browser,
            "account": account,
            "message": "Đã mở tài khoản Zalo và bật monitor đồng bộ nhóm.",
        })

    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 404
    except FileNotFoundError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/groups/join-link", methods=["POST"])
def api_join_group_by_link():
    """API riêng: tham gia nhóm bằng link mời."""
    data = request.get_json(silent=True) or request.form or {}
    account_id = str(data.get("accountId") or data.get("account_id") or "").strip()
    link = str(data.get("link") or data.get("groupLink") or data.get("group_link") or "").strip()

    if not account_id:
        return jsonify({"success": False, "error": "Chưa chọn tài khoản."}), 400
    if not link:
        return jsonify({"success": False, "error": "Thiếu link nhóm."}), 400

    try:
        _account, cookies, zpw_enk, _imei = _get_account_credentials(account_id, require_imei=False)
        result = _join_group_by_link(link, zpw_enk, cookies, zpw_ver=get_zpw_ver())
        status = 200 if result.get("ok") else 400
        return jsonify({
            "success": bool(result.get("ok")),
            "message": result.get("message", ""),
            "groupId": result.get("groupId", ""),
            "error_code": result.get("error_code"),
            "decoded": result.get("decoded"),
        }), status
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/groups/leave", methods=["POST"])
def api_leave_group():
    """API riêng: rời một hoặc nhiều nhóm theo groupId/grid."""
    data = request.get_json(silent=True) or request.form or {}
    account_id = str(data.get("accountId") or data.get("account_id") or "").strip()
    grids = data.get("grids") or data.get("groupIds") or data.get("group_ids") or data.get("grid") or data.get("groupId") or ""

    if isinstance(grids, str):
        grids = [x.strip() for x in re.split(r"[,\n\s]+", grids) if x.strip()]
    elif not isinstance(grids, list):
        grids = [grids]

    if not account_id:
        return jsonify({"success": False, "error": "Chưa chọn tài khoản."}), 400
    if not grids:
        return jsonify({"success": False, "error": "Thiếu groupId cần rời."}), 400

    try:
        _account, cookies, zpw_enk, imei = _get_account_credentials(account_id, require_imei=False)
        result = _leave_group(grids, imei=imei, zpw_enk=zpw_enk, cookies=cookies, zpw_ver=get_zpw_ver())
        status = 200 if result.get("ok") else 400
        return jsonify({
            "success": bool(result.get("ok")),
            "message": result.get("message", ""),
            "grids": result.get("grids", []),
            "error_code": result.get("error_code"),
            "decoded": result.get("decoded"),
        }), status
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500



@app.route("/api/groups/members", methods=["GET"])
def api_get_group_members_for_manager():
    """API: Lấy thành viên của một nhóm trong tab Quản lý nhóm."""
    account_id = (
        request.args.get("accountId")
        or request.args.get("account_id")
        or ""
    ).strip()
    group_id = (
        request.args.get("groupId")
        or request.args.get("grid")
        or request.args.get("id")
        or ""
    ).strip()

    if not account_id:
        return jsonify({"success": False, "error": "Chưa chọn tài khoản.", "members": []}), 400
    if not group_id:
        return jsonify({"success": False, "error": "Chưa chọn nhóm.", "members": []}), 400

    try:
        account, cookies, zpw_enk, imei = _get_account_credentials(account_id, require_imei=False)
        zpw_ver = get_zpw_ver()

        member_payload = fetch_group_members_by_input(
            group_id,
            zpw_enk,
            cookies,
            callback=None,
            zpw_ver=zpw_ver,
            imei=imei,
        )

        uid_list = member_payload.get("uidList") or []
        group_info = member_payload.get("groupInfo") or {}
        resolved_group_id = member_payload.get("groupId") or group_id
        member_map = member_payload.get("memberMap") or {}

        profiles = fetch_profiles_with_single_fallback(
            uid_list,
            zpw_enk,
            cookies,
            imei=imei,
            log_func=None,
            zpw_ver=zpw_ver,
        )
        members = build_member_rows_from_uids(uid_list, profiles, member_map=member_map)

        return jsonify({
            "success": True,
            "accountId": account_id,
            "groupId": str(resolved_group_id),
            "total": len(members),
            "members": members,
            "groupInfo": format_group_info(group_info, group_id=resolved_group_id, fallback_total=len(members)),
        })

    except ValueError as e:
        return jsonify({"success": False, "error": str(e), "members": []}), 400
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e), "members": []}), 500


# ─── TASK MANAGEMENT APIs ──────────────────────────────────────────────────────

@app.route("/api/tasks", methods=["GET"])
def api_list_tasks():
    """API: Danh sách tất cả tasks (background jobs)."""
    try:
        status_filter = request.args.get("status")  # "running", "completed", "failed", etc.
        limit = int(request.args.get("limit", 50))
        
        tasks = list_tasks(status=status_filter, limit=limit)
        task_dicts = [t.to_dict() for t in tasks]
        
        return jsonify({
            "success": True,
            "tasks": task_dicts,
            "total": len(task_dicts)
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/tasks/<task_id>", methods=["GET"])
def api_get_task_status(task_id):
    """API: Lấy status + progress + result của 1 task."""
    try:
        task = get_task(task_id)
        if not task:
            return jsonify({"error": f"Task {task_id} not found"}), 404
        
        return jsonify({
            "success": True,
            "task": task.to_dict()
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/groups/info", methods=["POST"])
def api_get_group_info():
    """API: Lấy thông tin nhóm chi tiết từ Zalo API (không lấy danh sách thành viên)."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400
    
    account_id = data.get("accountId") or data.get("account_id")
    group_id = data.get("groupId") or data.get("group_id")
    
    if not account_id or not group_id:
        return jsonify({"error": "Thiếu accountId hoặc groupId"}), 400
    
    try:
        account = get_account(account_id)
        if not account:
            return jsonify({"error": "Không tìm thấy tài khoản"}), 404
        
        zpw_enk = account.get("zpwEnk", "")
        cookies = account.get("cookies", "")
        imei = account.get("imei", "")
        
        if not zpw_enk or not cookies:
            return jsonify({"error": "Tài khoản chưa đầy đủ thông tin (zpwEnk, cookies)"}), 400
        
        sse_broadcast(f"Đang lấy thông tin nhóm {group_id[:8]}...", "loading")
        
        from features.members.get_members import get_members as get_members_api
        
        try:
            print(f"[api_groups_info] Calling get_members for group {group_id[:8]} zpw_enk={zpw_enk[:8]}... cookies len={len(cookies)}")
            resp, decoded, ginfo, mem_list = get_members_api(group_id, zpw_enk, cookies, imei=imei, zpw_ver=get_zpw_ver())
            
            print(f"[api_groups_info] Response: decoded={type(decoded)} ginfo keys={list(ginfo.keys()) if ginfo else 'None'}")
            
            if not ginfo:
                print(f"[api_groups_info] ginfo is empty!")
                ginfo = {}
            
            print(f"[api_groups_info] Returning groupInfo with name={ginfo.get('grid_name', 'N/A')}")
            sse_broadcast(f"Hoàn thành! {ginfo.get('grid_name', 'Nhóm')}", "success")
            
            return jsonify({
                "success": True,
                "groupInfo": {
                    "groupId": ginfo.get("gridId", group_id),
                    "name": ginfo.get("grid_name", ""),
                    "desc": ginfo.get("grid_desc", ""),
                    "type": ginfo.get("grid_type", 0),
                    "creatorId": ginfo.get("grid_creatorId", ""),
                    "adminIds": ginfo.get("grid_adminIds", []),
                    "avt": ginfo.get("grid_avatar", ""),
                    "fullAvt": ginfo.get("grid_fullAvt", ""),
                    "totalMember": ginfo.get("grid_totalMember", 0),
                }
            })
        
        except Exception as e:
            import traceback
            error_trace = traceback.format_exc()
            print(f"[api_groups_info] ERROR: {e}")
            print(f"[api_groups_info] Traceback:\n{error_trace}")
            error_msg = f"Lỗi khi lấy thông tin nhóm: {str(e)}"
            sse_broadcast(error_msg, "error")
            return jsonify({
                "success": False,
                "error": error_msg
            }), 400
    
    except Exception as e:
        import traceback
        traceback.print_exc()
        sse_broadcast(f"Lỗi: {str(e)}", "error")
        return jsonify({"error": str(e)}), 500


# ─── Device Management Endpoints ──────────────────────────────────────────────
DEVICE_REGISTRY_FILE = os.path.join(app_root, "data", "devices.json")

def load_devices_registry():
    """Tải danh sách thiết bị"""
    if os.path.exists(DEVICE_REGISTRY_FILE):
        try:
            with open(DEVICE_REGISTRY_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_devices_registry(devices):
    """Lưu danh sách thiết bị"""
    os.makedirs(os.path.dirname(DEVICE_REGISTRY_FILE), exist_ok=True)
    with open(DEVICE_REGISTRY_FILE, 'w', encoding='utf-8') as f:
        json.dump(devices, f, indent=2, ensure_ascii=False)

@app.route('/api/device/register', methods=['POST'])
def api_device_register():
    """
    Đăng ký thiết bị mới
    """
    try:
        data = request.json
        device_id = data.get("device_id")
        
        if not device_id:
            return jsonify({"error": "device_id required"}), 400
        
        devices = load_devices_registry()
        
        # Cập nhật hoặc thêm mới thiết bị
        devices[device_id] = {
            "device_name": data.get("device_name", "Unknown"),
            "username": data.get("username", "Unknown"),
            "device_id": device_id,
            "mac_address": data.get("mac_address", ""),
            "imei": data.get("imei", "N/A"),
            "location": data.get("location", "Unknown"),
            "os": data.get("os", ""),
            "ip_address": data.get("ip_address", ""),
            "first_seen": devices.get(device_id, {}).get("first_seen", datetime.now().isoformat()),
            "last_seen": datetime.now().isoformat(),
            "active": True
        }
        
        save_devices_registry(devices)
        
        print(f"✅ Đã đăng ký thiết bị: {data.get('device_name')} ({device_id})")
        
        return jsonify({
            "success": True,
            "message": f"Đã đăng ký thiết bị: {data.get('device_name')}",
            "device": devices[device_id]
        }), 201
        
    except Exception as e:
        print(f"❌ Lỗi đăng ký thiết bị: {str(e)}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/device/log', methods=['POST'])
def api_device_log():
    """
    Ghi log hoạt động thiết bị
    """
    try:
        data = request.json
        device_name = data.get("device_name", "Unknown")
        action = data.get("action", "unknown_action")
        
        log_file = os.path.join(app_root, "data", "device_logs.json")
        
        # Tải logs hiện có
        logs = []
        if os.path.exists(log_file):
            try:
                with open(log_file, 'r', encoding='utf-8') as f:
                    logs = json.load(f)
            except:
                logs = []
        
        # Thêm log mới
        log_entry = {
            "device_name": device_name,
            "username": data.get("username", "Unknown"),
            "action": action,
            "details": data.get("details", ""),
            "timestamp": datetime.now().isoformat(),
            "ip_address": data.get("ip_address", "")
        }
        
        logs.append(log_entry)
        
        # Giữ chỉ 1000 log gần nhất
        if len(logs) > 1000:
            logs = logs[-1000:]
        
        # Lưu logs
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        with open(log_file, 'w', encoding='utf-8') as f:
            json.dump(logs, f, indent=2, ensure_ascii=False)
        
        print(f"📝 Log: {device_name} - {action}")
        
        return jsonify({
            "success": True,
            "message": "Log đã ghi"
        }), 201
        
    except Exception as e:
        print(f"❌ Lỗi ghi log: {str(e)}")
        return jsonify({"error": str(e)}), 500


@app.route('/api/devices/list', methods=['GET'])
def api_devices_list():
    """
    Lấy danh sách tất cả thiết bị đã đăng ký
    """
    try:
        devices = load_devices_registry()
        
        return jsonify({
            "success": True,
            "total": len(devices),
            "devices": list(devices.values())
        })
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/devices', methods=['GET'])
def devices_dashboard():
    """
    Hiển thị dashboard quản lý thiết bị
    """
    try:
        return render_template('devices.html')
    except Exception as e:
        return f"Lỗi: {str(e)}", 500


# ─── Ensure UTF-8 encoding for all responses ──────────────────────────────────
@app.after_request
def after_request(response):
    """Ensure all responses use UTF-8 encoding."""
    content_type = response.headers.get('Content-Type', '')
    
    # If no content type, assume text/html
    if not content_type:
        response.headers['Content-Type'] = 'text/html; charset=utf-8'
    # If content type exists but no charset, add it
    elif 'charset' not in content_type:
        if 'application/json' in content_type:
            response.headers['Content-Type'] = 'application/json; charset=utf-8'
        elif 'text/html' in content_type:
            response.headers['Content-Type'] = 'text/html; charset=utf-8'
        elif 'text/javascript' in content_type or 'application/javascript' in content_type:
            response.headers['Content-Type'] = f'{content_type}; charset=utf-8'
        elif 'text/' in content_type:
            response.headers['Content-Type'] = f'{content_type}; charset=utf-8'
    
    return response



# ─── Tkinter log launcher ─────────────────────────────────────────────────────
class _GuiLogWriter:
    """Redirect stdout/stderr into a thread-safe queue for the Tkinter log window.

    Flask/Werkzeug/Click sometimes writes bytes instead of str when stdout is
    replaced by a custom stream. This writer accepts both, keeps console output
    if available, and mirrors everything to the GUI log box.
    """
    encoding = "utf-8"
    errors = "replace"

    def __init__(self, log_queue, original_stream=None):
        self.log_queue = log_queue
        self.original_stream = original_stream
        self._buffer = ""

    def _to_text(self, data):
        if data is None:
            return ""
        if isinstance(data, bytes):
            return data.decode(self.encoding, errors=self.errors)
        return str(data)

    def write(self, data):
        text = self._to_text(data)
        if not text:
            return 0

        if self.original_stream:
            try:
                self.original_stream.write(text)
                self.original_stream.flush()
            except Exception:
                pass

        self._buffer += text
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            self.log_queue.put(line + "\n")
        return len(text)

    def flush(self):
        if self._buffer:
            self.log_queue.put(self._buffer)
            self._buffer = ""
        if self.original_stream:
            try:
                self.original_stream.flush()
            except Exception:
                pass

    def isatty(self):
        try:
            return bool(self.original_stream and self.original_stream.isatty())
        except Exception:
            return False

    def writable(self):
        return True


def _get_app_icon_path():
    """Tìm icon .ico của ứng dụng cho Tkinter và PyInstaller.

    Ưu tiên static/ico/app.ico trong thư mục resource đã extract của PyInstaller,
    sau đó fallback về thư mục chạy EXE/source.
    """
    candidates = []
    try:
        candidates.append(os.path.join(static_path, "ico", "app.ico"))
    except Exception:
        pass
    try:
        candidates.append(os.path.join(base_path, "static", "ico", "app.ico"))
    except Exception:
        pass
    try:
        candidates.append(os.path.join(app_root, "static", "ico", "app.ico"))
    except Exception:
        pass
    try:
        candidates.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "ico", "app.ico"))
    except Exception:
        pass

    for path in candidates:
        if path and os.path.exists(path):
            return path
    return None


def _enable_windows_terminal_copy_mode():
    """Cho phép click/kéo chọn text trong CMD/PowerShell nếu app vẫn chạy console."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        STD_INPUT_HANDLE = -10

        ENABLE_MOUSE_INPUT = 0x0010
        ENABLE_INSERT_MODE = 0x0020
        ENABLE_QUICK_EDIT_MODE = 0x0040
        ENABLE_EXTENDED_FLAGS = 0x0080
        ENABLE_VIRTUAL_TERMINAL_INPUT = 0x0200

        stdin_handle = kernel32.GetStdHandle(STD_INPUT_HANDLE)
        mode = ctypes.c_uint32()

        if stdin_handle and stdin_handle != ctypes.c_void_p(-1).value:
            if kernel32.GetConsoleMode(stdin_handle, ctypes.byref(mode)):
                old_mode = mode.value
                new_mode = old_mode | ENABLE_EXTENDED_FLAGS | ENABLE_QUICK_EDIT_MODE | ENABLE_INSERT_MODE
                new_mode &= ~ENABLE_MOUSE_INPUT
                new_mode &= ~ENABLE_VIRTUAL_TERMINAL_INPUT

                if kernel32.SetConsoleMode(stdin_handle, new_mode):
                    print(
                        f"✅ Terminal edit/copy mode enabled: old=0x{old_mode:04x}, new=0x{new_mode:04x}",
                        flush=True,
                    )
                    print("✅ Có thể click/kéo chọn text trong terminal để copy log", flush=True)
                else:
                    err = kernel32.GetLastError()
                    print(f"⚠️ Không bật được terminal edit/copy mode. GetLastError={err}", flush=True)
    except Exception as e:
        print(f"⚠️ Không bật được terminal edit/copy mode: {e}", flush=True)


def _start_backend_server():
    """Start các worker phụ và Flask server. Hàm này chạy trong thread nền."""
    try:
        _enable_windows_terminal_copy_mode()

        # Kiểm tra/kích hoạt thiết bị trước khi cho worker chạy nền.
        if DEVICE_TRACKING_ENABLED:
            try:
                register_device_on_startup(verbose=True)
                is_valid, days_remaining, message = validate_activation_code()
                print(message, flush=True)
                if is_valid:
                    ensure_schedule_worker_started()
                    print(f"✅ License còn {days_remaining} ngày", flush=True)
                else:
                    print("🔐 Phần mềm chưa kích hoạt. Mở giao diện /activation để chọn thời gian kích hoạt và nhận mã 12 ký tự qua email.", flush=True)
            except Exception as e:
                print(f"⚠️  Lỗi kiểm tra/kích hoạt thiết bị: {str(e)}", flush=True)
        else:
            ensure_schedule_worker_started()

        print("✅ Backend đã sẵn sàng", flush=True)
        print("🌐 Giao diện: http://127.0.0.1:5000/guide", flush=True)
        print("👉 Bấm nút 'Mở giao diện' để mở trình duyệt.", flush=True)
        app.run(debug=False, port=5000, host="127.0.0.1", use_reloader=False, threaded=True)
    except Exception as e:
        print(f"❌ Lỗi khởi động backend: {e}", flush=True)
        import traceback
        traceback.print_exc()


def _launch_log_window():
    """Mở cửa sổ Tkinter hiển thị log và nút mở giao diện."""
    import webbrowser
    import tkinter as tk
    from tkinter import ttk
    from tkinter.scrolledtext import ScrolledText

    log_queue = queue.Queue()
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    sys.stdout = _GuiLogWriter(log_queue, original_stdout)
    sys.stderr = _GuiLogWriter(log_queue, original_stderr)

    # Đặt AppUserModelID trước khi tạo cửa sổ để Windows taskbar dùng icon riêng của app
    # thay vì icon mặc định của python.exe / tkinter.
    try:
        if sys.platform.startswith("win"):
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "PhanMem.Nexus.CMDLog"
            )
    except Exception:
        pass

    root = tk.Tk()
    root.title("Nexus - CMD Log")
    root.geometry("1000x640")
    root.minsize(760, 420)
    root.configure(bg="#0c0c0c")

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except Exception:
        pass
    style.configure("Cmd.TFrame", background="#0c0c0c")
    style.configure("Cmd.TLabel", background="#0c0c0c", foreground="#cfcfcf", font=("Consolas", 10))
    style.configure("CmdTitle.TLabel", background="#0c0c0c", foreground="#ffffff", font=("Consolas", 13, "bold"))
    style.configure("Cmd.TButton", font=("Consolas", 10), padding=(12, 6))

    try:
        icon_path = _get_app_icon_path()
        if icon_path and os.path.exists(icon_path):
            # iconbitmap(default=...) áp dụng cho title bar và các cửa sổ con Tkinter.
            root.iconbitmap(default=icon_path)
    except Exception as e:
        try:
            original_stdout.write(f"⚠️ Không đặt được icon cửa sổ: {e}\n")
        except Exception:
            pass

    header = ttk.Frame(root, padding=(12, 10, 12, 6), style="Cmd.TFrame")
    header.pack(fill="x")

    title = ttk.Label(header, text="Nexus - CMD Log", style="CmdTitle.TLabel")
    title.pack(side="left")

    status_var = tk.StringVar(value="Đang khởi động backend...")
    status = ttk.Label(header, textvariable=status_var, style="Cmd.TLabel")
    status.pack(side="right")

    log_box = ScrolledText(
        root,
        wrap="word",
        font=("Consolas", 10),
        height=28,
        bg="#0c0c0c",
        fg="#e6e6e6",
        insertbackground="#ffffff",
        selectbackground="#264f78",
        selectforeground="#ffffff",
        relief="flat",
        borderwidth=0,
    )
    log_box.pack(fill="both", expand=True, padx=12, pady=(4, 8))
    log_box.configure(state="disabled")

    buttons = ttk.Frame(root, padding=(12, 0, 12, 12), style="Cmd.TFrame")
    buttons.pack(fill="x")

    def open_ui():
        webbrowser.open("http://127.0.0.1:5000/guide")
        print("🌐 Đã mở giao diện: http://127.0.0.1:5000/guide", flush=True)

    def clear_log():
        log_box.configure(state="normal")
        log_box.delete("1.0", "end")
        log_box.configure(state="disabled")

    open_btn = ttk.Button(buttons, text="Mở giao diện", command=open_ui, style="Cmd.TButton")
    open_btn.pack(side="left")

    clear_btn = ttk.Button(buttons, text="Xóa log", command=clear_log, style="Cmd.TButton")
    clear_btn.pack(side="left", padx=(8, 0))

    hint = ttk.Label(buttons, text="App không tự mở trình duyệt khi khởi động.", style="Cmd.TLabel")
    hint.pack(side="right")

    def pump_logs():
        had_log = False
        while True:
            try:
                text = log_queue.get_nowait()
            except queue.Empty:
                break
            had_log = True
            log_box.configure(state="normal")
            log_box.insert("end", text)
            log_box.see("end")
            log_box.configure(state="disabled")
            if "Backend đã sẵn sàng" in text or "Running on" in text:
                status_var.set("Backend đang chạy")
            elif "Lỗi khởi động backend" in text:
                status_var.set("Backend lỗi")
        root.after(120 if had_log else 250, pump_logs)

    def on_close():
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        except Exception:
            pass
        root.destroy()
        # Flask chạy nền bằng daemon thread; thoát GUI thì thoát app luôn.
        os._exit(0)

    root.protocol("WM_DELETE_WINDOW", on_close)

    server_thread = threading.Thread(target=_start_backend_server, daemon=True)
    server_thread.start()

    print("🚀 Đang khởi động Nexus...", flush=True)
    print("📌 Trình duyệt sẽ không tự mở. Bấm 'Mở giao diện' khi cần.", flush=True)
    pump_logs()
    root.mainloop()


if __name__ == "__main__":
    _launch_log_window()

