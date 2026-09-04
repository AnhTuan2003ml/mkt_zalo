"""Quản lý tin nhắn ghim chưa đọc của từng nhóm Zalo.

Đồng bộ tin ghim (board pin) của các nhóm trong ``personalGroups`` từng tài
khoản, lưu vào ``data/unread_messages.json`` để nhắc nhở. Tin nào người dùng
đã xem thì xóa khỏi db (đưa vào danh sách dismissed để lần quét sau không
thêm lại). Chu kỳ giữa 2 lần quét lấy từ thiết lập trang tin nhắn
(``check_interval_minutes`` trong ``data/message_settings.json``).
"""

import os
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from features.messages.message_manager import (
    _data_dir,
    _read_json,
    _write_json,
    get_settings,
    log_check_attempt,
)

_store_lock = threading.RLock()

# Giữ tối đa chừng này khóa đã-đọc để file không phình vô hạn.
_MAX_DISMISSED = 5000
# Nghỉ giữa 2 lần gọi API cho 2 nhóm liên tiếp (tránh gọi dồn dập).
_GROUP_CALL_DELAY_SECONDS = 1.0


def _unread_path() -> str:
    return os.path.join(_data_dir(), "unread_messages.json")


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _load_store() -> Dict[str, Any]:
    store = _read_json(_unread_path(), {})
    if not isinstance(store, dict):
        store = {}
    store.setdefault("items", [])
    store.setdefault("dismissed", {})
    store.setdefault("groups", {})
    store.setdefault("lastSyncAt", "")
    store.setdefault("lastSyncMessage", "")
    if not isinstance(store["dismissed"], dict):
        # Tương thích bản cũ nếu dismissed từng là list.
        store["dismissed"] = {str(k): "" for k in store["dismissed"]}
    return store


def _save_store(store: Dict[str, Any]) -> None:
    _write_json(_unread_path(), store)


def _item_key(account_id: str, group_id: str, pin_id: str) -> str:
    return f"{account_id}:{group_id}:{pin_id}"


def _cap_dismissed(store: Dict[str, Any]) -> None:
    if len(store["dismissed"]) > _MAX_DISMISSED:
        oldest = sorted(store["dismissed"].items(), key=lambda kv: kv[1])
        for key, _ in oldest[: len(store["dismissed"]) - _MAX_DISMISSED]:
            store["dismissed"].pop(key, None)


