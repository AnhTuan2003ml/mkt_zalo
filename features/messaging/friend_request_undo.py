"""Thu hồi lời mời kết bạn đã gửi của tài khoản Zalo.

Gồm 2 API của Zalo Web:
- GET  /api/friend/requested/list -> danh sách lời mời kết bạn ĐÃ GỬI đang chờ
  người nhận chấp nhận.
- POST /api/friend/undo          -> thu hồi 1 lời mời (payload {"fid": <userId>}).

Worker sao chép nhóm dùng 2 hàm này để quét định kỳ và thu hồi những lời mời
đã gửi quá N ngày mà người nhận vẫn chưa chấp nhận.
"""

from __future__ import annotations

import json
from typing import Any

import requests

from core.zalo.dec import zalo_decode
from core.zalo.enc import zalo_encode
from core.zalo.zalo_config import get_zpw_ver
from core.zalo.zalo_headers import zalo_mobile_headers

REQUESTED_LIST_URL = "https://tt-friend-wpa.chat.zalo.me/api/friend/requested/list"
FRIEND_UNDO_URL = "https://tt-friend-wpa.chat.zalo.me/api/friend/undo"


def _cookie_dict(cookies: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in str(cookies or "").split(";"):
        item = item.strip()
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        result[key.strip()] = value.strip()
    return result


def _decoded_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {"raw": value}
        except Exception:
            return {"raw": value}
    return {}


def _normalize_requested_item(item: Any) -> dict | None:
    if not isinstance(item, dict):
        return None
    uid = str(item.get("userId") or item.get("uid") or item.get("id") or "").strip()
    if not uid:
        return None
    freq = item.get("fReqInfo") if isinstance(item.get("fReqInfo"), dict) else {}
    try:
        sent_at = int(freq.get("time") or item.get("time") or 0)
    except (TypeError, ValueError):
        sent_at = 0
    return {
        "userId": uid,
        "zaloName": item.get("zaloName") or item.get("displayName") or "",
        "displayName": item.get("displayName") or item.get("zaloName") or "",
        "avatar": item.get("avatar") or item.get("avatar_25") or "",
        "globalId": item.get("globalId") or "",
        "message": freq.get("message") or item.get("message") or "",
        "src": freq.get("src") if freq.get("src") is not None else item.get("src"),
        # Thời điểm gửi lời mời (unix giây) — dùng để so ngưỡng 3 ngày.
        "sentAt": sent_at,
    }


def get_sent_friend_requests(
    imei: str,
    zpw_enk: str,
    cookies: str,
    *,
    zpw_ver: str | None = None,
    timeout: int = 30,
) -> dict[str, dict]:
    """Trả về map ``userId -> thông tin lời mời kết bạn đã gửi`` đang chờ chấp nhận.

    Chỉ trả các lời mời CHƯA được chấp nhận (Zalo tự loại khi đã thành bạn bè).
    """
    imei = str(imei or "").strip()
    zpw_enk = str(zpw_enk or "").strip()
    cookies = str(cookies or "").strip()
    if not zpw_enk:
        raise ValueError("zpwEnk không được để trống.")
    if not cookies:
        raise ValueError("Cookies không được để trống.")

    payload = {"imei": imei}
    plaintext = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    params = zalo_encode(plaintext, zpw_enk, url_encode=False)

    response = requests.get(
        REQUESTED_LIST_URL,
        params={
            "zpw_ver": get_zpw_ver(zpw_ver),
            "zpw_type": "30",
            "params": params,
        },
        headers=zalo_mobile_headers(),
        cookies=_cookie_dict(cookies),
        timeout=timeout,
    )
    response.raise_for_status()
    response_json = response.json()

    error_code = response_json.get("error_code", 0)
    if error_code not in (0, "0", None):
        raise RuntimeError(
            f"Zalo trả lỗi {error_code}: {response_json.get('error_message') or 'không lấy được danh sách lời mời đã gửi'}"
        )

    data_field = response_json.get("data", "")
    if isinstance(data_field, str) and data_field:
        decoded = zalo_decode(data_field, zpw_enk)
    else:
        decoded = data_field

    raw: Any = decoded
    if isinstance(raw, dict) and isinstance(raw.get("data"), (dict, list)):
        raw = raw["data"]

    if isinstance(raw, list):
        iterable = raw
    elif isinstance(raw, dict):
        iterable = list(raw.values())
    else:
        iterable = []

    result: dict[str, dict] = {}
    for item in iterable:
        info = _normalize_requested_item(item)
        if info:
            result[info["userId"]] = info
    return result


def undo_friend_request(
    fid: str,
    zpw_enk: str,
    cookies: str,
    *,
    zpw_ver: str | None = None,
    timeout: int = 30,
) -> tuple[dict, dict]:
    """Thu hồi 1 lời mời kết bạn đã gửi tới ``fid``.

    Payload mã hóa trước khi gửi: ``{"fid": "<userId>"}``.
    """
    fid = str(fid or "").strip()
    zpw_enk = str(zpw_enk or "").strip()
    cookies = str(cookies or "").strip()
    if not fid:
        raise ValueError("fid không được để trống.")
    if not zpw_enk:
        raise ValueError("zpwEnk không được để trống.")
    if not cookies:
        raise ValueError("Cookies không được để trống.")

    payload = {"fid": fid}
    plaintext = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    encoded_params = zalo_encode(plaintext, zpw_enk, url_encode=True)

    response = requests.post(
        FRIEND_UNDO_URL,
        params={"zpw_ver": get_zpw_ver(zpw_ver), "zpw_type": "30"},
        data=f"params={encoded_params}",
        headers=zalo_mobile_headers(),
        cookies=_cookie_dict(cookies),
        timeout=timeout,
    )
    response.raise_for_status()
    try:
        response_json = response.json()
    except Exception as exc:
        raise RuntimeError("Zalo response không phải JSON: " + response.text[:500]) from exc

    data_field = response_json.get("data", "")
    if isinstance(data_field, str) and data_field:
        decoded = zalo_decode(data_field, zpw_enk)
    else:
        decoded = data_field
    return response_json, _decoded_dict(decoded)
