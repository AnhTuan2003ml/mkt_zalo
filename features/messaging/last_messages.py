"""Lấy tin nhắn MỚI NHẤT của các cuộc trò chuyện qua API preloadconvers.

API: GET https://tt-convers-wpa.chat.zalo.me/api/preloadconvers/get-last-msgs
Payload trước mã hóa:
    {"threadIdLocalMsgId": "{\"<groupId>_1\": \"<msgId đã biết>\", ...}",
     "imei": "..."}
Hậu tố thread: ``_1`` là nhóm, ``_0`` là cá nhân. msgId "0" = chưa biết gì,
server trả tin mới nhất hiện có. Response sau giải mã:
    {"data": {"msgs": [tin 1-1], "groupMsgs": [tin nhóm], ...}}
Mỗi tin nhóm: idTo = groupId, uidFrom = uid người gửi ("0" = chính mình),
msgId, ts (ms), msgType (webchat/chat.photo/chat.sticker/...), content.
"""

import json

import requests

from core.zalo.enc import zalo_encode
from core.zalo.dec import zalo_decode
from core.zalo.zalo_headers import zalo_mobile_headers
from features.messaging.board_pin import _normalize_thumb

GET_LAST_MSGS_URL = "https://tt-convers-wpa.chat.zalo.me/api/preloadconvers/get-last-msgs"

# Giới hạn số thread mỗi request để URL không vượt giới hạn header (HTTP 431).
_MAX_THREADS_PER_CALL = 50


def _cookie_dict(cookies: str) -> dict:
    result = {}
    for item in (cookies or "").split(";"):
        item = item.strip()
        if "=" in item:
            key, value = item.split("=", 1)
            result[key.strip()] = value.strip()
    return result


def _parse_content(msg_type: str, content):
    """Rút (text, thumb, href) hiển thị được từ content theo msgType."""
    msg_type = str(msg_type or "")
    if isinstance(content, str):
        text = content.strip()
    elif isinstance(content, dict):
        text = str(content.get("title") or content.get("description") or "").strip()
    else:
        text = ""
    thumb = ""
    href = ""
    if isinstance(content, dict):
        thumb = _normalize_thumb(content.get("thumb") or content.get("oriUrl") or "")
        href = str(content.get("href") or content.get("normalUrl") or "").strip()

    if msg_type == "chat.sticker":
        text = "[Sticker]"
        # content sticker chứa id (đôi khi là chuỗi JSON) -> dựng URL ảnh sticker
        # theo đúng endpoint Zalo Web dùng để hiển thị.
        raw = content
        if isinstance(raw, str) and raw.strip().startswith("{"):
            try:
                raw = json.loads(raw)
            except Exception:
                raw = {}
        sticker_id = ""
        if isinstance(raw, dict):
            sticker_id = str(raw.get("id") or raw.get("stickerId") or "").strip()
        # Host đúng theo bundle Zalo Web: api.zaloapp.com (trả image/png).
        thumb = (
            f"https://api.zaloapp.com/api/emoticon/sticker/webpc?eid={sticker_id}&size=130"
            if sticker_id else ""
        )
    elif msg_type == "chat.voice":
        text = "[Tin nhắn thoại]"
    elif msg_type in ("chat.video", "chat.video.msg"):
        text = ("[Video] " + text).strip()
    elif msg_type == "chat.photo":
        text = ("[Hình ảnh] " + text).strip() if text and text != "[Hình ảnh]" else "[Hình ảnh]"
    elif msg_type == "chat.gif":
        text = "[Ảnh GIF]"
    elif msg_type == "chat.location":
        text = "[Vị trí]"
    elif msg_type == "share.file":
        text = ("[Tệp đính kèm] " + text).strip()
    elif msg_type == "chat.undo":
        text = "[Tin nhắn đã được thu hồi]"
        thumb = href = ""
    elif msg_type == "chat.delete":
        text = "[Tin nhắn đã bị xóa]"
        thumb = href = ""
    elif msg_type == "chat.recommended" and isinstance(content, dict):
        action = str(content.get("action") or "")
        if "misscall" in action:
            text = "[Cuộc gọi nhỡ]"
            thumb = href = ""
        elif "call" in action:
            text = "[Cuộc gọi]"
            thumb = href = ""
        elif action == "recommened.user":
            text = ("[Danh thiếp] " + str(content.get("title") or "")).strip()
            href = ""
        else:  # recommened.link và các loại chia sẻ khác
            text = str(content.get("title") or href or "[Liên kết]").strip()
    if not text and thumb:
        text = "[Hình ảnh]"
    return text, thumb, href


def _normalize_group_msg(m: dict) -> dict:
    text, thumb, href = _parse_content(m.get("msgType"), m.get("content"))
    return {
        "groupId": str(m.get("idTo") or "").strip(),
        "msgId": str(m.get("msgId") or "").strip(),
        "cliMsgId": str(m.get("cliMsgId") or "").strip(),
        "senderUid": str(m.get("uidFrom") or "").strip(),  # "0" = tài khoản đang dùng
        "msgType": str(m.get("msgType") or ""),
        "ts": int(m.get("ts") or 0),
        "text": text,
        "thumb": thumb,
        "href": href,
    }