def _prune_expired(store: Dict[str, Any]) -> int:
    """Tự xóa tin đã lưu quá thời gian lưu trữ (retention_hours, 0 = tắt).

    Tin bị xóa được đưa vào dismissed để lần quét sau không thêm lại.
    """
    hours = int(get_settings().get("retention_hours") or 0)
    if hours <= 0:
        return 0
    cutoff = datetime.now() - timedelta(hours=hours)
    kept: List[Dict[str, Any]] = []
    removed = 0
    for item in store["items"]:
        try:
            fetched = datetime.strptime(str(item.get("fetchedAt") or ""), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            fetched = None
        if fetched is not None and fetched < cutoff:
            store["dismissed"][str(item.get("id") or "")] = _now_str()
            removed += 1
        else:
            kept.append(item)
    if removed:
        store["items"] = kept
        _cap_dismissed(store)
    return removed


def list_unread_messages(account_id: Optional[str] = None) -> Dict[str, Any]:
    """Danh sách tin ghim chưa đọc (mới nhất trước) kèm thông tin đồng bộ.

    Danh sách nhóm luôn dựng lại từ ``personalGroups`` hiện tại của các tài
    khoản (nguồn chân lý) và ghép thêm meta lần quét đã lưu — nhóm vừa đồng
    bộ thêm sẽ hiện ngay, nhóm đã gỡ khỏi tài khoản biến mất.
    """
    from features.accounts.account_manager import load_accounts

    with _store_lock:
        store = _load_store()
        if _prune_expired(store):
            _save_store(store)

    account_id = str(account_id or "").strip()
    stored_meta = store.get("groups", {})
    groups: Dict[str, Any] = {}
    for acc in load_accounts():
        aid = str(acc.get("accountId") or "").strip()
        acc_name = str(acc.get("name") or "").strip() or aid
        for group in _account_groups(acc):
            gid = group["groupId"]
            meta = stored_meta.get(gid, {})
            groups[gid] = {
                "name": group["name"] or meta.get("name", gid),
                "avatar": group.get("avatar") or meta.get("avatar", ""),
                "accountId": aid,
                "accountName": acc_name,
                "lastSyncAt": meta.get("lastSyncAt", ""),
                "lastError": meta.get("lastError", ""),
                "boardVersion": meta.get("boardVersion", 0),
            }

    items = [
        item for item in store.get("items", [])
        if (not account_id or item.get("accountId") == account_id)
        and item.get("groupId") in groups
    ]
    items.sort(key=lambda x: int(x.get("createTime") or 0), reverse=True)
    return {
        "items": items,
        "lastSyncAt": store.get("lastSyncAt", ""),
        "lastSyncMessage": store.get("lastSyncMessage", ""),
        "groups": groups,
    }


def dismiss_unread_message(item_id: str) -> bool:
    """Xóa một tin khỏi db (đã xem) và ghi nhớ để không thêm lại khi quét."""
    item_id = str(item_id or "").strip()
    if not item_id:
        return False
    with _store_lock:
        store = _load_store()
        before = len(store["items"])
        store["items"] = [x for x in store["items"] if x.get("id") != item_id]
        if len(store["items"]) == before:
            return False
        store["dismissed"][item_id] = _now_str()
        _cap_dismissed(store)
        _save_store(store)
    return True


def dismiss_group_messages(account_id: str, group_id: str) -> int:
    """Xóa toàn bộ tin của một nhóm khỏi db (đã xem hết)."""
    account_id = str(account_id or "").strip()
    group_id = str(group_id or "").strip()
    if not group_id:
        return 0
    removed = 0
    with _store_lock:
        store = _load_store()
        kept = []
        for item in store["items"]:
            if item.get("groupId") == group_id and (not account_id or item.get("accountId") == account_id):
                store["dismissed"][str(item.get("id") or "")] = _now_str()
                removed += 1
            else:
                kept.append(item)
        if removed:
            store["items"] = kept
            _cap_dismissed(store)
            _save_store(store)
    return removed


def _account_groups(account: dict) -> List[Dict[str, str]]:
    """Rút danh sách (groupId, name) từ personalGroups của tài khoản."""
    raw_groups = account.get("personalGroups") or []
    if isinstance(raw_groups, dict):
        raw_groups = list(raw_groups.values())
    groups = []
    seen = set()
    for g in raw_groups:
        if not isinstance(g, dict):
            continue
        gid = str(g.get("groupId") or g.get("gridId") or g.get("id") or g.get("gid") or "").strip()
        if not gid or gid in seen:
            continue
        seen.add(gid)
        name = str(
            g.get("name") or g.get("grid_name") or g.get("groupName")
            or g.get("title") or ("Nhóm " + gid[:8])
        ).strip()
        avatar = str(g.get("avatar") or g.get("fullAvt") or g.get("avt") or "").strip()
        groups.append({"groupId": gid, "name": name, "avatar": avatar})
    return groups


def sync_unread_messages(account_id: Optional[str] = None) -> Dict[str, Any]:
    """Quét tin ghim mọi nhóm của các tài khoản đủ phiên; cập nhật db.

    Returns:
        {"ok", "accounts", "groupsChecked", "newCount", "updatedCount",
         "errorCount", "message"}
    """
    from features.accounts.account_manager import load_accounts
    from features.messaging.board_pin import fetch_board_pins

    with _store_lock:
        store = _load_store()
        if _prune_expired(store):
            _save_store(store)

    account_id = str(account_id or "").strip()
    accounts = load_accounts()
    targets = []
    for acc in accounts:
        aid = str(acc.get("accountId") or "").strip()
        if account_id and aid != account_id:
            continue
        if not all([acc.get("cookies"), acc.get("zpwEnk"), acc.get("imei")]):
            continue
        if not _account_groups(acc):
            continue
        targets.append(acc)

    if not targets:
        message = ("Không có tài khoản đủ phiên đăng nhập hoặc chưa đồng bộ "
                   "danh sách nhóm cá nhân để quét tin ghim.")
        with _store_lock:
            store = _load_store()
            store["lastSyncAt"] = _now_str()
            store["lastSyncMessage"] = message
            _save_store(store)
        return {"ok": False, "accounts": 0, "groupsChecked": 0, "newCount": 0,
                "updatedCount": 0, "errorCount": 0, "message": message}

    groups_checked = 0
    new_count = 0
    updated_count = 0
    error_count = 0
    first_call = True

    for acc in targets:
        aid = str(acc.get("accountId") or "").strip()
        acc_name = str(acc.get("name") or "").strip() or aid
        for group in _account_groups(acc):
            gid = group["groupId"]
            if not first_call:
                time.sleep(_GROUP_CALL_DELAY_SECONDS)
            first_call = False
            result = fetch_board_pins(
                gid,
                cookies=acc.get("cookies", ""),
                zpw_enk=acc.get("zpwEnk", ""),
                imei=acc.get("imei", ""),
            )
            groups_checked += 1
            now = _now_str()
            with _store_lock:
                store = _load_store()
                group_meta = store["groups"].setdefault(gid, {})
                group_meta["name"] = group["name"]
                group_meta["avatar"] = group.get("avatar", "")
                group_meta["accountId"] = aid
                group_meta["accountName"] = acc_name
                group_meta["lastSyncAt"] = now
                if not result.get("ok"):
                    error_count += 1
                    group_meta["lastError"] = result.get("message", "Lỗi")
                    _save_store(store)
                    continue
                group_meta["lastError"] = ""
                group_meta["boardVersion"] = result.get("boardVersion", 0)

                existing = {x.get("id"): x for x in store["items"]}
                for pin in result.get("items", []):
                    key = _item_key(aid, gid, pin["pinId"])
                    if key in store["dismissed"]:
                        continue
                    if key in existing:
                        item = existing[key]
                        if int(pin.get("editTime") or 0) > int(item.get("editTime") or 0):
                            item.update({
                                "title": pin["title"],
                                "thumb": pin.get("thumb", ""),
                                "href": pin.get("href", ""),
                                "editTime": pin["editTime"],
                                "emoji": pin["emoji"],
                                "updatedAt": now,
                            })
                            updated_count += 1
                        elif not item.get("thumb") and pin.get("thumb"):
                            # Bổ sung ảnh cho tin đã lưu bằng bản cũ chưa parse thumb.
                            item["thumb"] = pin.get("thumb", "")
                            item["href"] = pin.get("href", "")
                        continue
                    store["items"].append({
                        "id": key,
                        "accountId": aid,
                        "accountName": acc_name,
                        "groupId": gid,
                        "groupName": group["name"],
                        "pinId": pin["pinId"],
                        "emoji": pin["emoji"],
                        "title": pin["title"],
                        "thumb": pin.get("thumb", ""),
                        "href": pin.get("href", ""),
                        "senderUid": pin["senderUid"],
                        "senderName": pin["senderName"],
                        "clientMsgId": pin["clientMsgId"],
                        "globalMsgId": pin["globalMsgId"],
                        "msgType": pin["msgType"],
                        "createTime": pin["createTime"],
                        "editTime": pin["editTime"],
                        "fetchedAt": now,
                    })
                    new_count += 1
                _save_store(store)

    # Dọn meta + tin của nhóm không còn trong personalGroups của tài khoản đã quét
    # (người dùng vừa đồng bộ lại danh sách nhóm).
    valid_by_account = {
        str(acc.get("accountId") or "").strip(): {g["groupId"] for g in _account_groups(acc)}
        for acc in targets
    }
    with _store_lock:
        store = _load_store()
        stale_gids = [
            gid for gid, meta in store["groups"].items()
            if meta.get("accountId") in valid_by_account
            and gid not in valid_by_account[meta.get("accountId")]
        ]
        if stale_gids:
            for gid in stale_gids:
                store["groups"].pop(gid, None)
            stale_set = set(stale_gids)
            store["items"] = [x for x in store["items"] if x.get("groupId") not in stale_set]
            _save_store(store)

    message = (
        f"Đã quét {groups_checked} nhóm của {len(targets)} tài khoản: "
        f"{new_count} tin ghim mới, {updated_count} tin cập nhật"
        + (f", {error_count} nhóm lỗi." if error_count else ".")
    )
    with _store_lock:
        store = _load_store()
        store["lastSyncAt"] = _now_str()
        store["lastSyncMessage"] = message
        _save_store(store)
    return {"ok": True, "accounts": len(targets), "groupsChecked": groups_checked,
            "newCount": new_count, "updatedCount": updated_count,
            "errorCount": error_count, "message": message}


def get_group_latest_messages(group_id: str, account_id: Optional[str] = None) -> Dict[str, Any]:
    """Gọi trực tiếp Zalo để lấy tin ghim mới nhất của một nhóm truyền vào.

    Không đụng tới db tin chưa đọc — dùng cho API tích hợp bên ngoài.
    Raises ValueError/RuntimeError khi thiếu tham số, thiếu phiên hoặc API lỗi.
    """
    from features.accounts.account_manager import load_accounts
    from features.messaging.board_pin import fetch_board_pins

    group_id = str(group_id or "").strip()
    if not group_id:
        raise ValueError("Thiếu groupId")

    account_id = str(account_id or "").strip()
    account = None
    for acc in load_accounts():
        aid = str(acc.get("accountId") or "").strip()
        if account_id and aid != account_id:
            continue
        if not all([acc.get("cookies"), acc.get("zpwEnk"), acc.get("imei")]):
            continue
        account = acc
        break
    if not account:
        raise ValueError("Không có tài khoản đủ phiên đăng nhập (cookies/zpwEnk/imei).")

    result = fetch_board_pins(
        group_id,
        cookies=account.get("cookies", ""),
        zpw_enk=account.get("zpwEnk", ""),
        imei=account.get("imei", ""),
    )
    if not result.get("ok"):
        raise RuntimeError(result.get("message") or "Không lấy được tin nhắn của nhóm.")

    items = sorted(
        result.get("items", []),
        key=lambda x: int(x.get("createTime") or 0),
        reverse=True,
    )
    return {
        "groupId": result.get("groupId", group_id),
        "accountId": str(account.get("accountId") or ""),
        "boardVersion": result.get("boardVersion", 0),
        "pinLimit": result.get("pinLimit", 0),
        "count": len(items),
        "latest": items[0] if items else None,
        "items": items,
    }


# ─── Worker tự động quét theo chu kỳ thiết lập ────────────────────────────────

_worker_lock = threading.Lock()
_worker_started = False
_last_auto_sync = 0.0


def _worker_loop() -> None:
    global _last_auto_sync
    while True:
        time.sleep(15)
        try:
            settings = get_settings()
            if not settings.get("auto_check_enabled"):
                continue
            interval_seconds = max(1, int(settings.get("check_interval_minutes") or 5)) * 60
            if time.time() - _last_auto_sync < interval_seconds:
                continue
            _last_auto_sync = time.time()
            result = sync_unread_messages()
            log_check_attempt(account_id="", message=result.get("message", ""))
            print(f"[unread_worker] {result.get('message', '')}", flush=True)
        except Exception as exc:
            print(f"[unread_worker] Loop error: {exc}", flush=True)


def start_unread_worker() -> None:
    """Khởi động worker quét tin ghim (chạy 1 lần, thread daemon)."""
    global _worker_started
    with _worker_lock:
        if _worker_started:
            return
        thread = threading.Thread(target=_worker_loop, daemon=True, name="unread-pin-worker")
        thread.start()
        _worker_started = True
        print("[unread_worker] Worker started", flush=True)
