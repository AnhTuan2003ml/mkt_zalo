import json
import os
import sys
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional


def _get_app_root() -> str:
    """Return writable app root in source mode and PyInstaller mode."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _data_dir() -> str:
    path = os.path.join(_get_app_root(), "data")
    os.makedirs(path, exist_ok=True)
    return path


def _messages_path() -> str:
    return os.path.join(_data_dir(), "messages.json")


def _settings_path() -> str:
    return os.path.join(_data_dir(), "message_settings.json")


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _read_json(path: str, default: Any) -> Any:
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _write_json(path: str, data: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def _load_db() -> Dict[str, Any]:
    db = _read_json(_messages_path(), {"conversations": [], "events": []})
    if not isinstance(db, dict):
        db = {"conversations": [], "events": []}
    db.setdefault("conversations", [])
    db.setdefault("events", [])
    return db


def _save_db(db: Dict[str, Any]) -> None:
    _write_json(_messages_path(), db)


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def list_conversations(account_id: Optional[str] = None, status: Optional[str] = None, q: Optional[str] = None) -> List[Dict[str, Any]]:
    """List conversations with light filtering for inbox UI."""
    db = _load_db()
    items = db.get("conversations", [])
    account_id = _normalize_text(account_id)
    status = _normalize_text(status)
    q = _normalize_text(q).lower()

    filtered: List[Dict[str, Any]] = []
    for item in items:
        if account_id and item.get("account_id") != account_id:
            continue
        if status and status != "all" and item.get("status") != status:
            continue
        if q:
            haystack = " ".join([
                _normalize_text(item.get("peer_name")),
                _normalize_text(item.get("peer_id")),
                _normalize_text(item.get("last_message")),
                _normalize_text(item.get("note")),
                " ".join(item.get("tags") or []),
            ]).lower()
            if q not in haystack:
                continue
        # Avoid sending very large raw data in the list endpoint.
        slim = dict(item)
        slim["messages"] = item.get("messages", [])[-3:]
        filtered.append(slim)

    def sort_key(x: Dict[str, Any]) -> str:
        return x.get("last_message_time") or x.get("updated_at") or x.get("created_at") or ""

    filtered.sort(key=sort_key, reverse=True)
    return filtered


def get_conversation(conversation_id: str) -> Optional[Dict[str, Any]]:
    db = _load_db()
    for item in db.get("conversations", []):
        if item.get("id") == conversation_id:
            return item
    return None


def _find_conversation(db: Dict[str, Any], conversation_id: str) -> Optional[Dict[str, Any]]:
    for item in db.get("conversations", []):
        if item.get("id") == conversation_id:
            return item
    return None


def create_conversation(
    account_id: str,
    peer_id: str,
    peer_name: str = "",
    peer_avatar: str = "",
    source: str = "manual",
    first_message: str = "",
    direction: str = "in",
) -> Dict[str, Any]:
    """Create a conversation record, or append to existing pair account_id + peer_id."""
    account_id = _normalize_text(account_id)
    peer_id = _normalize_text(peer_id)
    if not account_id:
        raise ValueError("Thiếu account_id")
    if not peer_id:
        raise ValueError("Thiếu peer_id hoặc UID người nhắn")

    db = _load_db()
    existing = None
    for item in db.get("conversations", []):
        if item.get("account_id") == account_id and item.get("peer_id") == peer_id:
            existing = item
            break

    now = _now()
    if existing is None:
        existing = {
            "id": uuid.uuid4().hex,
            "account_id": account_id,
            "peer_id": peer_id,
            "peer_name": peer_name or peer_id,
            "peer_avatar": peer_avatar or "",
            "source": source or "manual",
            "status": "new",
            "priority": "normal",
            "unread_count": 0,
            "last_message": "",
            "last_message_time": now,
            "tags": [],
            "note": "",
            "auto_reply_enabled": False,
            "created_at": now,
            "updated_at": now,
            "messages": [],
        }
        db.setdefault("conversations", []).append(existing)

    if first_message:
        message = {
            "id": uuid.uuid4().hex,
            "direction": "out" if direction == "out" else "in",
            "text": first_message,
            "created_at": now,
            "status": "saved",
            "raw": {},
        }
        existing.setdefault("messages", []).append(message)
        existing["last_message"] = first_message
        existing["last_message_time"] = now
        existing["updated_at"] = now
        if message["direction"] == "in":
            existing["unread_count"] = int(existing.get("unread_count") or 0) + 1
            if existing.get("status") in ("closed", "replied"):
                existing["status"] = "new"

    _save_db(db)
    return existing


def add_message(conversation_id: str, text: str, direction: str = "in", raw: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    text = _normalize_text(text)
    if not text:
        raise ValueError("Nội dung tin nhắn trống")
    db = _load_db()
    conv = _find_conversation(db, conversation_id)
    if not conv:
        raise ValueError("Không tìm thấy hội thoại")
    now = _now()
    msg = {
        "id": uuid.uuid4().hex,
        "direction": "out" if direction == "out" else "in",
        "text": text,
        "created_at": now,
        "status": "saved",
        "raw": raw or {},
    }
    conv.setdefault("messages", []).append(msg)
    conv["last_message"] = text
    conv["last_message_time"] = now
    conv["updated_at"] = now
    if msg["direction"] == "in":
        conv["unread_count"] = int(conv.get("unread_count") or 0) + 1
        if conv.get("status") in ("closed", "replied"):
            conv["status"] = "new"
    else:
        conv["status"] = "replied"
    _save_db(db)
    return conv


def update_conversation(conversation_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    db = _load_db()
    conv = _find_conversation(db, conversation_id)
    if not conv:
        raise ValueError("Không tìm thấy hội thoại")

    allowed = {"status", "priority", "tags", "note", "auto_reply_enabled", "peer_name", "peer_avatar", "unread_count"}
    for key, value in (patch or {}).items():
        if key not in allowed:
            continue
        if key == "tags":
            if isinstance(value, str):
                value = [x.strip() for x in value.split(",") if x.strip()]
            if not isinstance(value, list):
                value = []
        if key == "auto_reply_enabled":
            value = bool(value)
        if key == "unread_count":
            try:
                value = max(0, int(value))
            except Exception:
                value = 0
        conv[key] = value
    conv["updated_at"] = _now()
    _save_db(db)
    return conv


def mark_read(conversation_id: str) -> Dict[str, Any]:
    return update_conversation(conversation_id, {"unread_count": 0})


def get_stats(account_id: Optional[str] = None) -> Dict[str, int]:
    items = list_conversations(account_id=account_id, status="all", q="")
    stats = {
        "total": len(items),
        "new": 0,
        "waiting": 0,
        "replied": 0,
        "closed": 0,
        "auto_reply_enabled": 0,
        "unread": 0,
    }
    for item in items:
        status = item.get("status") or "new"
        if status in stats:
            stats[status] += 1
        if item.get("auto_reply_enabled"):
            stats["auto_reply_enabled"] += 1
        stats["unread"] += int(item.get("unread_count") or 0)
    return stats


def get_settings() -> Dict[str, Any]:
    settings = _read_json(_settings_path(), {})
    if not isinstance(settings, dict):
        settings = {}
    settings.setdefault("auto_check_enabled", True)
    settings.setdefault("check_interval_minutes", 1)
    # Số giờ lưu tin trong db trước khi tự xóa (0 = không tự xóa). Mặc định 1 ngày.
    settings.setdefault("retention_hours", 24)
    settings.setdefault("auto_reply_enabled", False)
    settings.setdefault("default_reply", "")
    settings.setdefault("last_check_at", "")
    settings.setdefault("last_check_message", "Chưa đồng bộ tin nhắn")
    return settings


def update_settings(patch: Dict[str, Any]) -> Dict[str, Any]:
    settings = get_settings()
    allowed = {"auto_check_enabled", "check_interval_minutes", "retention_hours", "auto_reply_enabled", "default_reply"}
    for key, value in (patch or {}).items():
        if key not in allowed:
            continue
        if key in {"auto_check_enabled", "auto_reply_enabled"}:
            settings[key] = bool(value)
        elif key == "check_interval_minutes":
            try:
                settings[key] = max(1, int(value))
            except Exception:
                settings[key] = 1
        elif key == "retention_hours":
            try:
                settings[key] = max(0, int(value))
            except Exception:
                settings[key] = 24
        else:
            settings[key] = _normalize_text(value)
    _write_json(_settings_path(), settings)
    return settings


def log_check_attempt(account_id: Optional[str] = None, message: str = "") -> Dict[str, Any]:
    """Record that the UI requested a message sync. Actual Zalo sync can be wired here later."""
    now = _now()
    settings = get_settings()
    settings["last_check_at"] = now
    settings["last_check_account_id"] = account_id or ""
    settings["last_check_message"] = message or "Đã ghi nhận yêu cầu kiểm tra. Chưa nối API đồng bộ tin nhắn Zalo thật."
    _write_json(_settings_path(), settings)

    db = _load_db()
    db.setdefault("events", []).append({
        "id": uuid.uuid4().hex,
        "type": "check_requested",
        "account_id": account_id or "",
        "message": settings["last_check_message"],
        "created_at": now,
    })
    db["events"] = db.get("events", [])[-300:]
    _save_db(db)
    return settings
