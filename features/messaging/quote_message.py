"""Trả lời (quote) một tin nhắn Zalo.

- Tin 1-1: POST https://tt-chat2-wpa.chat.zalo.me/api/message/quote  (payload dùng toid)
- Tin nhóm: POST https://tt-group-wpa.chat.zalo.me/api/group/quote   (payload dùng grid)

Payload (trước mã hóa) tham chiếu tin gốc cần trả lời:
    {toid|grid, message, clientId, qmsgOwner, qmsgId, qmsgCliId, qmsgType,
     qmsgTs, qmsg, imei, qmsgAttach, qmsgTTL, ttl}
Response: {"error_code":0, "data":{"msgId":...}}
"""
import json
import time

import requests

from core.zalo.enc import zalo_encode
from core.zalo.dec import zalo_decode
from core.zalo.zalo_config import get_zpw_ver
from core.zalo.zalo_headers import zalo_mobile_headers

QUOTE_USER_URL = "https://tt-chat2-wpa.chat.zalo.me/api/message/quote"
QUOTE_GROUP_URL = "https://tt-group-wpa.chat.zalo.me/api/group/quote"

# Loại tin (string trong get-last-msgs) -> số qmsgType Zalo Web dùng khi quote.
# chat.sticker=36 lấy trực tiếp từ request thật; các loại khác dùng giá trị phổ biến,
# text (webchat) là mặc định vì đa số tin được trả lời là tin chữ.
_QMSG_TYPE_MAP = {
    "webchat": 1,
    "chat.photo": 32,
    "chat.sticker": 36,
    "chat.gif": 49,
    "chat.voice": 31,
    "chat.video": 44,
    "chat.video.msg": 44,
    "share.file": 46,
    "chat.location": 43,
    "chat.recommended": 25,
}


def _cookie_dict(cookies):
    result = {}
    for item in str(cookies or "").split(";"):
        item = item.strip()
        if "=" in item:
            k, v = item.split("=", 1)
            result[k] = v
    return result


def quote_type_of(msg_type):
    return _QMSG_TYPE_MAP.get(str(msg_type or ""), 1)


def quote_message(
    cookies,
    zpw_enk,
    imei,
    *,
    thread_id,
    message,
    is_group=False,
    qmsg_owner="",
    qmsg_id="",
    qmsg_cli_id="",
    qmsg_type="webchat",
    qmsg_ts="",
    qmsg_text="",
    qmsg_attach="",
    zpw_ver=None,
    timeout=30,
):
    """Gửi tin trả lời (quote). thread_id là uid (1-1) hoặc groupId (nhóm)."""
    thread_id = str(thread_id or "").strip()
    message = str(message or "").strip()
    if not thread_id:
        raise ValueError("Thiếu người/nhóm nhận.")
    if not message:
        raise ValueError("Nội dung trả lời không được để trống.")
    if not qmsg_id or not qmsg_cli_id:
        raise ValueError("Thiếu thông tin tin nhắn gốc để trả lời.")

    client_id = int(time.time() * 1000)
    payload = {
        "message": message,
        "clientId": client_id,
        "qmsgOwner": str(qmsg_owner or thread_id),
        "qmsgId": str(qmsg_id),
        "qmsgCliId": str(qmsg_cli_id),
        "qmsgType": quote_type_of(qmsg_type),
        "qmsgTs": str(qmsg_ts or ""),
        "qmsg": str(qmsg_text or ""),
        "imei": str(imei or ""),
        "qmsgAttach": str(qmsg_attach or ""),
        "qmsgTTL": 0,
        "ttl": 0,
    }
    if is_group:
        payload["grid"] = thread_id
        payload["visibility"] = 0
        payload["mentionInfo"] = ""
        url = QUOTE_GROUP_URL
    else:
        payload["toid"] = thread_id
        url = QUOTE_USER_URL

    plaintext = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    params = zalo_encode(plaintext, zpw_enk, url_encode=False)

    response = requests.post(
        url,
        params={"zpw_ver": get_zpw_ver(zpw_ver), "zpw_type": "30", "nretry": "0"},
        data={"params": params},
        headers=zalo_mobile_headers(),
        cookies=_cookie_dict(cookies),
        timeout=timeout,
        proxies={"http": None, "https": None},
    )
    response.raise_for_status()
    response_json = response.json()

    data_field = response_json.get("data", "")
    if isinstance(data_field, str) and data_field:
        try:
            decoded = zalo_decode(data_field, zpw_enk)
        except Exception:
            decoded = {}
    else:
        decoded = data_field

    outer = response_json.get("error_code", 0)
    inner = decoded.get("error_code", 0) if isinstance(decoded, dict) else 0
    ok = outer in (0, None) and inner in (0, None)
    message_text = ""
    if isinstance(decoded, dict):
        message_text = str(decoded.get("error_message") or "")
    if not message_text:
        message_text = str(response_json.get("error_message") or "")
    return {"ok": ok, "code": outer or inner, "message": message_text, "raw": response_json}
