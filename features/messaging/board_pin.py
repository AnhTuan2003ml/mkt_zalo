"""Lấy danh sách tin nhắn ghim (board pin) của nhóm Zalo.

API: GET https://groupboard-wpa.chat.zalo.me/api/board/pin/list
Payload trước mã hóa: {"groupId": "...", "boardVersion": 0, "imei": "..."}
Response sau giải mã: {"error_code": 0, "data": {"pinLimit", "groupId",
"boardVersion", "items": [{id, type, emoji, params, createTime, ...}]}}
Trường ``params`` của mỗi item là chuỗi JSON chứa nội dung tin nhắn gốc
(title, senderName, senderUid, msg_type, client_msg_id, global_msg_id).
"""

import json

import requests

from core.zalo.enc import zalo_encode
from core.zalo.dec import zalo_decode
from core.zalo.zalo_headers import zalo_mobile_headers

BOARD_PIN_LIST_URL = "https://groupboard-wpa.chat.zalo.me/api/board/pin/list"


def _is_up_to_date(message: str) -> bool:
    """API trả lỗi "Your data is up to date" khi nhóm không có tin ghim nào."""
    return "up to date" in str(message or "").lower()


def _cookie_dict(cookies: str) -> dict:
    result = {}
    for item in (cookies or "").split(";"):
        item = item.strip()
        if "=" in item:
            key, value = item.split("=", 1)
            result[key.strip()] = value.strip()
    return result


def _normalize_thumb(url: str) -> str:
    """Đổi ảnh JXL (trình duyệt không decode) sang bản JPG cùng CDN.

    Zalo phục vụ cùng một ảnh ở cả hai định dạng: thay ``/jxl/`` bằng
    ``/jpg/`` và đuôi ``.jxl`` bằng ``.jpg`` (bỏ query jxlstatus) là ra JPG.
    """
    url = str(url or "").strip()
    if ".jxl" in url:
        url = url.replace("/jxl/", "/jpg/").replace(".jxl", ".jpg")
        url = url.split("?")[0]
    return url


def _parse_pin_item(item: dict) -> dict:
    """Chuẩn hóa một pin item; giải JSON lồng trong trường ``params``."""
    params = item.get("params")
    if isinstance(params, str):
        try:
            params = json.loads(params)
        except Exception:
            params = {}
    if not isinstance(params, dict):
        params = {}
    return {
        "pinId": str(item.get("id") or "").strip(),
        "type": item.get("type", 0),
        "emoji": str(item.get("emoji") or "") or "📌",
        "title": str(params.get("title") or "").strip(),
        # msg_type 32 (ảnh) và 38 (link card) có thumb; link card thêm href.
        "thumb": _normalize_thumb(params.get("thumb")),
        "href": str(params.get("href") or params.get("linkCaption") or "").strip(),
        "senderUid": str(params.get("senderUid") or "").strip(),
        "senderName": str(params.get("senderName") or "").strip(),
        "clientMsgId": str(params.get("client_msg_id") or "").strip(),
        "globalMsgId": str(params.get("global_msg_id") or "").strip(),
        "msgType": params.get("msg_type") or 0,
        "creatorId": str(item.get("creatorId") or "").strip(),
        "createTime": int(item.get("createTime") or 0),
        "editTime": int(item.get("editTime") or 0),
    }