def _normalize_user_msg(m: dict) -> dict:
    """Tin 1-1: threadId = uid của BẠN BÈ (uidFrom nếu họ gửi, idTo nếu mình gửi)."""
    text, thumb, href = _parse_content(m.get("msgType"), m.get("content"))
    uid_from = str(m.get("uidFrom") or "").strip()
    id_to = str(m.get("idTo") or "").strip()
    thread_id = uid_from if uid_from not in ("", "0") else id_to
    return {
        "threadId": thread_id,
        "msgId": str(m.get("msgId") or "").strip(),
        "cliMsgId": str(m.get("cliMsgId") or "").strip(),
        "senderUid": uid_from,  # "0" = tài khoản đang dùng
        "msgType": str(m.get("msgType") or ""),
        "ts": int(m.get("ts") or 0),
        "text": text,
        "thumb": thumb,
        "href": href,
    }


def fetch_last_messages(thread_map: dict, cookies: str, zpw_enk: str, imei: str,
                        zpw_ver: str = None, timeout: int = 20) -> dict:
    """Gọi get-last-msgs với map {"<threadId>_1|_0": "<msgId đã biết>"}.

    Returns:
        {"ok": bool, "groupMsgs": [tin nhóm], "userMsgs": [tin 1-1], "message": str}
    """
    from core.zalo.zalo_config import get_zpw_ver as _get_zpw_ver

    if not thread_map:
        return {"ok": True, "groupMsgs": [], "userMsgs": [], "message": "Không có thread nào cần kiểm tra"}

    # Quá nhiều thread trong 1 request GET làm URL vượt giới hạn (HTTP 431),
    # nên chia thành từng đợt nhỏ rồi gộp kết quả.
    if len(thread_map) > _MAX_THREADS_PER_CALL:
        entries = list(thread_map.items())
        all_group, all_user = [], []
        for i in range(0, len(entries), _MAX_THREADS_PER_CALL):
            part = dict(entries[i:i + _MAX_THREADS_PER_CALL])
            res = fetch_last_messages(part, cookies, zpw_enk, imei, zpw_ver=zpw_ver, timeout=timeout)
            if not res.get("ok"):
                res["groupMsgs"] = all_group + (res.get("groupMsgs") or [])
                res["userMsgs"] = all_user + (res.get("userMsgs") or [])
                return res
            all_group.extend(res.get("groupMsgs") or [])
            all_user.extend(res.get("userMsgs") or [])
        return {"ok": True, "groupMsgs": all_group, "userMsgs": all_user, "message": "OK"}

    zpw_ver = _get_zpw_ver(zpw_ver)
    payload = {
        "threadIdLocalMsgId": json.dumps(thread_map, separators=(",", ":")),
        "imei": imei,
    }
    plaintext = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    encoded = zalo_encode(plaintext, zpw_enk, url_encode=False)

    try:
        response = requests.get(
            GET_LAST_MSGS_URL,
            params={"zpw_ver": zpw_ver, "zpw_type": "30", "params": encoded},
            headers=zalo_mobile_headers(),
            cookies=_cookie_dict(cookies),
            timeout=timeout,
            # Gọi thẳng, không qua system proxy (tool bắt gói làm fail SSL).
            proxies={"http": None, "https": None},
        )
    except requests.exceptions.RequestException as e:
        return {"ok": False, "groupMsgs": [], "userMsgs": [], "message": f"Lỗi kết nối: {e}"}

    if response.status_code != 200:
        return {"ok": False, "groupMsgs": [], "userMsgs": [], "message": f"HTTP {response.status_code}"}

    try:
        resp_json = response.json()
    except Exception:
        return {"ok": False, "groupMsgs": [], "userMsgs": [], "message": "Response không phải JSON"}

    if resp_json.get("error_code", 0) not in (0, None):
        return {"ok": False, "groupMsgs": [], "userMsgs": [],
                "message": resp_json.get("error_message", "Lỗi API")}

    data_field = resp_json.get("data", "")
    if isinstance(data_field, str) and data_field:
        try:
            decoded = zalo_decode(data_field, zpw_enk)
        except Exception as e:
            return {"ok": False, "groupMsgs": [], "userMsgs": [], "message": f"Giải mã thất bại: {e}"}
    else:
        decoded = data_field
    if not isinstance(decoded, dict):
        return {"ok": False, "groupMsgs": [], "userMsgs": [], "message": "Response không hợp lệ"}

    inner_code = decoded.get("error_code", 0)
    if inner_code not in (0, None):
        return {"ok": False, "groupMsgs": [], "userMsgs": [],
                "message": decoded.get("error_message", "Lỗi API")}
    data_obj = decoded.get("data", decoded)
    if not isinstance(data_obj, dict):
        data_obj = {}

    group_msgs = []
    for m in data_obj.get("groupMsgs", []) or []:
        if not isinstance(m, dict):
            continue
        gm = _normalize_group_msg(m)
        if gm["groupId"] and gm["msgId"]:
            group_msgs.append(gm)

    user_msgs = []
    for m in data_obj.get("msgs", []) or []:
        if not isinstance(m, dict):
            continue
        um = _normalize_user_msg(m)
        if um["threadId"] and um["msgId"]:
            user_msgs.append(um)

    return {"ok": True, "groupMsgs": group_msgs, "userMsgs": user_msgs, "message": "OK"}
