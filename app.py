import sys
import os

from flask import Flask, render_template, request, jsonify, Response, redirect, url_for
import json
import time
import queue
import threading
import requests
import re
import uuid
import hashlib
import shutil
import subprocess
import tempfile
import zipfile
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
    """Get the resource directory used by Flask.

    In a PyInstaller one-file build the bundled templates/static files are
    extracted into ``sys._MEIPASS``.  Before Flask starts, overlay any external
    ``templates`` and ``static`` folders placed next to Nexus.exe onto that
    extracted directory.  This keeps the EXE self-contained by default while
    allowing a small UI patch ZIP to take effect without rebuilding the whole
    application again.
    """
    if getattr(sys, "frozen", False):
        bundled_root = sys._MEIPASS
        external_root = os.path.dirname(sys.executable)

        for resource_name in ("templates", "static"):
            external_dir = os.path.join(external_root, resource_name)
            bundled_dir = os.path.join(bundled_root, resource_name)
            if not os.path.isdir(external_dir):
                continue
            try:
                os.makedirs(bundled_dir, exist_ok=True)
                shutil.copytree(external_dir, bundled_dir, dirs_exist_ok=True)
                print(f"[UI] Loaded external {resource_name} override from {external_dir}")
            except Exception as error:
                # A failed optional override must never stop Nexus from opening.
                print(f"[UI] Could not load external {resource_name} override: {error}")

        return bundled_root

    # Running as Python source.
    return os.path.dirname(os.path.abspath(__file__))

app_root = get_app_root()

# ─── Single activity log file ─────────────────────────────────────────────────
# Mỗi lần ứng dụng khởi động, file này được xóa trắng và toàn bộ stdout/stderr
# cùng log tác vụ SSE được ghi vào đúng một file duy nhất.
ACTIVITY_LOG_FILE = os.path.join(app_root, "Nexus.log")
_ACTIVITY_LOG_LOCK = threading.RLock()
_ACTIVITY_LOG_INITIALIZED = False


def _append_activity_log(message, level="INFO"):
    """Append one normalized entry to the single activity log file."""
    text = str(message or "").replace("\r\n", "\n").replace("\r", "\n")
    if not text:
        return
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    normalized_level = str(level or "INFO").upper().replace("TASK:", "TASK-")
    lines = text.split("\n")
    try:
        os.makedirs(os.path.dirname(ACTIVITY_LOG_FILE), exist_ok=True)
        with _ACTIVITY_LOG_LOCK:
            with open(ACTIVITY_LOG_FILE, "a", encoding="utf-8", newline="\n") as log_file:
                for line in lines:
                    if line:
                        log_file.write(f"[{timestamp}] [{normalized_level}] {line}\n")
                log_file.flush()
    except Exception:
        # Logging must never stop the application from starting.
        pass


class _ActivityLogWriter:
    """Line-buffered stdout/stderr writer backed by Nexus.log."""
    encoding = "utf-8"
    errors = "replace"

    def __init__(self, level="INFO", original_stream=None):
        self.level = level
        self.original_stream = original_stream
        self._buffer = ""

    def write(self, data):
        if data is None:
            return 0
        if isinstance(data, bytes):
            text = data.decode(self.encoding, errors=self.errors)
        else:
            text = str(data)
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
            if line.strip():
                _append_activity_log(line, self.level)
        return len(text)

    def flush(self):
        if self._buffer.strip():
            _append_activity_log(self._buffer, self.level)
        self._buffer = ""
        if self.original_stream:
            try:
                self.original_stream.flush()
            except Exception:
                pass

    def isatty(self):
        return False

    def writable(self):
        return True


def _initialize_activity_logging():
    """Clear the previous run and redirect all process output to one log file."""
    global _ACTIVITY_LOG_INITIALIZED
    if _ACTIVITY_LOG_INITIALIZED:
        return
    try:
        os.makedirs(os.path.dirname(ACTIVITY_LOG_FILE), exist_ok=True)
        with _ACTIVITY_LOG_LOCK:
            with open(ACTIVITY_LOG_FILE, "w", encoding="utf-8", newline="\n"):
                pass
    except Exception:
        pass

    original_stdout = sys.stdout
    original_stderr = sys.stderr
    sys.stdout = _ActivityLogWriter("INFO", original_stdout)
    sys.stderr = _ActivityLogWriter("ERROR", original_stderr)
    _ACTIVITY_LOG_INITIALIZED = True
    _append_activity_log("Nexus started. Previous activity log was cleared.", "SYSTEM")


if __name__ == "__main__":
    _initialize_activity_logging()

base_path = get_base_path()
template_path = os.path.join(base_path, 'templates')
static_path = os.path.join(base_path, 'static')

app = Flask(__name__, 
            template_folder=template_path,
            static_folder=static_path)
app.secret_key = "zalo-tool-secret-key"
app.config['JSON_AS_ASCII'] = False  # Allow non-ASCII characters in JSON
app.config['JSONIFY_PRETTYPRINT_REGULAR'] = False
# UI patches must be visible immediately after overwrite/restart.
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0
app.config['TEMPLATES_AUTO_RELOAD'] = True

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