def fetch_board_pins(group_id: str, cookies: str, zpw_enk: str, imei: str,
                     zpw_ver: str = None, board_version: int = 0,
                     timeout: int = 15) -> dict:
    """Gọi board/pin/list của một nhóm và trả danh sách pin đã chuẩn hóa.

    Returns:
        {"ok": bool, "groupId": str, "boardVersion": int, "pinLimit": int,
         "items": [pin đã chuẩn hóa], "message": str}
    """
    from core.zalo.zalo_config import get_zpw_ver as _get_zpw_ver

    group_id = str(group_id or "").strip()
    if not group_id:
        return {"ok": False, "groupId": "", "boardVersion": 0, "pinLimit": 0,
                "items": [], "message": "Thiếu groupId"}

    zpw_ver = _get_zpw_ver(zpw_ver)
    payload = {
        "groupId": group_id,
        "boardVersion": int(board_version or 0),
        "imei": imei,
    }
    plaintext = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    encoded = zalo_encode(plaintext, zpw_enk, url_encode=False)

    try:
        response = requests.get(
            BOARD_PIN_LIST_URL,
            params={"zpw_ver": zpw_ver, "zpw_type": "30", "params": encoded},
            headers=zalo_mobile_headers(),
            cookies=_cookie_dict(cookies),
            timeout=timeout,
            # Gọi thẳng, không qua system proxy: các tool bắt gói (đặt proxy
            # 127.0.0.1:xxxx toàn hệ thống) sẽ làm fail SSL verification.
            proxies={"http": None, "https": None},
        )
    except requests.exceptions.RequestException as e:
        return {"ok": False, "groupId": group_id, "boardVersion": 0,
                "pinLimit": 0, "items": [], "message": f"Lỗi kết nối: {e}"}

    if response.status_code != 200:
        return {"ok": False, "groupId": group_id, "boardVersion": 0,
                "pinLimit": 0, "items": [], "message": f"HTTP {response.status_code}"}

    try:
        resp_json = response.json()
    except Exception:
        return {"ok": False, "groupId": group_id, "boardVersion": 0,
                "pinLimit": 0, "items": [], "message": "Response không phải JSON"}

    if resp_json.get("error_code", 0) not in (0, None):
        message = resp_json.get("error_message", "Lỗi API")
        if _is_up_to_date(message):
            return {"ok": True, "groupId": group_id, "boardVersion": 0,
                    "pinLimit": 0, "items": [], "message": "Nhóm không có tin ghim"}
        return {"ok": False, "groupId": group_id, "boardVersion": 0, "pinLimit": 0,
                "items": [], "message": message}

    data_field = resp_json.get("data", "")
    if isinstance(data_field, str) and data_field:
        try:
            decoded = zalo_decode(data_field, zpw_enk)
        except Exception as e:
            return {"ok": False, "groupId": group_id, "boardVersion": 0,
                    "pinLimit": 0, "items": [], "message": f"Giải mã thất bại: {e}"}
    else:
        decoded = data_field

    if isinstance(decoded, str):
        try:
            decoded = json.loads(decoded)
        except Exception:
            pass
    if not isinstance(decoded, dict):
        return {"ok": False, "groupId": group_id, "boardVersion": 0,
                "pinLimit": 0, "items": [], "message": "Response không hợp lệ"}

    # Bản giải mã có thể là {error_code, data: {...}} hoặc thẳng {...}.
    inner_code = decoded.get("error_code", 0)
    if inner_code not in (0, None):
        message = decoded.get("error_message", "Lỗi API")
        if _is_up_to_date(message):
            return {"ok": True, "groupId": group_id, "boardVersion": 0,
                    "pinLimit": 0, "items": [], "message": "Nhóm không có tin ghim"}
        return {"ok": False, "groupId": group_id, "boardVersion": 0, "pinLimit": 0,
                "items": [], "message": message}
    data_obj = decoded.get("data", decoded)
    if isinstance(data_obj, str):
        try:
            data_obj = json.loads(data_obj)
        except Exception:
            pass
    if not isinstance(data_obj, dict):
        data_obj = {}

    items = []
    for raw in data_obj.get("items", []) or []:
        if not isinstance(raw, dict):
            continue
        pin = _parse_pin_item(raw)
        if pin["pinId"]:
            items.append(pin)

    return {
        "ok": True,
        "groupId": str(data_obj.get("groupId") or group_id),
        "boardVersion": int(data_obj.get("boardVersion") or 0),
        "pinLimit": int(data_obj.get("pinLimit") or 0),
        "items": items,
        "message": "OK",
    }