from features.members.group_member_service import fetch_group_members_by_input, format_group_info, normalize_group_input, resolve_group_input_to_group_id
from features.profiles.profile_service import fetch_profiles_with_single_fallback, build_member_rows_from_uids, fetch_friend_relations
from features.messaging.send_sms import send_sms
from features.messaging.add_friend import send_friend_request
from features.groups.add_group import create_group as _create_group
from features.groups.invite_group import invite_members_to_group as _invite_members_to_group
from features.groups.group_join_leave import join_group_by_link as _join_group_by_link, leave_group as _leave_group
from features.groups.group_manager import start_fetch_group_details_parallel, ensure_group_link_for_account
from features.profiles.get_single_profile import get_single_profile, ProfileRateLimitError
from features.accounts.account_manager import (
    load_accounts,
    create_account,
    open_account,
    delete_account,
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
from features.groups.group_copy_manager import (
    create_job as create_group_copy_job,
    list_jobs as list_group_copy_jobs,
    get_job as get_group_copy_job,
    cancel_job as cancel_group_copy_job,
    resume_job as resume_group_copy_job,
    request_verification as request_group_copy_verification,
    delete_job as delete_group_copy_job,
    mutate_job as save_group_copy_job_mutation,
)
from features.groups.group_copy_worker import start_group_copy_worker
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
from features.messages.unread_manager import (
    list_unread_messages,
    dismiss_unread_message,
    dismiss_group_messages,
    sync_unread_messages,
    get_group_latest_messages,
    start_unread_worker,
)
from core.zalo.zalo_config import get_zpw_ver


# ─── Friend request planning storage ─────────────────────────────────────────
FRIEND_PLANS_FILE = os.path.join(app_root, "data", "friend_invite_plans.json")
INVITE_GROUP_PLANS_FILE = os.path.join(app_root, "data", "group_invite_plans.json")
USER_POLICY_FILE = os.path.join(app_root, "data", "user_policy_acceptance.json")
USER_POLICY_VERSION = "2026-07-28-nexus-masterise-v8-compact-session"
VERSION_FILE = os.path.join(app_root, "VERSION")
APP_VERSION = "1.2.6"
UPDATE_REPO = "AnhTuan2003ml/mkt_zalo"
UPDATE_ASSET_NAME = "Nexus.zip"
UPDATE_HASH_ASSET_NAME = UPDATE_ASSET_NAME + ".sha256"
UPDATE_EXE_NAME = "Nexus.exe"


def _read_app_version():
    try:
        if os.path.isfile(VERSION_FILE):
            with open(VERSION_FILE, "r", encoding="utf-8") as f:
                version = f.read().strip()
                if version:
                    return version.lstrip("v")
    except Exception as e:
        print("[updater] read VERSION error=", e)
    return APP_VERSION


def _parse_version_parts(version):
    return [int(x) for x in re.findall(r"\d+", str(version or ""))]


def _is_newer_version(latest, current):
    latest_parts = _parse_version_parts(latest)
    current_parts = _parse_version_parts(current)
    size = max(len(latest_parts), len(current_parts), 1)
    latest_parts += [0] * (size - len(latest_parts))
    current_parts += [0] * (size - len(current_parts))
    return latest_parts > current_parts


def _github_headers():
    return {
        "Accept": "application/vnd.github+json",
        "User-Agent": "Nexus-Updater",
    }


# Gọi thẳng không qua system proxy: các tool bắt gói (đặt proxy 127.0.0.1:xxxx
# toàn hệ thống) sẽ làm SSL verification thất bại khi kiểm tra cập nhật.
_UPDATE_NO_PROXY = {"http": None, "https": None}


def _fetch_latest_release():
    url = f"https://api.github.com/repos/{UPDATE_REPO}/releases/latest"
    res = requests.get(url, headers=_github_headers(), timeout=20, proxies=_UPDATE_NO_PROXY)
    res.raise_for_status()
    release = res.json()
    tag_name = str(release.get("tag_name") or "").strip()
    latest_version = tag_name.lstrip("v")
    assets = release.get("assets") or []
    asset = next((a for a in assets if str(a.get("name") or "").lower() == UPDATE_ASSET_NAME.lower()), None)
    hash_asset = next((a for a in assets if str(a.get("name") or "").lower() == UPDATE_HASH_ASSET_NAME.lower()), None)
    if not latest_version:
        raise ValueError("Release mới nhất không có tag phiên bản.")
    if not asset or not asset.get("browser_download_url"):
        raise ValueError(f"Không tìm thấy asset {UPDATE_ASSET_NAME} trong release {tag_name}.")
    return {
        "version": latest_version,
        "downloadUrl": asset.get("browser_download_url"),
        "size": asset.get("size") or 0,
        "digest": asset.get("digest") or "",
        "hashUrl": hash_asset.get("browser_download_url") if hash_asset else "",
    }


def _download_update_zip(download_url, target_zip):
    part_path = target_zip + ".part"
    if os.path.exists(part_path):
        os.remove(part_path)
    with requests.get(download_url, headers=_github_headers(), timeout=60, stream=True, proxies=_UPDATE_NO_PROXY) as res:
        res.raise_for_status()
        with open(part_path, "wb") as f:
            for chunk in res.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
    os.replace(part_path, target_zip)


def _download_text(url):
    if not url:
        return ""
    res = requests.get(url, headers=_github_headers(), timeout=20, proxies=_UPDATE_NO_PROXY)
    res.raise_for_status()
    return res.text


def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _extract_sha256(value):
    if not value:
        return ""
    match = re.search(r"\b[a-fA-F0-9]{64}\b", str(value))
    return match.group(0).lower() if match else ""


def _verify_update_hash(update_zip, release):
    expected = _extract_sha256(release.get("digest"))
    if not expected and release.get("hashUrl"):
        expected = _extract_sha256(_download_text(release["hashUrl"]))
    if not expected:
        return
    actual = _sha256_file(update_zip)
    if actual.lower() != expected:
        raise ValueError("SHA-256 của gói cập nhật không khớp release.")


def _get_update_dir():
    return os.path.join(app_root, "update")


def _validate_update_zip(update_zip, expected_size=0):
    if not os.path.isfile(update_zip) or os.path.getsize(update_zip) < 1024 * 1024:
        raise ValueError("Gói cập nhật không hợp lệ hoặc quá nhỏ.")
    if expected_size and os.path.getsize(update_zip) != int(expected_size):
        raise ValueError("Kích thước gói cập nhật không khớp release.")
    with zipfile.ZipFile(update_zip, "r") as zf:
        exe_members = []
        for info in zf.infolist():
            normalized = info.filename.replace("\\", "/")
            if normalized.startswith("/") or ".." in normalized.split("/"):
                raise ValueError("Gói cập nhật chứa đường dẫn không an toàn.")
            if os.path.basename(normalized).lower() == UPDATE_EXE_NAME.lower():
                exe_members.append(info)
        if not exe_members:
            raise ValueError("Nexus.zip không chứa Nexus.exe.")

def _install_zip_update(update_zip, version):
    install_dir = app_root
    if getattr(sys, "frozen", False):
        install_dir = os.path.dirname(sys.executable)

    os.makedirs(install_dir, exist_ok=True)

    update_dir = os.path.dirname(update_zip)

    script_path = os.path.join(tempfile.gettempdir(), "_nexus_apply_update.bat")
    wait_pid = os.getpid() if getattr(sys, "frozen", False) else 0
    script = r'''@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul

set "PID_TO_WAIT=%~1"
set "ZIP_PATH=%~2"
set "INSTALL_DIR=%~3"
set "UPDATE_DIR=%~4"
set "VERSION_VALUE=%~5"
set "STAGE_DIR=%UPDATE_DIR%\stage"
set "RUN_EXE=%INSTALL_DIR%\Nexus.exe"
set "NEW_EXE=%INSTALL_DIR%\Nexus.exe.new"

if not "%PID_TO_WAIT%"=="0" (
    powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "try { Wait-Process -Id %PID_TO_WAIT% -Timeout 45 -ErrorAction SilentlyContinue } catch {}" >nul 2>&1
)

timeout /t 1 /nobreak >nul

if not exist "%ZIP_PATH%" goto :fail

if exist "%STAGE_DIR%" rmdir /s /q "%STAGE_DIR%"
mkdir "%STAGE_DIR%" || goto :fail

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Expand-Archive -LiteralPath $env:ZIP_PATH -DestinationPath $env:STAGE_DIR -Force" >nul 2>&1
if errorlevel 1 goto :fail

set "EXE_PATH=%STAGE_DIR%\Nexus.exe"
if not exist "%EXE_PATH%" (
    for /r "%STAGE_DIR%" %%F in (Nexus.exe) do (
        set "EXE_PATH=%%~fF"
        goto :found_exe
    )
)
:found_exe
if not exist "%EXE_PATH%" goto :fail

if exist "%NEW_EXE%" del /f /q "%NEW_EXE%"
copy /y "%EXE_PATH%" "%NEW_EXE%" >nul 2>&1 || goto :fail

move /y "%NEW_EXE%" "%RUN_EXE%" >nul 2>&1 || (
    if exist "%NEW_EXE%" del /f /q "%NEW_EXE%"
    goto :fail
)

> "%INSTALL_DIR%\VERSION" echo %VERSION_VALUE%
set "PYINSTALLER_RESET_ENVIRONMENT=1"
start "" /d "%INSTALL_DIR%" "%RUN_EXE%"

timeout /t 1 /nobreak >nul
if exist "%UPDATE_DIR%" rmdir /s /q "%UPDATE_DIR%"
del /f /q "%~f0"
exit /b 0

:fail
if exist "%NEW_EXE%" del /f /q "%NEW_EXE%"
if exist "%RUN_EXE%" (
    set "PYINSTALLER_RESET_ENVIRONMENT=1"
    start "" /d "%INSTALL_DIR%" "%RUN_EXE%"
)
del /f /q "%~f0"
exit /b 1
'''
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(script)

    command = [
        "cmd.exe",
        "/d",
        "/c",
        "call",
        script_path,
        str(wait_pid),
        update_zip,
        install_dir,
        update_dir,
        str(version).lstrip("v"),
    ]
    if not getattr(sys, "frozen", False):
        subprocess.check_call(command, cwd=install_dir)
        return {
            "state": "done",
            "message": "Đã giải nén gói cập nhật và chạy lại Nexus.exe.",
        }

    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    creationflags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    creationflags |= getattr(subprocess, "DETACHED_PROCESS", 0)
    subprocess.Popen(command, cwd=install_dir, creationflags=creationflags, close_fds=True)

    def _exit_for_update():
        os._exit(0)

    threading.Timer(1.2, _exit_for_update).start()
    return {
        "state": "restart",
        "message": "Đã tải bản cập nhật. Ứng dụng sẽ tự giải nén, xóa gói tải về và chạy lại Nexus.exe.",
    }


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

# ─── Danh sách gói kích hoạt (xác thực thực tế qua máy chủ license) ───────────
try:
    from authencation.activation_plans import get_activation_duration_options
    DEVICE_TRACKING_ENABLED = True
except Exception as e:
    print(f"⚠️  Activation options unavailable: {str(e)}")
    DEVICE_TRACKING_ENABLED = False

    def get_activation_duration_options():
        return [
            {"key": "1m", "label": "1 Tháng", "icon": "📊"},
            {"key": "3m", "label": "3 Tháng", "icon": "📊"},
            {"key": "6m", "label": "6 Tháng", "icon": "📊"},
            {"key": "lifetime", "label": "Vĩnh viễn", "icon": "💎"},
            {"key": "enterprise", "label": "Doanh nghiệp (10 máy)", "icon": "🏢"},
        ]

# ─── Global log queue for SSE streaming ───────────────────────────────────────
_log_queue = queue.Queue()
_sse_clients = []

def sse_broadcast(msg, msg_type="info"):
    """Broadcast message to all SSE clients and persist it in Nexus.log."""
    _append_activity_log(msg, msg_type)
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

# ─── User policy + activation gates ───────────────────────────────────────────
SCHEDULE_WORKER_STARTED = False
SCHEDULE_WORKER_LOCK = threading.Lock()
POLICY_ACCEPTED_THIS_SESSION = False


def _load_policy_acceptance():
    try:
        if not os.path.isfile(USER_POLICY_FILE):
            return {}
        with open(USER_POLICY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _policy_is_accepted():
    # Bắt buộc xác nhận lại sau mỗi lần mở ứng dụng. File chỉ lưu lịch sử
    # phiên bản/thời điểm đã đồng ý, không được dùng để bỏ qua màn chính sách.
    return bool(POLICY_ACCEPTED_THIS_SESSION)


def _save_policy_acceptance():
    global POLICY_ACCEPTED_THIS_SESSION
    os.makedirs(os.path.dirname(USER_POLICY_FILE), exist_ok=True)
    payload = {
        "accepted": True,
        "version": USER_POLICY_VERSION,
        "acceptedAt": datetime.now().isoformat(timespec="seconds"),
    }
    temp_path = USER_POLICY_FILE + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(temp_path, USER_POLICY_FILE)
    POLICY_ACCEPTED_THIS_SESSION = True
    return payload


def ensure_schedule_worker_started():
    """Khởi động các worker một lần sau khi đã đồng ý chính sách và kích hoạt."""
    global SCHEDULE_WORKER_STARTED
    if not _policy_is_accepted():
        print("📜 Chưa đồng ý chính sách người dùng, worker chưa được khởi động.", flush=True)
        return False
    with SCHEDULE_WORKER_LOCK:
        if SCHEDULE_WORKER_STARTED:
            return False
        start_schedule_worker()
        start_group_copy_worker()
        start_unread_worker()
        SCHEDULE_WORKER_STARTED = True
        print("✅ Schedule worker, group-copy worker và unread worker đã khởi động", flush=True)
        return True


def _activation_status_payload():
    # Trạng thái kích hoạt lấy TỪ MÁY CHỦ license.
    try:
        from authencation.server_license import get_server_plan
        plan = get_server_plan()
        activated = bool(plan.get("activated"))
        if activated:
            label = plan.get("planLabel") or plan.get("planKey") or ""
            if plan.get("isPermanent"):
                msg = f"✅ License {label} hợp lệ".strip()
            else:
                msg = f"✅ License {label} còn {plan.get('daysRemaining', 0)} ngày".strip()
        else:
            msg = "🔐 Chưa kích hoạt. Gửi thông tin máy lên máy chủ để nhận key qua email."
            if plan.get("reason"):
                msg += f" ({plan.get('reason')})"
        return {
            "success": True,
            "activated": activated,
            "days_remaining": int(plan.get("daysRemaining") or 0),
            "message": msg,
        }
    except Exception as e:
        return {"success": True, "activated": False, "days_remaining": 0,
                "message": f"Lỗi kiểm tra kích hoạt qua máy chủ: {e}"}


@app.before_request
def require_activation_before_use():
    """Chặn giao diện/API cho đến khi đồng ý chính sách và kích hoạt hợp lệ."""
    path = request.path or ""
    endpoint = request.endpoint or ""
    policy_endpoints = {"policy_page", "api_policy_status", "api_policy_accept", "static"}

    if endpoint in policy_endpoints or path.startswith("/static/") or path.startswith("/api/policy/"):
        return None

    if not _policy_is_accepted():
        if path.startswith("/api/") or request.accept_mimetypes.best == "application/json":
            return jsonify({
                "success": False,
                "policy_required": True,
                "error": "Bạn cần đồng ý chính sách người dùng trước khi tiếp tục.",
            }), 403
        return redirect(url_for("policy_page"))

    if not DEVICE_TRACKING_ENABLED:
        ensure_schedule_worker_started()
        return None

    allowed_endpoints = {
        "activation_page",
        "api_activation_status",
        "api_activation_save",
        "api_activation_resend",
        "api_device_register",
        "api_device_log",
        "api_device_mac",
        "static",
    }

    if endpoint in allowed_endpoints or path.startswith("/api/activation/"):
        return None

    status = _activation_status_payload()
    if status.get("activated"):
        ensure_schedule_worker_started()
        return None

    if path.startswith("/api/") or request.accept_mimetypes.best == "application/json":
        return jsonify({
            "success": False,
            "activation_required": True,
            "error": "Phần mềm chưa được kích hoạt",
            "message": status.get("message", "Vui lòng nhập mã kích hoạt."),
        }), 403

    return redirect(url_for("activation_page"))


@app.route("/policy", methods=["GET"])
def policy_page():
    accepted = _policy_is_accepted()
    return render_template(
        "policy.html",
        policy_version=USER_POLICY_VERSION,
        accepted=accepted,
    )


@app.route("/api/policy/status", methods=["GET"])
def api_policy_status():
    data = _load_policy_acceptance()
    return jsonify({
        "success": True,
        "accepted": _policy_is_accepted(),
        "version": USER_POLICY_VERSION,
        "acceptedAt": data.get("acceptedAt", ""),
    })


@app.route("/api/policy/accept", methods=["POST"])
def api_policy_accept():
    data = request.get_json(silent=True) or request.form or {}
    confirmed = bool(data.get("confirmed") or data.get("accepted") or data.get("agree"))
    if not confirmed:
        return jsonify({
            "success": False,
            "error": "Vui lòng tích xác nhận đã đọc và đồng ý chính sách.",
        }), 400

    acceptance = _save_policy_acceptance()
    activation = _activation_status_payload() if DEVICE_TRACKING_ENABLED else {"activated": True}
    if activation.get("activated"):
        ensure_schedule_worker_started()
    next_url = (url_for("guide_page") + "?welcome=1") if activation.get("activated") else url_for("activation_page")
    return jsonify({
        "success": True,
        "message": "Đã ghi nhận đồng ý chính sách người dùng.",
        "acceptance": acceptance,
        "nextUrl": next_url,
    })


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


def _server_license_mode():
    """True nếu đã cấu hình LICENSE_SERVER_URL (xác thực qua máy chủ)."""
    try:
        from authencation.server_license import is_server_mode
        return is_server_mode()
    except Exception:
        return False


def _current_plan():
    # Xác thực license HOÀN TOÀN qua máy chủ (không còn cấp phép offline).
    try:
        from authencation.server_license import get_server_plan
        return get_server_plan()
    except Exception:
        # Không đọc được -> coi như chưa kích hoạt (khóa an toàn, gói cơ bản).
        return {"activated": False, "planKey": "", "planLabel": "", "isPermanent": False,
                "daysRemaining": 0, "maxAccounts": 2, "multiAccountExec": False}


def _license_watchdog():
    """Định kỳ cập nhật license_gate để KHÓA CỨNG: khi license bị hủy/hết hạn,
    các worker nền (gửi lịch, sao chép nhóm, quét tin) sẽ tự tạm dừng."""
    import time as _t
    from authencation.license_gate import set_active
    while True:
        try:
            if not DEVICE_TRACKING_ENABLED or not _server_license_mode():
                set_active(True, "")            # không cấu hình license -> không chặn
            else:
                plan = _current_plan()
                set_active(bool(plan.get("activated")), str(plan.get("reason") or ""))
        except Exception:
            pass  # lỗi tạm thời -> giữ nguyên trạng thái trước, tránh khóa oan
        _t.sleep(60)


try:
    threading.Thread(target=_license_watchdog, daemon=True, name="license-watchdog").start()
except Exception:
    pass


@app.route("/api/license/plan", methods=["GET"])
def api_license_plan():
    """Quyền tính năng theo gói license (giới hạn tài khoản, chọn nhiều TK)."""
    plan = _current_plan()
    try:
        plan["accountCount"] = len(load_accounts() or [])
    except Exception:
        plan["accountCount"] = 0
    return jsonify({"success": True, **plan})


@app.route("/api/device/mac", methods=["GET"])
def api_device_mac():
    """Trả MAC + tên máy + IP để hiển thị trên giao diện (click copy MAC)."""
    try:
        from authencation.server_license import get_machine_info
        return jsonify({"success": True, **get_machine_info()})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/activation/save", methods=["POST"])
def api_activation_save():
    data = request.get_json(silent=True) or request.form or {}
    code_text = (data.get("code") or data.get("activation_code") or "").strip()

    # Xác thực key với MÁY CHỦ license (client chỉ check, không tự cấp).
    try:
        from authencation.server_license import verify_with_server
        result = verify_with_server(code_text)
    except Exception as e:
        return jsonify({"success": False, "message": f"Lỗi xác thực máy chủ: {e}"}), 500
    ok = bool(result.get("valid"))
    message = "✅ Kích hoạt thành công!" if ok else (result.get("error") or "Key không hợp lệ.")
    status = _activation_status_payload()
    if ok:
        ensure_schedule_worker_started()
    return jsonify({
        "success": ok,
        "message": message,
        "activated": bool(status.get("activated")),
        "days_remaining": status.get("days_remaining", 0),
        "status_message": status.get("message", ""),
    }), 200 if ok else 400


@app.route("/api/activation/resend", methods=["POST"])
def api_activation_resend():
    try:
        data = request.get_json(silent=True) or request.form or {}
        duration_key = (data.get("duration_key") or data.get("plan") or "3m").strip()
        options_map = {item["key"]: item for item in get_activation_duration_options()}
        selected = options_map.get(duration_key, options_map.get("3m", {"key": duration_key, "label": duration_key}))

        # Gửi thông tin máy lên MÁY CHỦ để server sinh key + gửi email admin.
        from authencation.server_license import register_with_server
        result = register_with_server(duration_key)
        sent = bool(result.get("success") and result.get("sent"))
        status = _activation_status_payload()
        return jsonify({
            "success": bool(result.get("success")),
            "sent": sent,
            "activated": bool(status.get("activated")),
            "selected_duration": selected,
            "message": result.get("message") or (
                "Đã gửi thông tin máy lên máy chủ. Kiểm tra email để lấy key."
                if sent else (result.get("error") or "Không gửi được yêu cầu tới máy chủ cấp phép.")
            ),
            "status_message": status.get("message", ""),
        }), (200 if result.get("success") else 400)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/")
def index():
    from flask import redirect
    return redirect(url_for("guide_page"))


@app.route("/dashboard")
def dashboard_page():
    return render_template("dashboard.html", active_page="dashboard")


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


@app.route("/friends")
def friends_page():
    return render_template("friends.html", active_page="friends")


@app.route("/api/friends", methods=["GET"])
def api_friends_list():
    """Danh sách bạn bè của tài khoản được chọn (API getfriends của Zalo Web)."""
    account_id = str(request.args.get("accountId") or request.args.get("account_id") or "").strip()
    if not account_id:
        return jsonify({"success": False, "error": "Thiếu accountId."}), 400
    account = get_account(account_id)
    if not account:
        return jsonify({"success": False, "error": "Không tìm thấy tài khoản."}), 404
    cookies = str(account.get("cookies") or "").strip()
    zpw_enk = str(account.get("zpwEnk") or "").strip()
    imei = str(account.get("imei") or "").strip()
    if not cookies or not zpw_enk:
        return jsonify({"success": False, "error": "Tài khoản chưa đủ cookies/zpwEnk. Hãy mở lại tài khoản ở trang Tài khoản."}), 400
    try:
        from features.friends.friend_service import get_friend_list
        friends = get_friend_list(zpw_enk, cookies, imei=imei)
    except Exception as exc:
        message = str(exc)
        if "429" in message:
            message = "Zalo đang giới hạn request (429). Hãy chờ 1-2 phút rồi bấm Làm mới."
        elif len(message) > 220:
            message = message[:220] + "..."
        return jsonify({"success": False, "error": f"Không lấy được danh sách bạn bè: {message}"}), 502
    return jsonify({"success": True, "friends": friends, "total": len(friends)})


@app.route("/api/friends/remove", methods=["POST"])
def api_friends_remove():
    """Xóa kết bạn với một UID theo tài khoản được chọn."""
    data = request.get_json(silent=True) or {}
    account_id = str(data.get("accountId") or data.get("account_id") or "").strip()
    user_id = str(data.get("userId") or data.get("uid") or "").strip()
    if not account_id or not user_id:
        return jsonify({"success": False, "error": "Thiếu accountId hoặc userId."}), 400
    account = get_account(account_id)
    if not account:
        return jsonify({"success": False, "error": "Không tìm thấy tài khoản."}), 404
    cookies = str(account.get("cookies") or "").strip()
    zpw_enk = str(account.get("zpwEnk") or "").strip()
    imei = str(account.get("imei") or "").strip()
    if not all([cookies, zpw_enk, imei]):
        return jsonify({"success": False, "error": "Tài khoản chưa đủ cookies/zpwEnk/IMEI."}), 400
    try:
        from features.messaging.remove_friend import remove_friend
        response_json, decoded = remove_friend(user_id, imei, zpw_enk, cookies, zpw_ver=get_zpw_ver())
    except Exception as exc:
        return jsonify({"success": False, "error": f"Không xóa được bạn bè: {exc}"}), 502

    outer_code = response_json.get("error_code", 0) if isinstance(response_json, dict) else -1
    inner_code = decoded.get("error_code", 0) if isinstance(decoded, dict) else 0
    ok = outer_code in (0, None) and inner_code in (0, None)
    if not ok:
        message = ""
        if isinstance(decoded, dict):
            message = str(decoded.get("error_message") or "")
        if not message and isinstance(response_json, dict):
            message = str(response_json.get("error_message") or "")
        return jsonify({"success": False, "error": message or f"Zalo trả lỗi (code {outer_code}/{inner_code})."}), 502
    return jsonify({"success": True, "message": "Đã xóa kết bạn."})


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
    return render_template("schedule_monitor.html", active_page="schedules", page_title="Lịch gửi")


@app.route("/marketing/group-copy")
def marketing_group_copy_page():
    return render_template("group_copy.html", active_page="group_copy")


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
    """API: quét tin ghim (board pin) của các nhóm để cập nhật tin chưa đọc."""
    try:
        data = request.get_json(silent=True) or request.form or {}
        account_id = (data.get("account_id") or "").strip()
        result = sync_unread_messages(account_id=account_id or None)
        settings = log_message_check_attempt(account_id=account_id, message=result.get("message", ""))
        return jsonify({
            "success": True,
            "synced_count": result.get("newCount", 0),
            "result": result,
            "settings": settings,
            "message": result.get("message", ""),
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/messages/unread", methods=["GET"])
def api_messages_unread_list():
    """API: danh sách tin ghim chưa đọc đã lưu trong data/unread_messages.json."""
    try:
        account_id = (request.args.get("account_id") or "").strip() or None
        payload = list_unread_messages(account_id=account_id)
        return jsonify({"success": True, **payload})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/messages/reply", methods=["POST"])
def api_messages_reply():
    """API: trả lời (quote) một tin nhắn đã lưu trong danh sách."""
    data = request.get_json(silent=True) or {}
    item_id = str(data.get("itemId") or "").strip()
    message = str(data.get("message") or "").strip()
    if not item_id:
        return jsonify({"success": False, "error": "Thiếu itemId."}), 400
    if not message:
        return jsonify({"success": False, "error": "Nội dung trả lời không được để trống."}), 400

    payload = list_unread_messages()
    item = next((x for x in payload.get("items", []) if x.get("id") == item_id), None)
    if not item:
        return jsonify({"success": False, "error": "Không tìm thấy tin nhắn để trả lời."}), 404

    account_id = str(item.get("accountId") or "").strip()
    account = get_account(account_id)
    if not account:
        return jsonify({"success": False, "error": "Không tìm thấy tài khoản."}), 404
    cookies = str(account.get("cookies") or "").strip()
    zpw_enk = str(account.get("zpwEnk") or "").strip()
    imei = str(account.get("imei") or "").strip()
    if not all([cookies, zpw_enk, imei]):
        return jsonify({"success": False, "error": "Tài khoản chưa đủ cookies/zpwEnk/IMEI."}), 400

    is_group = item.get("threadType") != "friend"
    try:
        from features.messaging.quote_message import quote_message
        result = quote_message(
            cookies, zpw_enk, imei,
            thread_id=item.get("groupId"),
            message=message,
            is_group=is_group,
            qmsg_owner=item.get("senderUid"),
            qmsg_id=item.get("globalMsgId") or item.get("pinId"),
            qmsg_cli_id=item.get("clientMsgId"),
            qmsg_type=item.get("msgType"),
            qmsg_ts=item.get("createTime"),
            qmsg_text=item.get("title"),
            zpw_ver=get_zpw_ver(),
        )
    except Exception as exc:
        return jsonify({"success": False, "error": f"Không gửi được trả lời: {exc}"}), 502
    if not result.get("ok"):
        return jsonify({"success": False, "error": result.get("message") or "Zalo từ chối gửi trả lời."}), 502
    return jsonify({"success": True, "message": "Đã gửi trả lời."})


@app.route("/api/messages/unread/<path:item_id>", methods=["DELETE"])
def api_messages_unread_delete(item_id):
    """API: xóa một tin đã xem khỏi db; lần quét sau không thêm lại."""
    try:
        if not dismiss_unread_message(item_id):
            return jsonify({"success": False, "error": "Không tìm thấy tin nhắn"}), 404
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/messages/unread/group/<group_id>", methods=["DELETE"])
def api_messages_unread_delete_group(group_id):
    """API: xóa toàn bộ tin của một nhóm khỏi db (đã xem hết)."""
    try:
        account_id = (request.args.get("account_id") or "").strip()
        removed = dismiss_group_messages(account_id, group_id)
        return jsonify({"success": True, "removed": removed})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/messages/group-latest", methods=["GET", "POST"])
def api_messages_group_latest():
    """API tích hợp: lấy tin nhắn ghim mới nhất của một nhóm truyền vào.

    GET  /api/messages/group-latest?groupId=...&account_id=...
    POST /api/messages/group-latest  {"groupId": "...", "account_id": "..."}
    """
    try:
        if request.method == "POST":
            data = request.get_json(silent=True) or request.form or {}
        else:
            data = request.args
        group_id = (data.get("groupId") or data.get("group_id") or "").strip()
        # Ưu tiên profileId (uid Zalo — ổn định); vẫn nhận accountId để tương thích.
        account_ref = (data.get("profileId") or data.get("profile_id")
                       or data.get("account_id") or data.get("accountId") or "").strip()
        payload = get_group_latest_messages(group_id, account_ref or None)
        return jsonify({"success": True, **payload})
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 502


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
    """API: Thêm tài khoản mới. Chỉ mở Chrome khi autoLaunch=true (mặc định false)."""
    data = request.get_json(silent=True) or {}
    name = str(data.get("name") or "").strip()
    proxy = str(data.get("proxy") or "").strip()
    auto_launch = bool(data.get("autoLaunch", False))
    # Giới hạn số tài khoản đăng nhập theo gói license.
    plan = _current_plan()
    max_accounts = int(plan.get("maxAccounts") or 0)
    if max_accounts > 0:
        try:
            current = len(load_accounts() or [])
        except Exception:
            current = 0
        if current >= max_accounts:
            return jsonify({
                "success": False,
                "error": f"Gói hiện tại chỉ cho phép đăng nhập tối đa {max_accounts} tài khoản Zalo. "
                         f"Nâng lên gói 6 tháng trở lên để đăng nhập không giới hạn tài khoản.",
                "limitReached": True,
                "maxAccounts": max_accounts,
            }), 403
    try:
        account = create_account(name=name or None, proxy=proxy, auto_launch=auto_launch)
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
        fields = {}

        if "name" in data:
            name = str(data.get("name") or "").strip()
            if not name:
                return jsonify({"error": "Tên không được để trống."}), 400
            fields["name"] = name

        if "proxy" in data:
            fields["proxy"] = str(data.get("proxy") or "").strip()

        if not fields:
            return jsonify({"error": "Không có dữ liệu hợp lệ để cập nhật."}), 400

        account = update_account(account_id, **fields)
        if not account:
            return jsonify({"error": "Không tìm thấy tài khoản."}), 404
        return jsonify({"success": True, "account": account})

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




def _cookie_has_name(cookie_str: str, cookie_name: str) -> bool:
    target = str(cookie_name or "").strip().lower()
    if not target:
        return False
    for item in str(cookie_str or "").split(";"):
        item = item.strip()
        if "=" not in item:
            continue
        name, value = item.split("=", 1)
        if name.strip().lower() == target and value.strip():
            return True
    return False


def _refresh_account_cookies_if_possible(account_id: str, account: dict, timeout: float = 7.0) -> bool:
    port = (account or {}).get("remoteDebugPort")
    if not port:
        return False
    try:
        from features.accounts.account_network_monitor import refresh_account_cookies
        return bool(refresh_account_cookies(account_id, int(port), timeout=timeout))
    except Exception as error:
        print(f"[session_refresh] Không đọc lại được cookies account={account_id}: {error}")
        return False


def _is_zalo_session_error(error) -> bool:
    message = str(error or "").lower()
    return any(marker in message for marker in (
        "zpw_sek",
        "zpw enk",
        "zpwenk",
        "cookie", 
        "phiên đăng nhập",
        "session",
    ))


def _refresh_account_session_blocking(account_id: str, timeout: float = 18.0) -> bool:
    """Tự bắt lại zpwEnk + zpw_sek từ Chrome đang mở và chờ tối đa timeout.

    Chỉ dùng sau khi API Zalo xác nhận session hiện tại không hợp lệ.
    """
    account = get_account(account_id)
    if not account:
        return False
    port = account.get("remoteDebugPort")
    if not port:
        return False

    try:
        if not _chrome_debug_port_alive(port):
            return False
    except Exception:
        return False

    refresh_started_at = int(time.time() * 1000)
    # Không xóa cookies/zpwEnk đang lưu. Monitor sẽ thay thế từng giá trị sau
    # khi bắt được bộ phiên mới hợp lệ; nếu quá trình bắt lại thất bại, dữ liệu
    # cũ vẫn còn để người dùng không bị mất phiên vì một lần bấm Làm mới.
    update_account(
        account_id,
        loginCaptured=False,
        sessionRefreshStartedAt=refresh_started_at,
    )

    try:
        from features.accounts.account_network_monitor import start_account_network_monitor
        start_account_network_monitor(account_id, int(port))
    except Exception as error:
        print(f"[session_refresh] Không khởi động được monitor account={account_id}: {error}")
        return False

    deadline = time.time() + max(5.0, float(timeout or 0))
    while time.time() < deadline:
        current = get_account(account_id) or {}
        cookies = current.get("cookies") or ""
        captured_at = int(current.get("sessionCapturedAt") or 0)
        if (
            current.get("loginCaptured")
            and str(current.get("zpwEnk") or "").strip()
            and _cookie_has_name(cookies, "zpw_sek")
            and captured_at >= refresh_started_at
        ):
            print(f"[session_refresh] Đã khôi phục session account={account_id}")
            return True
        time.sleep(0.35)

    print(f"[session_refresh] Hết thời gian chờ session account={account_id}")
    return False


def _get_account_credentials(account_id: str, require_imei: bool = False):
    """Lấy bộ session đồng nhất của tài khoản đang chọn.

    getmg cần cả zpwEnk và cookie zpw_sek cùng một phiên. Cookie dài hoặc có
    zpsid/__zi nhưng thiếu zpw_sek vẫn không dùng được.
    """
    account_id = (account_id or "").strip()
    if not account_id:
        raise ValueError("Chưa chọn tài khoản.")

    account = get_account(account_id)
    if not account:
        raise ValueError("Không tìm thấy tài khoản.")

    cookies = (account.get("cookies") or "").strip()
    if not _cookie_has_name(cookies, "zpw_sek"):
        # Tự đọc lại cookie HttpOnly từ Chrome đang mở trước khi báo lỗi cho UI.
        _refresh_account_cookies_if_possible(account_id, account)
        account = get_account(account_id) or account
        cookies = (account.get("cookies") or "").strip()

    zpw_enk = (account.get("zpwEnk") or "").strip()
    imei = (account.get("imei") or "").strip()

    missing = []
    if not account.get("loginCaptured"):
        missing.append("phiên đăng nhập chưa hoàn tất")
    if not zpw_enk:
        missing.append("zpwEnk")
    if not _cookie_has_name(cookies, "zpw_sek"):
        missing.append("cookie zpw_sek")
    if require_imei and not imei:
        missing.append("imei")

    if missing:
        raise ValueError(
            "Tài khoản chưa đủ phiên xác thực: " + ", ".join(missing) +
            ". Hãy mở lại tài khoản Zalo, chờ trạng thái Sẵn sàng rồi thao tác lại."
        )

    return account, cookies, zpw_enk, imei


def _emit_session_status(callback, message: str, kind: str = "loading"):
    if not callback:
        return
    try:
        callback(message, kind)
    except TypeError:
        callback(message)
    except Exception:
        pass


def _fetch_group_members_session_safe(
    account_id: str,
    group_input: str,
    callback=None,
    require_imei: bool = False,
    auto_join_when_not_member: bool = True,
    leave_after_auto_join: bool = True,
):
    """Gọi getmg với một lần tự khôi phục session khi zpw_sek/zpwEnk lệch phiên."""
    account, cookies, zpw_enk, imei = _get_account_credentials(
        account_id, require_imei=require_imei
    )
    zpw_ver = get_zpw_ver()

    for attempt in range(2):
        try:
            payload = fetch_group_members_by_input(
                group_input,
                zpw_enk,
                cookies,
                callback=callback,
                zpw_ver=zpw_ver,
                imei=imei,
                auto_join_when_not_member=auto_join_when_not_member,
                leave_after_auto_join=leave_after_auto_join,
            )
            return payload, account, cookies, zpw_enk, imei, zpw_ver
        except ValueError as error:
            if attempt > 0 or not _is_zalo_session_error(error):
                raise
            _emit_session_status(
                callback,
                "Phiên Zalo chưa đồng nhất. Đang bắt lại zpwEnk và zpw_sek...",
                "loading",
            )
            if not _refresh_account_session_blocking(account_id):
                raise ValueError(
                    "Phiên Zalo đã hết hạn hoặc thiếu zpw_sek. Hãy mở tài khoản Zalo, "
                    "đợi trang tải xong rồi thao tác lại."
                ) from error
            account, cookies, zpw_enk, imei = _get_account_credentials(
                account_id, require_imei=require_imei
            )
            zpw_ver = get_zpw_ver()

    raise ValueError("Không thể khôi phục phiên Zalo.")


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
        member_payload, account, cookies, zpw_enk, imei, zpw_ver = _fetch_group_members_session_safe(
            account_id,
            raw,
            callback=sse_broadcast,
            require_imei=False,
            auto_join_when_not_member=auto_join_when_not_member,
            leave_after_auto_join=False,
        )

        uid_list = member_payload["uidList"]
        group_info = member_payload["groupInfo"]
        group_id = member_payload["groupId"]
        member_map = member_payload.get("memberMap") or {}

        sse_broadcast(f"Lấy được {len(uid_list)} UID. Đang lấy thông tin profile...", "loading")
        auto_joined = bool(member_payload.get("autoJoined"))
        auto_left = False
        leave_result = None

        try:
            profiles = fetch_profiles_with_single_fallback(
                uid_list,
                zpw_enk,
                cookies,
                imei=imei,
                log_func=sse_broadcast,
                zpw_ver=zpw_ver,
            )
            result = build_member_rows_from_uids(uid_list, profiles, member_map=member_map, group_info=group_info)
        finally:
            # Nếu hệ thống tự join ngầm thì LUÔN rời nhóm sau khi đã có UID,
            # kể cả khi lấy profile hoặc build bảng lỗi, để account không kẹt
            # lại trong nhóm. Rời sau bước profile để mini profile lấy đủ dữ liệu.
            if auto_joined and uid_list:
                try:
                    leave_result = _leave_group([group_id], imei=imei, zpw_enk=zpw_enk, cookies=cookies, zpw_ver=zpw_ver)
                    auto_left = bool(isinstance(leave_result, dict) and leave_result.get("ok"))
                except Exception as leave_error:
                    sse_broadcast(f"Không rời được nhóm đã tự tham gia: {leave_error}", "warn")

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

        # Nội dung chứa link nhóm Zalo -> tự lấy thông tin nhóm rồi gửi link card
        # (message/link); không có link hoặc lỗi -> gửi SMS text như cũ.
        from features.messaging.send_link import send_message_smart
        result = send_message_smart(
            to_uid, message, zpw_enk, cookies, imei,
            is_group=False, zpw_ver=get_zpw_ver(),
        )

        print("[api_send_sms] result=", result)

        if result.get("ok"):
            return jsonify({
                "success": True,
                "message": "Da gui tin nhan." + (" (link card)" if result.get("sentAsLink") else ""),
                "sentAsLink": bool(result.get("sentAsLink")),
                "data": result.get("decoded"),
            })

        return jsonify({
            "success": False,
            "error": "Loi gui SMS tu Zalo.",
            "message": result.get("error", ""),
            "detail": result.get("decoded"),
        }), 400

    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/send-photo", methods=["POST"])
def api_send_photo():
    """API: Gửi ảnh (kèm chú thích) tới user ID theo account được chọn.

    Luồng 2 bước theo Zalo Web: upload chunk qua photo_original/upload để lấy
    photoId + URL, sau đó POST photo_original/send để tạo tin nhắn ảnh.
    Nhận multipart/form-data: accountId, to_uid, message (tùy chọn), photo (file).
    """
    account_id = str(request.form.get("accountId", request.form.get("account_id", "")) or "").strip()
    to_uid = str(
        request.form.get("to_uid")
        or request.form.get("toid")
        or request.form.get("toId")
        or request.form.get("uid")
        or request.form.get("userId")
        or ""
    ).strip()
    message = str(request.form.get("message", request.form.get("msg", "")) or "").strip()

    if to_uid.lower().startswith("id:"):
        to_uid = to_uid.split(":", 1)[1].strip()
    import re
    m = re.search(r"\d{8,}", to_uid)
    if m:
        to_uid = m.group(0)

    photo_file = request.files.get("photo") or request.files.get("image") or request.files.get("file")

    if not account_id:
        return jsonify({"success": False, "error": "Chưa chọn tài khoản gửi tin nhắn."}), 400
    if not to_uid:
        return jsonify({"success": False, "error": "Thiếu người nhận."}), 400
    if not photo_file:
        return jsonify({"success": False, "error": "Thiếu file ảnh cần gửi."}), 400

    image_bytes = photo_file.read()
    if not image_bytes:
        return jsonify({"success": False, "error": "File ảnh rỗng."}), 400
    if len(image_bytes) > 20 * 1024 * 1024:
        return jsonify({"success": False, "error": "Ảnh vượt quá 20MB."}), 400

    file_name = photo_file.filename or "image.jpg"

    try:
        _, cookies, zpw_enk, imei = _get_account_credentials(account_id, require_imei=True)

        from features.messaging.send_photo import send_photo as _send_photo
        # Gửi ảnh TRƯỚC (không caption), sau đó gửi text thành tin nhắn riêng.
        result = _send_photo(
            image_bytes,
            file_name,
            to_uid,
            zpw_enk,
            cookies,
            imei,
            desc="",
            is_group=False,
            zpw_ver=get_zpw_ver(),
        )

        if result.get("ok"):
            text_sent = False
            text_error = ""
            if message:
                try:
                    from features.messaging.send_link import send_message_smart
                    text_result = send_message_smart(
                        to_uid, message, zpw_enk, cookies, imei,
                        is_group=False, zpw_ver=get_zpw_ver(),
                    )
                    text_sent = bool(text_result.get("ok"))
                    if not text_sent:
                        text_error = f"Gửi text sau ảnh lỗi: {text_result.get('error')}"
                except Exception as text_exc:
                    text_error = f"Gửi text sau ảnh lỗi: {text_exc}"
            return jsonify({
                "success": True,
                "message": "Đã gửi ảnh." + (" Đã gửi tin nhắn." if text_sent else ""),
                "photoId": result.get("photoId"),
                "urls": result.get("urls"),
                "textSent": text_sent,
                "textError": text_error,
                "data": result.get("decoded"),
            })

        return jsonify({
            "success": False,
            "error": result.get("message", "Gửi ảnh thất bại."),
            "step": result.get("step"),
            "detail": result.get("decoded"),
            "raw": result.get("response"),
        }), 400

    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/send-group-message", methods=["POST"])
def api_send_group_message():
    """API: Gửi tin nhắn vào nhóm Zalo, có thể kèm ảnh.

    Nhận multipart/form-data: accountId, group_id, message (tùy chọn nếu có ảnh),
    photo (file, tùy chọn). Có ảnh -> photo_original/upload + send (nhóm),
    không ảnh -> /api/group/sendmsg như cũ.
    """
    # Ưu tiên profileId (uid Zalo — ổn định); Nexus tự tra accountId + phiên.
    account_ref = str(
        request.form.get("profileId")
        or request.form.get("profile_id")
        or request.form.get("accountId")
        or request.form.get("account_id")
        or ""
    ).strip()
    from features.accounts.account_manager import resolve_account_id
    account_id = resolve_account_id(account_ref) or account_ref
    group_id = str(
        request.form.get("group_id")
        or request.form.get("groupId")
        or request.form.get("grid")
        or ""
    ).strip()
    message = str(request.form.get("message", request.form.get("msg", "")) or "").strip()
    photo_file = request.files.get("photo") or request.files.get("image") or request.files.get("file")

    import re
    m = re.search(r"\d{8,}", group_id)
    if m:
        group_id = m.group(0)

    if not account_id:
        return jsonify({"success": False, "error": "Chưa chọn tài khoản gửi tin nhắn."}), 400
    if not group_id:
        return jsonify({"success": False, "error": "Thiếu ID nhóm nhận."}), 400
    if not message and not photo_file:
        return jsonify({"success": False, "error": "Thiếu nội dung tin nhắn hoặc ảnh."}), 400

    try:
        _, cookies, zpw_enk, imei = _get_account_credentials(account_id, require_imei=True)

        if photo_file:
            image_bytes = photo_file.read()
            if not image_bytes:
                return jsonify({"success": False, "error": "File ảnh rỗng."}), 400
            if len(image_bytes) > 20 * 1024 * 1024:
                return jsonify({"success": False, "error": "Ảnh vượt quá 20MB."}), 400

            from features.messaging.send_photo import send_photo as _send_photo
            # Gửi ảnh TRƯỚC (không caption), sau đó gửi text thành tin nhắn riêng.
            result = _send_photo(
                image_bytes,
                photo_file.filename or "image.jpg",
                group_id,
                zpw_enk,
                cookies,
                imei,
                desc="",
                is_group=True,
                zpw_ver=get_zpw_ver(),
            )
            if result.get("ok"):
                text_sent = False
                text_error = ""
                if message:
                    try:
                        from features.messaging.send_link import send_message_smart
                        text_result = send_message_smart(
                            group_id, message, zpw_enk, cookies, imei,
                            is_group=True, zpw_ver=get_zpw_ver(),
                        )
                        text_sent = bool(text_result.get("ok"))
                        if not text_sent:
                            text_error = f"Gửi text sau ảnh lỗi: {text_result.get('error')}"
                    except Exception as text_exc:
                        text_error = f"Gửi text sau ảnh lỗi: {text_exc}"
                return jsonify({
                    "success": True,
                    "message": "Đã gửi ảnh vào nhóm." + (" Đã gửi tin nhắn." if text_sent else ""),
                    "photoId": result.get("photoId"),
                    "urls": result.get("urls"),
                    "textSent": text_sent,
                    "textError": text_error,
                    "data": result.get("decoded"),
                })
            return jsonify({
                "success": False,
                "error": result.get("message", "Gửi ảnh vào nhóm thất bại."),
                "step": result.get("step"),
                "detail": result.get("decoded"),
            }), 400

        # Nội dung chứa link nhóm Zalo -> tự lấy thông tin nhóm rồi gửi link card
        # (group/sendlink); không có link hoặc lỗi -> gửi text nhóm như cũ.
        from features.messaging.send_link import send_message_smart
        result = send_message_smart(
            group_id, message, zpw_enk, cookies, imei,
            is_group=True, zpw_ver=get_zpw_ver(),
        )

        if result.get("ok"):
            return jsonify({
                "success": True,
                "message": "Đã gửi tin nhắn vào nhóm." + (" (link card)" if result.get("sentAsLink") else ""),
                "sentAsLink": bool(result.get("sentAsLink")),
                "data": result.get("decoded"),
            })

        return jsonify({
            "success": False,
            "error": result.get("error") or "Gửi tin nhắn nhóm thất bại.",
            "detail": result.get("decoded"),
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
        def task_log(msg, typ="info"):
            task.log(msg)

        task.log(f"Đang xử lý: {group_input}")
        task.set_progress(10)

        member_payload, account, cookies, zpw_enk, imei, zpw_ver = _fetch_group_members_session_safe(
            account_id,
            group_input,
            callback=task_log,
            require_imei=False,
            auto_join_when_not_member=auto_join_when_not_member,
            leave_after_auto_join=False,
        )
        uid_list = member_payload["uidList"]
        group_info = member_payload["groupInfo"]
        group_id = member_payload["groupId"]
        member_map = member_payload.get("memberMap") or {}

        task.set_progress(30)
        task.log(f"Lấy được {len(uid_list)} UID. Đang lấy profile...")
        auto_joined = bool(member_payload.get("autoJoined"))
        auto_left = False
        leave_result = None

        try:
            profiles = fetch_profiles_with_single_fallback(
                uid_list,
                zpw_enk,
                cookies,
                imei=imei,
                log_func=task_log,
                zpw_ver=zpw_ver,
            )
            task.set_progress(70)
            result = build_member_rows_from_uids(uid_list, profiles, member_map=member_map, group_info=group_info)
        finally:
            # Nếu tự join ngầm thì LUÔN rời nhóm sau khi đã có UID, kể cả khi
            # lấy profile hoặc build bảng lỗi, để account không kẹt lại trong nhóm.
            if auto_joined and uid_list:
                try:
                    leave_result = _leave_group([group_id], imei=imei, zpw_enk=zpw_enk, cookies=cookies, zpw_ver=zpw_ver)
                    auto_left = bool(isinstance(leave_result, dict) and leave_result.get("ok"))
                except Exception as leave_error:
                    task.log(f"Không rời được nhóm đã tự tham gia: {leave_error}")

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


def _prepare_group_copy_job_worker(task, payload: dict):
    """Lấy thành viên nhóm nguồn và lưu tác vụ sao chép theo hạn mức mỗi ngày."""
    from features.groups.group_copy_worker import _avatar_hash as _extract_avatar_hash

    account_id = str(payload.get("accountId") or "").strip()
    source_input = str(payload.get("sourceInput") or "").strip()
    source_input_type, normalized_source_input = normalize_group_input(source_input)
    # Link mời nhóm NGUỒN (nếu người dùng nhập bằng link) — cần để tài khoản phụ tự
    # vào nhóm nguồn và đọc lại thành viên nhằm lấy uid hợp lệ cho phiên của nó.
    source_group_link = ""
    for _cand in (payload.get("sourceGroupLink"), payload.get("sourceLink"), source_input):
        _c = str(_cand or "").strip()
        if _c.startswith("http") or "zalo.me" in _c:
            source_group_link = ("https:" + _c) if _c.startswith("//") else _c
            break
    source_group_id_hint = str(payload.get("sourceGroupId") or "").strip()
    if source_input_type == "group_id" and normalized_source_input:
        # Luôn truyền Group ID sạch vào getmg; không để nhãn ``ID:``, tiền tố g
        # hoặc khoảng trắng làm backend hiểu nhầm thành mã link mời.
        source_input = normalized_source_input
    elif not source_input and source_group_id_hint:
        hint_type, normalized_hint = normalize_group_input(source_group_id_hint)
        if hint_type == "group_id":
            source_input = normalized_hint
    task.set_progress(10)
    task.log("Đang đọc thông tin và danh sách thành viên nhóm nguồn...")
    member_payload, account, cookies, zpw_enk, imei, zpw_ver = _fetch_group_members_session_safe(
        account_id,
        source_input,
        callback=lambda msg, typ="info": task.log(msg, typ),
        require_imei=False,
        auto_join_when_not_member=True,
        leave_after_auto_join=False,   # A vào lấy thành viên xong Ở LẠI nhóm, không rời
    )
    uid_list = member_payload.get("uidList") or []
    member_map = member_payload.get("memberMap") or {}
    source_group_id = str(member_payload.get("groupId") or "").strip()
    source_group = format_group_info(
        member_payload.get("groupInfo") or {},
        group_id=source_group_id,
        fallback_total=len(uid_list),
    )

    target_group_id = str(payload.get("targetGroupId") or "").strip()
    if payload.get("targetMode") == "existing" and target_group_id == source_group_id:
        raise ValueError("Nhóm nguồn và nhóm đích không được trùng nhau.")

    task.set_progress(40)
    task.log(f"Đã lấy {len(uid_list)} UID. Đang bổ sung tên và ảnh đại diện...")
    profiles = fetch_profiles_with_single_fallback(
        uid_list,
        zpw_enk,
        cookies,
        imei=imei,
        log_func=lambda msg, typ="info": task.log(msg, typ),
        zpw_ver=zpw_ver,
    )
    members = build_member_rows_from_uids(uid_list, profiles, member_map=member_map, group_info=member_payload.get("groupInfo") or {})

    task.set_progress(58)
    task.log("Đang xác định thành viên đã là bạn bè để ưu tiên thêm vào nhóm trước...")
    friend_relations = fetch_friend_relations(
        uid_list,
        zpw_enk,
        cookies,
        imei=imei,
        log_func=lambda msg, typ="info": task.log(msg, typ),
        zpw_ver=zpw_ver,
    )

    account_uid = str(
        account.get("uid")
        or account.get("userId")
        or account.get("zaloId")
        or account.get("profileId")
        or ""
    ).strip()
    skip_leaders = bool(payload.get("skipLeaders"))
    skipped_leader_count = 0
    clean_members = []
    seen = set()
    for member in members:
        uid = str(member.get("userId") or member.get("id") or "").strip()
        if not uid or uid == account_uid or uid in seen:
            continue
        seen.add(uid)
        # Bật "Bỏ qua trưởng/phó nhóm": không đưa owner/admin của nhóm nguồn vào tác vụ.
        if skip_leaders and str(member.get("groupRole") or "") in ("owner", "admin"):
            skipped_leader_count += 1
            continue
        relation_value = friend_relations.get(uid)
        raw_friend = relation_value if relation_value is not None else member.get("isFr", member.get("isFriend"))
        friend_text = str(raw_friend).strip().lower()
        is_friend = True if raw_friend is True or friend_text in {"1", "true", "yes"} else (
            False if raw_friend is False or friend_text in {"0", "false", "no"} else None
        )
        _avatar_url = member.get("avatar") or ""
        clean_members.append({
            "userId": uid,
            "zaloName": member.get("zaloName") or member.get("displayName") or uid,
            "avatar": _avatar_url,
            # Định danh gốc ổn định (hash ảnh) — GIỐNG nhau giữa mọi tài khoản. Dùng để
            # tài khoản phụ tự phân giải uid RIÊNG của nó khi đọc lại nhóm nguồn.
            "avatarHash": _extract_avatar_hash(_avatar_url),
            # Quan hệ được dùng để ưu tiên thêm toàn bộ bạn bè trước.
            "isFriend": is_friend,
            "isFr": 1 if is_friend is True else (0 if is_friend is False else None),
        })

    if skipped_leader_count:
        task.log(f"Đã bỏ qua {skipped_leader_count} trưởng/phó nhóm của nhóm nguồn theo thiết lập.")
    if not clean_members:
        raise ValueError("Không tìm thấy thành viên hợp lệ trong nhóm nguồn.")

    task.set_progress(85)

    # Danh sách tài khoản thực hiện (đã lọc trùng ở route). Tài khoản đầu là CHỦ.
    account_ids = payload.get("accountIds") or [account_id]
    account_ids = [str(a).strip() for a in account_ids if str(a or "").strip()]
    if not account_ids:
        account_ids = [account_id]

    # Chia khối tuần tự: TK đầu nhận phần đầu danh sách, TK sau nhận phần tiếp theo.
    n = len(account_ids)
    k, m = divmod(len(clean_members), n)
    blocks = [
        clean_members[i * k + min(i, m):(i + 1) * k + min(i + 1, m)]
        for i in range(n)
    ]

    is_multi = n > 1
    if is_multi and not source_group_link:
        task.log(
            "⚠️ Nhóm nguồn đang nhập bằng ID nên các tài khoản phụ không thể tự đọc lại "
            "để lấy UID hợp lệ (UID Zalo mã hóa riêng từng tài khoản). Hãy nhập nhóm nguồn "
            "bằng LINK mời để chạy nhiều tài khoản chính xác.",
            "warn",
        )
    target_mode = str(payload.get("targetMode") or "existing").strip()

    # Nhiều tài khoản + tạo nhóm mới: TÀI KHOẢN CHÍNH tạo nhóm NGAY tại đây rồi
    # lấy link nhóm, để mọi tài khoản (chủ + phụ) cùng dùng chung một nhóm và
    # mời vào bằng link đó — thay vì mỗi tài khoản tự tạo một nhóm riêng.
    shared_target_id = str(payload.get("targetGroupId") or "").strip()
    shared_target_link = str(payload.get("targetGroupLink") or "").strip()
    shared_target_name = str(payload.get("newGroupName") or payload.get("targetGroupName") or "").strip()
    if is_multi and target_mode == "new":
        from features.groups.add_group import create_group as _create_group
        from features.groups.group_link import create_group_link as _create_group_link
        from features.groups.group_copy_worker import _extract_group_id, _decoded_dict, _error_message

        master_acc = get_account(account_ids[0]) or {}
        m_cookies = str(master_acc.get("cookies") or "").strip()
        m_enk = str(master_acc.get("zpwEnk") or "").strip()
        m_imei = str(master_acc.get("imei") or "").strip()
        group_name = shared_target_name or "Nhóm mới"
        # Ưu tiên bạn bè của khối tài khoản chính làm thành viên khởi tạo nhóm.
        seed_block = blocks[0] or clean_members
        seed_ids = [
            str(mem.get("userId") or "").strip()
            for mem in sorted(seed_block, key=lambda x: 0 if x.get("isFriend") is True else 1)
            if str(mem.get("userId") or "").strip()
        ][:50]
        task.log(f"Tài khoản chính đang tạo nhóm đích chung '{group_name}'...")
        resp_json, decoded_raw = _create_group(group_name, seed_ids, m_enk, m_cookies, m_imei, zpw_ver=zpw_ver)
        resp_json = resp_json if isinstance(resp_json, dict) else {}
        decoded = _decoded_dict(decoded_raw)
        shared_target_id = _extract_group_id(resp_json, decoded)
        if not shared_target_id:
            raise RuntimeError(_error_message(resp_json, decoded) or "Không tạo được nhóm đích chung bằng tài khoản chính.")
        # Lấy link nhóm để các tài khoản khác mời thành viên vào.
        try:
            link_result = _create_group_link(shared_target_id, m_imei, m_enk, m_cookies, zpw_ver=zpw_ver)
            shared_target_link = str((link_result or {}).get("link") or "").strip()
        except Exception as exc:
            shared_target_link = ""
            task.log(f"Đã tạo nhóm nhưng chưa lấy được link ngay: {exc}", "warn")
        task.log(f"Đã tạo nhóm đích chung (ID {shared_target_id})." + (f" Link: {shared_target_link}" if shared_target_link else ""))
        # Từ đây mọi job dùng nhóm CÓ SẴN này, không tạo nhóm mới nữa.
        target_mode = "existing"

    jobs_created = []
    # Cùng một chiến dịch (nhiều tài khoản) chung 1 campaignId để giao diện gom nhóm.
    campaign_id = "camp_" + uuid.uuid4().hex[:12]
    for idx, aid in enumerate(account_ids):
        block = blocks[idx]
        if not block:
            continue  # tài khoản không được chia thành viên nào (danh sách ngắn hơn số TK)
        acc = get_account(aid) or {}
        acc_name = acc.get("name") or acc.get("displayName") or aid
        acc_avatar = acc.get("avatarUrl") or acc.get("avatar") or ""

        sub = dict(payload)
        sub["accountId"] = aid
        sub.pop("accountIds", None)
        sub["campaignId"] = campaign_id
        # Link nhóm nguồn để tài khoản phụ tự đọc lại (lấy uid riêng hợp lệ).
        if source_group_link:
            sub["sourceGroupLink"] = source_group_link
        # Tài khoản CHÍNH (đầu danh sách) sở hữu nhóm đích: đọc/mời nhóm đích luôn
        # dùng tài khoản này, kể cả khi job đang chạy bằng tài khoản phụ.
        sub["targetOwnerAccountId"] = account_ids[0]
        if is_multi:
            sub["title"] = f"{payload.get('title') or 'Sao chép thành viên nhóm'} (TK {idx + 1}/{n} - {acc_name})"
            # Mọi tài khoản dùng chung nhóm đích đã tạo/đã chọn.
            sub["targetMode"] = target_mode
            if shared_target_id:
                sub["targetGroupId"] = shared_target_id
                sub["targetGroupLink"] = shared_target_link
                sub["targetGroupName"] = shared_target_name or sub.get("targetGroupName") or ""
            else:
                sub["targetGroupId"] = str(payload.get("targetGroupId") or "")
                sub["targetGroupLink"] = str(payload.get("targetGroupLink") or "")

        job = create_group_copy_job(sub, block, source_group, acc_name, account_avatar=acc_avatar)
        # Ghi cờ nhiều tài khoản để hiển thị/quản lý.
        if is_multi:
            def _tag(j, midx=idx):
                j["multiAccount"] = True
                j["multiAccountIndex"] = midx
                j["multiAccountTotal"] = n
            save_group_copy_job_mutation(job.get("jobId"), _tag)
        jobs_created.append(job)

    task.log(
        f"Đã lập lịch {len(clean_members)} thành viên"
        + (f" chia cho {n} tài khoản (mỗi tài khoản một khối liên tiếp, không trùng)." if is_multi else ".")
        + f" Mỗi tài khoản gửi tối đa {payload.get('friendRequestDailyLimit', payload.get('dailyLimit'))} lời mời kết bạn/ngày."
    )
    first_job = jobs_created[0] if jobs_created else {}
    return {
        "jobs": [{key: value for key, value in j.items() if key != "members"} for j in jobs_created],
        "jobCount": len(jobs_created),
        "accountCount": n,
        "sourceGroup": source_group,
        "totalMembers": len(clean_members),
        "verifyIntervalMinutes": first_job.get("verifyIntervalMinutes", 30),
        "campaignDurationDays": first_job.get("campaignDurationDays", 30),
    }


@app.route("/api/group-copy/jobs", methods=["GET"])
def api_group_copy_jobs():
    try:
        return jsonify({"success": True, "jobs": list_group_copy_jobs(include_members=False)})
    except Exception as e:
        return jsonify({"success": False, "error": str(e), "jobs": []}), 500


@app.route("/api/group-copy/jobs/<job_id>", methods=["GET", "DELETE"])
def api_group_copy_job_detail(job_id):
    try:
        if request.method == "DELETE":
            delete_group_copy_job(job_id)
            return jsonify({"success": True, "message": "Đã xóa tác vụ sao chép nhóm."})
        job = get_group_copy_job(job_id)
        if not job:
            return jsonify({"success": False, "error": "Không tìm thấy tác vụ."}), 404
        return jsonify({"success": True, "job": job})
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/group-copy/jobs/<job_id>/cancel", methods=["POST"])
def api_group_copy_job_cancel(job_id):
    try:
        job = cancel_group_copy_job(job_id)
        return jsonify({"success": True, "message": "Đã hủy tác vụ.", "job": job})
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400


@app.route("/api/group-copy/jobs/<job_id>/resume", methods=["POST"])
def api_group_copy_job_resume(job_id):
    data = request.get_json(silent=True) or {}
    try:
        job = resume_group_copy_job(job_id, str(data.get("nextRunAt") or "").strip())
        return jsonify({"success": True, "message": "Đã tiếp tục tác vụ.", "job": job})
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400


@app.route("/api/group-copy/jobs/<job_id>/verify", methods=["POST"])
def api_group_copy_job_verify(job_id):
    try:
        job = request_group_copy_verification(job_id)
        return jsonify({
            "success": True,
            "message": "Đã đưa yêu cầu kiểm tra nhóm đích vào hàng đợi.",
            "job": job,
        })
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/group-copy/start", methods=["POST"])
def api_group_copy_start():
    data = request.get_json(silent=True) or {}
    account_id = str(data.get("accountId") or "").strip()
    # Nhiều tài khoản cùng thực hiện, chia khối thành viên không trùng.
    raw_account_ids = data.get("accountIds") or []
    if isinstance(raw_account_ids, str):
        raw_account_ids = [x.strip() for x in raw_account_ids.split(",") if x.strip()]
    account_ids = []
    for aid in ([account_id] + list(raw_account_ids)):
        aid = str(aid or "").strip()
        if aid and aid not in account_ids:
            account_ids.append(aid)
    if not account_id and account_ids:
        account_id = account_ids[0]
    # Gói không cho chọn nhiều tài khoản thực hiện: chỉ giữ 1 tài khoản.
    if not _current_plan().get("multiAccountExec") and len(account_ids) > 1:
        account_ids = [account_id or account_ids[0]]
    source_input = str(data.get("sourceInput") or data.get("sourceGroup") or "").strip()
    source_group_id = str(data.get("sourceGroupId") or data.get("resolvedSourceGroupId") or "").strip()
    source_input_type, normalized_source_input = normalize_group_input(source_input)
    if source_input_type == "group_id" and normalized_source_input:
        source_input = normalized_source_input
    if source_group_id:
        hint_type, normalized_hint = normalize_group_input(source_group_id)
        source_group_id = normalized_hint if hint_type == "group_id" else ""
    target_mode = str(data.get("targetMode") or "existing").strip().lower()
    target_group_id = str(data.get("targetGroupId") or "").strip()
    target_group_name = str(data.get("targetGroupName") or "").strip()
    new_group_name = str(data.get("newGroupName") or "").strip()
    start_at = str(data.get("startAt") or "").strip()
    consent = bool(data.get("consentConfirmed") or data.get("confirmConsent"))

    try:
        daily_limit = max(1, min(int(data.get("friendRequestDailyLimit") or data.get("dailyLimit") or data.get("batchSize") or 10), 30))
    except Exception:
        return jsonify({"success": False, "error": "Số lời mời kết bạn mỗi ngày không hợp lệ."}), 400
    try:
        verify_minutes = max(1, min(int(data.get("verifyIntervalMinutes") or 30), 1440))
    except Exception:
        return jsonify({"success": False, "error": "Chu kỳ kiểm tra không hợp lệ."}), 400
    try:
        campaign_days = max(1, min(int(data.get("campaignDurationDays") or 30), 365))
    except Exception:
        return jsonify({"success": False, "error": "Số ngày chạy chiến dịch không hợp lệ."}), 400

    if not account_id:
        return jsonify({"success": False, "error": "Vui lòng chọn tài khoản thực hiện."}), 400
    if not source_input:
        return jsonify({"success": False, "error": "Vui lòng dán link hoặc ID nhóm nguồn."}), 400
    if target_mode not in {"existing", "new"}:
        return jsonify({"success": False, "error": "Kiểu nhóm đích không hợp lệ."}), 400
    if target_mode == "existing" and not target_group_id:
        return jsonify({"success": False, "error": "Vui lòng chọn nhóm đích có sẵn."}), 400
    if target_mode == "new" and not new_group_name:
        return jsonify({"success": False, "error": "Vui lòng nhập tên nhóm mới."}), 400
    if not start_at:
        return jsonify({"success": False, "error": "Vui lòng chọn thời gian bắt đầu."}), 400
    try:
        parsed_start = datetime.fromisoformat(start_at)
        if parsed_start < datetime.now() - timedelta(minutes=1):
            start_at = datetime.now().isoformat(timespec="minutes")
    except ValueError:
        return jsonify({"success": False, "error": "Thời gian bắt đầu không hợp lệ."}), 400
    if not consent:
        return jsonify({
            "success": False,
            "error": "Cần xác nhận bạn có quyền mời các thành viên này và tuân thủ chính sách nền tảng.",
        }), 400

    account = get_account(account_id)
    if not account:
        return jsonify({"success": False, "error": "Không tìm thấy tài khoản."}), 404
    # Kiểm tra mọi tài khoản phụ đủ phiên.
    for aid in account_ids:
        acc = get_account(aid)
        if not acc:
            return jsonify({"success": False, "error": f"Không tìm thấy tài khoản {aid}."}), 404
        if not all([acc.get("cookies"), acc.get("zpwEnk"), acc.get("imei")]):
            return jsonify({"success": False, "error": f"Tài khoản {acc.get('name') or aid} chưa đủ phiên (cookies/zpwEnk/IMEI)."}), 400

    payload = {
        "title": str(data.get("title") or "Sao chép thành viên nhóm").strip(),
        "accountId": account_id,
        "accountIds": account_ids,
        "sourceInput": source_input,
        "sourceInputType": source_input_type,
        "sourceGroupId": source_group_id or (source_input if source_input_type == "group_id" else ""),
        "targetMode": target_mode,
        "targetGroupId": target_group_id,
        "targetGroupName": target_group_name,
        "newGroupName": new_group_name,
        "dailyLimit": daily_limit,
        "batchSize": daily_limit,
        "friendRequestDailyLimit": daily_limit,
        "verifyIntervalMinutes": verify_minutes,
        "campaignDurationDays": campaign_days,
        "removeFriendAfterJoin": bool(data.get("removeFriendAfterJoin")),
        "leaveGroupAfterDone": bool(data.get("leaveGroupAfterDone")),
        "skipLeaders": bool(data.get("skipLeaders")),
        "startAt": start_at,
        "consentConfirmed": True,
    }

    task = run_task_in_background(
        _prepare_group_copy_job_worker,
        "Lập lịch sao chép nhóm",
        f"Đọc thành viên từ {source_input}, thêm bạn bè trực tiếp, gửi kết bạn và kiểm tra add lại trong {campaign_days} ngày",
        payload,
    )
    return jsonify({
        "success": True,
        "taskId": task.task_id,
        "status": task.status,
        "message": f"Đang đọc nhóm nguồn và lập lịch thêm bạn bè trực tiếp, gửi kết bạn và kiểm tra add lại mỗi {verify_minutes} phút.",
    }), 202


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


@app.route("/api/schedules/upload-photo", methods=["POST"])
def api_schedules_upload_photo():
    """API: Lưu ảnh đính kèm cho lịch gửi tin (chiến dịch).

    Nhận multipart `photo`, lưu vào data/schedule_photos/, trả về photoPath
    (tương đối với thư mục data) để gắn vào schedule; worker sẽ đọc file này
    khi chạy lịch và gửi qua photo_original/upload + send.
    """
    photo_file = request.files.get("photo") or request.files.get("image") or request.files.get("file")
    if not photo_file:
        return jsonify({"success": False, "error": "Thiếu file ảnh."}), 400

    image_bytes = photo_file.read()
    if not image_bytes:
        return jsonify({"success": False, "error": "File ảnh rỗng."}), 400
    if len(image_bytes) > 20 * 1024 * 1024:
        return jsonify({"success": False, "error": "Ảnh vượt quá 20MB."}), 400

    try:
        from features.messaging.send_photo import get_image_info
        get_image_info(image_bytes)  # raise ValueError nếu không phải ảnh
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400

    try:
        import uuid as _uuid
        from features.schedules.schedule_manager import DATA_DIR as _SCHED_DATA_DIR

        original_name = photo_file.filename or "image.jpg"
        ext = os.path.splitext(original_name)[1].lower()
        if ext not in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"):
            ext = ".jpg"
        photos_dir = os.path.join(_SCHED_DATA_DIR, "schedule_photos")
        os.makedirs(photos_dir, exist_ok=True)
        file_id = _uuid.uuid4().hex[:12]
        saved_name = f"{file_id}{ext}"
        with open(os.path.join(photos_dir, saved_name), "wb") as f:
            f.write(image_bytes)

        return jsonify({
            "success": True,
            "photoPath": f"schedule_photos/{saved_name}",
            "photoName": original_name,
            "size": len(image_bytes),
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/schedules", methods=["POST"])
def api_create_schedule_api():
    """API: Tạo lịch gửi mới."""
    data = request.get_json(silent=True) or {}
    if not data:
        return jsonify({"error": "No data provided"}), 400

    try:
        account_id = str(data.get("accountId") or data.get("account_id") or "").strip()

        # Danh sách tài khoản gửi (nhiều tài khoản chia người nhận không trùng).
        raw_ids = data.get("accountIds") or []
        if isinstance(raw_ids, str):
            raw_ids = [x.strip() for x in raw_ids.split(",") if x.strip()]
        account_ids = []
        for aid in ([account_id] + list(raw_ids)):
            aid = str(aid or "").strip()
            if aid and aid not in account_ids:
                account_ids.append(aid)
        if not account_id and account_ids:
            account_id = account_ids[0]
        # Gói không cho chọn nhiều tài khoản thực hiện: chỉ giữ 1 tài khoản.
        if not _current_plan().get("multiAccountExec") and len(account_ids) > 1:
            account_ids = [account_id or account_ids[0]]

        # Map accountId -> tên hiển thị (để lịch không hiện N/A).
        name_by_id = {}
        try:
            accounts = load_accounts()
            iterable = accounts.values() if isinstance(accounts, dict) else accounts
            for acc in iterable:
                if not isinstance(acc, dict):
                    continue
                aid = str(acc.get("accountId") or acc.get("id") or acc.get("account_id") or "").strip()
                if aid:
                    name_by_id[aid] = (acc.get("name") or acc.get("displayName") or acc.get("zaloName") or acc.get("phone") or aid[:8])
        except Exception as e:
            print("[api_create_schedule_api] load account names failed:", e)

        recipients = data.get("recipients") or []
        n = len(account_ids)

        # Một tài khoản: giữ nguyên hành vi cũ.
        if n <= 1:
            if account_id:
                nm = name_by_id.get(account_id)
                if nm:
                    data["accountName"] = nm
                    data["senderName"] = nm
            schedule = create_schedule(data)
            return jsonify({"success": True, "schedule": schedule})

        # Nhiều tài khoản: chia người nhận thành khối tuần tự, mỗi tài khoản 1 lịch.
        k, m = divmod(len(recipients), n)
        blocks = [recipients[i * k + min(i, m):(i + 1) * k + min(i + 1, m)] for i in range(n)]
        base_title = str(data.get("title") or "Chiến dịch").strip()
        campaign_id = "sched_camp_" + uuid.uuid4().hex[:12]  # gom nhóm nhiều tài khoản
        created = []
        for idx, aid in enumerate(account_ids):
            block = blocks[idx]
            if not block:
                continue
            sub = dict(data)
            sub.pop("accountIds", None)
            sub["accountId"] = aid
            sub["campaignId"] = campaign_id
            nm = name_by_id.get(aid, aid[:8])
            sub["accountName"] = nm
            sub["senderName"] = nm
            sub["title"] = f"{base_title} (TK {idx + 1}/{n} - {nm})"
            sub["recipients"] = block
            created.append(create_schedule(sub))
        if not created:
            return jsonify({"error": "Không có người nhận để chia cho các tài khoản."}), 400
        return jsonify({
            "success": True,
            "schedules": created,
            "scheduleCount": len(created),
            "accountCount": n,
            "schedule": created[0],
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


@app.route("/api/schedules/<schedule_id>/rerun", methods=["POST"])
def api_rerun_schedule(schedule_id):
    """Chạy lại lịch: toàn bộ hoặc chỉ người chưa gửi/lỗi."""
    try:
        schedule = get_schedule(schedule_id)
        if not schedule:
            return jsonify({"error": "Không tìm thấy lịch"}), 404
        if schedule.get("status") == "running":
            return jsonify({"error": "Lịch đang chạy, không thể chạy lại lúc này"}), 400
        mode = str((request.get_json(silent=True) or {}).get("mode") or "retry").strip().lower()
        if mode not in {"all", "retry"}:
            return jsonify({"error": "Chế độ chạy lại không hợp lệ"}), 400
        if mode == "all":
            results = []
        else:
            # Giữ người đã gửi thành công; xóa kết quả lỗi để worker gửi lại.
            results = [r for r in (schedule.get("results") or []) if r.get("status") == "success"]
        schedule = update_schedule(schedule_id, {
            "results": results,
            "status": "running",
            "runAt": datetime.now().isoformat(timespec="seconds"),
            "updatedAt": int(time.time() * 1000),
        })
        return jsonify({"success": True, "schedule": schedule, "mode": mode,
                        "message": "Đã đưa lịch vào hàng đợi chạy lại."})
    except Exception as e:
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

        results = []
        total_accepted = 0
        total_invited = 0
        total_joined = 0
        total_inbox = 0
        total_failed = 0

        for group_id in clean_group_ids:
            invite_result = _invite_members_to_group(
                group_id,
                clean_user_ids,
                imei,
                zpw_enk,
                cookies,
                zpw_ver=zpw_ver,
                batch_size=batch_size,
            )
            group_result = {
                "groupId": group_id,
                "success": bool(invite_result.get("success")),
                "partial": bool(invite_result.get("partial")),
                "accepted": int(invite_result.get("acceptedCount") or 0),
                "invited": int(invite_result.get("invitedCount") or 0),
                "joined": int(invite_result.get("joinedCount") or 0),
                "inbox": int(invite_result.get("inboxCount") or 0),
                "failed": int(invite_result.get("failedCount") or 0),
                "errorMembers": [
                    item.get("userId")
                    for item in (invite_result.get("members") or [])
                    if item.get("status") == "failed"
                ],
                "members": invite_result.get("members") or [],
                "batches": invite_result.get("batches") or [],
            }
            total_accepted += group_result["accepted"]
            total_invited += group_result["invited"]
            total_joined += group_result["joined"]
            total_inbox += group_result["inbox"]
            total_failed += group_result["failed"]
            results.append(group_result)

        all_success = total_failed == 0
        message = (
            f"Zalo đã nhận {total_accepted} lượt mời"
            + (f", trong đó {total_inbox} lượt vào tin nhắn chờ" if total_inbox else "")
            + (f"; lỗi {total_failed} lượt." if total_failed else ".")
        )
        return jsonify({
            "success": all_success,
            "partial": not all_success and total_accepted > 0,
            "message": message,
            "totalGroups": len(clean_group_ids),
            "totalMembers": len(clean_user_ids),
            "totalAccepted": total_accepted,
            "totalInvited": total_invited,
            "totalJoined": total_joined,
            "totalInbox": total_inbox,
            "totalFailed": total_failed,
            "results": results,
        }), 200 if total_accepted > 0 or all_success else 400

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
    # Ưu tiên profileId (uid Zalo — ổn định); vẫn nhận accountId để tương thích.
    account_ref = (
        request.args.get("profileId")
        or request.args.get("profile_id")
        or request.args.get("accountId")
        or request.args.get("account_id")
        or request.args.get("id")
        or ""
    ).strip()
    account_id = account_ref

    if not account_ref:
        return jsonify({"success": False, "error": "Thieu profileId/accountId", "groups": []}), 400

    try:
        accounts = load_accounts()

        account = None
        for acc in accounts:
            aid = str(acc.get("accountId") or acc.get("id") or acc.get("account_id") or "").strip()
            uid = str(acc.get("uid") or "").strip()
            if account_ref in (aid, uid) and aid:
                account = acc
                account_id = aid
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

            group_link = str(
                g.get("groupLink")
                or g.get("group_link")
                or g.get("inviteLink")
                or g.get("link")
                or ""
            ).strip()
            if group_link and not group_link.lower().startswith(("http://", "https://")):
                token = group_link.strip().strip("/")
                group_link = ("https://" + token) if token.lower().startswith("zalo.me/g/") else ("https://zalo.me/g/" + token)

            groups.append({
                "groupId": gid,
                "name": name,
                "avatar": g.get("avatar") or g.get("grid_avatar") or g.get("avt") or "",
                "fullAvt": g.get("fullAvt") or g.get("grid_fullAvt") or g.get("fullAvatar") or "",
                "memberCount": g.get("memberCount") or g.get("grid_totalMember") or g.get("totalMember") or g.get("total") or 0,
                "groupLink": group_link,
                "group_link": group_link,
                "inviteLink": group_link,
                "linkStatus": g.get("linkStatus") or ("ready" if group_link else "pending"),
                "linkExpirationDate": int(g.get("linkExpirationDate") or g.get("expirationDate") or 0),
                "linkCreatedAutomatically": bool(g.get("linkCreatedAutomatically", False)),
                "linkError": g.get("linkError") or "",
                "fetchStatus": g.get("fetchStatus") or "",
                "error": g.get("error") or "",
            })

        # Dữ liệu cũ chỉ có Group ID sẽ được bổ sung link tự động ở nền.
        # Không khóa request UI; lần tải/poll kế tiếp sẽ nhận groupLink đã lưu.
        missing_link_ids = [
            item["groupId"] for item in groups
            if not item.get("groupLink") and str(item.get("linkStatus") or "").lower() not in {"error", "ready"}
        ]
        sync_status = str(account.get("groupsSyncStatus") or "").lower()
        if (
            missing_link_ids
            and sync_status not in {"refreshing", "ids_captured", "links_updating"}
            and account.get("zpwEnk")
            and account.get("cookies")
            and account.get("imei")
        ):
            update_account(account_id, groupsSyncStatus="links_updating")
            start_fetch_group_details_parallel(
                account_id,
                [item["groupId"] for item in groups],
                str(account.get("zpwEnk") or ""),
                str(account.get("cookies") or ""),
                max_workers=4,
            )

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


@app.route("/api/groups/link", methods=["POST"])
def api_get_or_create_group_link():
    """Lấy hoặc tự kích hoạt link cho nhóm đang được chọn trên giao diện."""
    data = request.get_json(silent=True) or request.form or {}
    account_id = str(
        data.get("accountId")
        or data.get("account_id")
        or ""
    ).strip()
    group_id = str(
        data.get("groupId")
        or data.get("group_id")
        or data.get("gridId")
        or data.get("grid")
        or ""
    ).strip()
    force_value = data.get("force", False)
    force = str(force_value).strip().lower() in {"1", "true", "yes", "on"}

    if not account_id:
        return jsonify({"success": False, "error": "Chưa chọn tài khoản."}), 400
    if not group_id:
        return jsonify({"success": False, "error": "Thiếu Group ID."}), 400

    try:
        result = ensure_group_link_for_account(account_id, group_id, force=force)
        status = 200 if result.get("success") else 400
        return jsonify(result), status
    except ValueError as error:
        return jsonify({
            "success": False,
            "groupId": group_id,
            "groupLink": "",
            "linkStatus": "error",
            "error": str(error),
        }), 400
    except Exception as error:
        import traceback
        traceback.print_exc()
        return jsonify({
            "success": False,
            "groupId": group_id,
            "groupLink": "",
            "linkStatus": "error",
            "error": str(error),
        }), 500


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
                zpwEnk="",
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
        member_payload, account, cookies, zpw_enk, imei, zpw_ver = _fetch_group_members_session_safe(
            account_id,
            group_id,
            callback=None,
            require_imei=False,
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
        members = build_member_rows_from_uids(uid_list, profiles, member_map=member_map, group_info=group_info)

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
    """API: Lấy thông tin nhóm chi tiết từ Zalo API (không lấy danh sách thành viên).

    Nhận groupId thô hoặc groupInput (link/ID) — dùng chung logic chuẩn hoá
    link->groupId với luồng lấy thành viên (resolve_group_input_to_group_id)
    để trang Sao chép nhóm có thể xem trước avatar/tên/số thành viên nhóm
    nguồn ngay khi dán link, không cần tạo hẳn tác vụ mới biết.
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    account_id = data.get("accountId") or data.get("account_id")
    group_input = str(
        data.get("groupInput") or data.get("groupId") or data.get("group_id") or ""
    ).strip()

    if not account_id or not group_input:
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

        try:
            _, group_id, resolved_info, _ = resolve_group_input_to_group_id(
                group_input, zpw_enk, cookies, zpw_ver=get_zpw_ver()
            )
        except ValueError as e:
            return jsonify({"success": False, "error": str(e)}), 400
        resolved_info = resolved_info or {}

        if not group_id:
            return jsonify({"success": False, "error": "Không xác định được Group ID từ link/ID đã nhập."}), 400

        sse_broadcast(f"Đang lấy thông tin nhóm {group_id[:8]}...", "loading")

        from features.members.get_members import get_members as get_members_api

        try:
            print(f"[api_groups_info] Calling get_members for group {group_id[:8]} zpw_enk={zpw_enk[:8]}... cookies len={len(cookies)}")
            resp, decoded, ginfo, mem_list = get_members_api(group_id, zpw_enk, cookies, imei=imei, zpw_ver=get_zpw_ver())

            print(f"[api_groups_info] Response: decoded={type(decoded)} ginfo keys={list(ginfo.keys()) if ginfo else 'None'}")

            if not ginfo:
                print(f"[api_groups_info] ginfo is empty!")
                ginfo = {}

            merged_name = ginfo.get("grid_name") or resolved_info.get("name") or ""
            print(f"[api_groups_info] Returning groupInfo with name={merged_name or 'N/A'}")
            sse_broadcast(f"Hoàn thành! {merged_name or 'Nhóm'}", "success")

            return jsonify({
                "success": True,
                "groupInfo": {
                    "groupId": ginfo.get("gridId", group_id),
                    "name": merged_name,
                    "desc": ginfo.get("grid_desc", "") or resolved_info.get("desc", ""),
                    "type": ginfo.get("grid_type", 0),
                    "creatorId": ginfo.get("grid_creatorId", ""),
                    "adminIds": ginfo.get("grid_adminIds", []),
                    "avt": ginfo.get("grid_avatar", "") or resolved_info.get("avt", ""),
                    "fullAvt": ginfo.get("grid_fullAvt", "") or resolved_info.get("fullAvt", ""),
                    "totalMember": ginfo.get("grid_totalMember", 0) or resolved_info.get("totalMember", 0),
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
@app.route("/api/updater/version", methods=["GET"])
def api_updater_version():
    return jsonify({
        "success": True,
        "message": "OK",
    })


@app.route("/api/updater/check", methods=["GET", "POST"])
def api_updater_check():
    try:
        current = _read_app_version()
        release = _fetch_latest_release()
        has_update = _is_newer_version(release["version"], current)
        return jsonify({
            "success": True,
            "state": "ready" if has_update else "none",
            "message": "Có bản cập nhật mới." if has_update else "Bạn đang dùng phiên bản mới nhất.",
            "currentVersion": current,
            "latestVersion": release["version"],
        })
    except Exception as e:
        print("[updater] check error=", e)
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/updater/apply", methods=["POST"])
def api_updater_apply():
    try:
        current = _read_app_version()
        release = _fetch_latest_release()
        if not _is_newer_version(release["version"], current):
            return jsonify({
                "success": True,
                "state": "none",
                "message": "Bạn đang dùng phiên bản mới nhất.",
            })

        update_dir = _get_update_dir()
        if os.path.isdir(update_dir):
            shutil.rmtree(update_dir, ignore_errors=True)
        os.makedirs(update_dir, exist_ok=True)
        zip_path = os.path.join(update_dir, UPDATE_ASSET_NAME)
        _download_update_zip(release["downloadUrl"], zip_path)
        _validate_update_zip(zip_path, release.get("size") or 0)
        _verify_update_hash(zip_path, release)
        install_result = _install_zip_update(zip_path, release["version"])

        return jsonify({
            "success": True,
            "state": install_result.get("state") or "done",
            "message": install_result.get("message") or "Đã cập nhật.",
        })
    except Exception as e:
        print("[updater] apply error=", e)
        return jsonify({"success": False, "error": str(e)}), 500


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
    
    # Do not let the embedded browser keep an old HTML/CSS/JS version after a
    # patch has been copied over the application directory.
    request_path = request.path or ""
    if (
        "text/html" in response.headers.get("Content-Type", "")
        or request_path.startswith("/static/")
    ):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"

    return response



# ─── Minimal Tkinter launcher ─────────────────────────────────────────────────
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


def _get_app_logo_path():
    """Return the bundled PNG logo used by the minimal launcher."""
    candidates = [
        os.path.join(static_path, "ico", "app_256.png"),
        os.path.join(base_path, "static", "ico", "app_256.png"),
        os.path.join(app_root, "static", "ico", "app_256.png"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "ico", "app_256.png"),
    ]
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

        # Kiểm tra kích hoạt qua MÁY CHỦ license trước khi cho worker chạy nền.
        try:
            status = _activation_status_payload()
            print(status.get("message", ""), flush=True)
            if status.get("activated"):
                ensure_schedule_worker_started()
            else:
                print("🔐 Phần mềm chưa kích hoạt. Mở /activation, gửi thông tin máy lên máy chủ để nhận key qua email.", flush=True)
        except Exception as e:
            print(f"⚠️  Lỗi kiểm tra kích hoạt: {str(e)}", flush=True)

        print("✅ Backend đã sẵn sàng", flush=True)
        print("🌐 Giao diện: http://127.0.0.1:5000/policy", flush=True)
        print("👉 Bấm nút 'Mở giao diện' để mở trình duyệt.", flush=True)
        app.run(debug=False, port=5000, host="127.0.0.1", use_reloader=False, threaded=True)
    except Exception as e:
        print(f"❌ Lỗi khởi động backend: {e}", flush=True)
        import traceback
        traceback.print_exc()


def _cleanup_update_script():
    try:
        candidates = []
        candidates.append(os.path.join(tempfile.gettempdir(), "_nexus_apply_update.bat"))
        if getattr(sys, "frozen", False):
            candidates.append(os.path.join(os.path.dirname(sys.executable), "_nexus_apply_update.bat"))
        else:
            candidates.append(os.path.join(app_root, "_nexus_apply_update.bat"))
        for path in candidates:
            if os.path.exists(path):
                os.remove(path)
    except Exception:
        pass


NEXUS_UI_URL = "http://127.0.0.1:5000/policy"
NEXUS_UI_ORIGIN = "http://127.0.0.1:5000"
NEXUS_UI_EDGE_DEBUG_PORT = 9322


def _find_edge_executable():
    """Tìm Microsoft Edge trên Windows; giao diện Nexus không dùng Chrome/default browser."""
    candidates = [
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe"),
    ]
    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return candidate

    if sys.platform.startswith("win"):
        try:
            import winreg
            registry_locations = (
                (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe"),
            )
            for hive, key_path in registry_locations:
                try:
                    with winreg.OpenKey(hive, key_path) as key:
                        value, _ = winreg.QueryValueEx(key, None)
                    if value and os.path.isfile(value):
                        return value
                except OSError:
                    continue
        except Exception:
            pass

    return ""


def _edge_ui_debug_ready():
    try:
        response = requests.get(
            f"http://127.0.0.1:{NEXUS_UI_EDGE_DEBUG_PORT}/json/version",
            timeout=0.6,
        )
        return response.status_code == 200
    except Exception:
        return False


def _edge_ui_targets():
    try:
        response = requests.get(
            f"http://127.0.0.1:{NEXUS_UI_EDGE_DEBUG_PORT}/json/list",
            timeout=0.8,
        )
        if response.status_code != 200:
            return []
        payload = response.json()
        return payload if isinstance(payload, list) else []
    except Exception:
        return []


def _find_existing_nexus_ui_target():
    """Tìm bất kỳ tab Nexus đang mở, kể cả khi người dùng đã chuyển khỏi /policy."""
    matches = []
    for target in _edge_ui_targets():
        if not isinstance(target, dict) or target.get("type") != "page":
            continue
        url = str(target.get("url") or "")
        if url == NEXUS_UI_ORIGIN or url.startswith(NEXUS_UI_ORIGIN + "/"):
            matches.append(target)

    # /json/list thường trả target đang hoạt động gần đầu danh sách. Không điều hướng
    # lại URL để giữ nguyên đúng trang người dùng đang xem trước đó.
    return matches[0] if matches else None


def _activate_edge_target(target):
    target_id = str((target or {}).get("id") or "").strip()
    if not target_id:
        return False
    try:
        response = requests.get(
            f"http://127.0.0.1:{NEXUS_UI_EDGE_DEBUG_PORT}/json/activate/{target_id}",
            timeout=1.0,
        )
        return response.status_code == 200
    except Exception:
        return False


def _create_edge_ui_target():
    """Tạo tab UI chỉ khi Edge đã chạy nhưng chưa hề có tab Nexus."""
    try:
        from urllib.parse import quote
        response = requests.put(
            f"http://127.0.0.1:{NEXUS_UI_EDGE_DEBUG_PORT}/json/new?{quote(NEXUS_UI_URL, safe='')}",
            timeout=1.5,
        )
        if response.status_code != 200:
            return None
        payload = response.json()
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None


def _open_nexus_ui_in_edge():
    """Mở Nexus bằng Edge App mode: không tab/địa chỉ/thanh điều hướng và không mở trùng cửa sổ."""
    existing = _find_existing_nexus_ui_target()
    if existing and _activate_edge_target(existing):
        return True, str(existing.get("url") or NEXUS_UI_URL), True

    edge_exe = _find_edge_executable()
    if not edge_exe:
        return False, "Không tìm thấy Microsoft Edge trên máy.", False

    # Dùng profile Edge riêng cho cửa sổ Nexus để CDP có thể nhận diện cửa sổ
    # đang tồn tại ở lần bấm sau. --app=URL giúp giao diện hiển thị như ứng dụng:
    # chỉ còn title bar của cửa sổ, không có tab, address bar, Back/Refresh hay menu trình duyệt.
    profile_dir = os.path.join(app_root, "data", "nexus_edge_ui")
    os.makedirs(profile_dir, exist_ok=True)

    args = [
        edge_exe,
        f"--user-data-dir={profile_dir}",
        f"--remote-debugging-port={NEXUS_UI_EDGE_DEBUG_PORT}",
        "--remote-allow-origins=*",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-session-crashed-bubble",
        f"--app={NEXUS_UI_URL}",
    ]
    creationflags = 0
    if sys.platform.startswith("win"):
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

    # Kể cả Edge profile vẫn còn tiến trình nền/CDP sau khi đóng cửa sổ UI,
    # luôn gọi lại --app để Edge tạo đúng cửa sổ app-mode; không dùng /json/new
    # vì endpoint đó có thể sinh tab/cửa sổ trình duyệt thông thường có thanh điều hướng.
    subprocess.Popen(args, creationflags=creationflags, close_fds=True)

    for _ in range(40):
        time.sleep(0.1)
        target = _find_existing_nexus_ui_target()
        if target:
            _activate_edge_target(target)
            return True, str(target.get("url") or NEXUS_UI_URL), False

    # Edge có thể khởi động chậm hơn thời gian chờ nhưng lệnh --app đã được gửi thành công.
    return True, NEXUS_UI_URL, False


def _launch_wait_window():
    """Show only the Nexus logo and the button that opens the web interface."""
    _cleanup_update_script()
    import socket
    import tkinter as tk
    from tkinter import ttk

    try:
        if sys.platform.startswith("win"):
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("PhanMem.Nexus.Launcher")
    except Exception:
        pass

    root = tk.Tk()
    root.title("Nexus")
    window_width = 420
    window_height = 330
    screen_width = root.winfo_screenwidth()
    screen_height = root.winfo_screenheight()
    pos_x = max(0, (screen_width - window_width) // 2)
    pos_y = max(0, (screen_height - window_height) // 2)
    root.geometry(f"{window_width}x{window_height}+{pos_x}+{pos_y}")
    root.resizable(False, False)
    root.configure(bg="#F5F7FB")

    try:
        icon_path = _get_app_icon_path()
        if icon_path and os.path.exists(icon_path):
            root.iconbitmap(default=icon_path)
    except Exception as error:
        print(f"Không đặt được icon cửa sổ: {error}", flush=True)

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except Exception:
        pass
    style.configure(
        "Nexus.TButton",
        background="#111827",
        foreground="#FFFFFF",
        borderwidth=0,
        focusthickness=0,
        focuscolor="#111827",
        font=("Segoe UI", 11, "bold"),
        padding=(30, 14),
        relief="flat",
    )
    style.map(
        "Nexus.TButton",
        background=[("disabled", "#CBD5E1"), ("active", "#1F2937"), ("pressed", "#0F172A")],
        foreground=[("disabled", "#64748B"), ("active", "#FFFFFF")],
    )

    content = tk.Frame(root, bg="#F5F7FB", padx=42, pady=36)
    content.pack(fill="both", expand=True)

    logo_path = _get_app_logo_path()
    if logo_path:
        try:
            logo_image = tk.PhotoImage(file=logo_path)
            max_side = max(logo_image.width(), logo_image.height())
            if max_side > 150:
                factor = max(1, (max_side + 149) // 150)
                logo_image = logo_image.subsample(factor, factor)
            logo = tk.Label(content, image=logo_image, bg="#F5F7FB", borderwidth=0)
            logo.image = logo_image
            root._nexus_logo_image = logo_image
            logo.pack(pady=(4, 42))
        except Exception as error:
            print(f"Không tải được logo launcher: {error}", flush=True)

    open_button = ttk.Button(
        content,
        text="Đang khởi động…",
        state="disabled",
        style="Nexus.TButton",
    )
    open_button.pack(fill="x")

    def backend_is_ready():
        try:
            with socket.create_connection(("127.0.0.1", 5000), timeout=0.08):
                return True
        except OSError:
            return False

    ui_open_lock = threading.Lock()

    def open_ui():
        if not backend_is_ready() or ui_open_lock.locked():
            return

        def worker():
            with ui_open_lock:
                try:
                    root.after(0, lambda: open_button.configure(text="Đang mở Edge…", state="disabled"))
                    success, detail, reused = _open_nexus_ui_in_edge()
                    if success:
                        action = "Đã chuyển tới giao diện Edge đang mở" if reused else "Đã mở giao diện bằng Edge"
                        print(f"{action}: {detail}", flush=True)
                    else:
                        print(f"Không thể mở giao diện bằng Edge: {detail}", flush=True)
                except Exception as error:
                    print(f"Không thể mở giao diện bằng Edge: {error}", flush=True)
                finally:
                    try:
                        root.after(0, lambda: open_button.configure(text="Mở giao diện", state="normal"))
                    except Exception:
                        pass

        threading.Thread(target=worker, daemon=True).start()

    open_button.configure(command=open_ui)

    def poll_backend():
        if backend_is_ready():
            open_button.configure(text="Mở giao diện", state="normal")
            return
        root.after(250, poll_backend)

    def on_close():
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        except Exception:
            pass
        root.destroy()
        os._exit(0)

    root.protocol("WM_DELETE_WINDOW", on_close)

    server_thread = threading.Thread(target=_start_backend_server, daemon=True)
    server_thread.start()
    print("Đang khởi động Nexus. Launcher không hiển thị log.", flush=True)
    root.after(200, poll_backend)
    root.mainloop()


if __name__ == "__main__":
    _launch_wait_window()
