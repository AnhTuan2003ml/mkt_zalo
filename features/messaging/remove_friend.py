"""Xóa một người khỏi danh sách bạn bè Zalo.

Chỉ module gọi API. Quyết định có được xóa hay không thuộc về worker chiến dịch:
worker chỉ gọi hàm này với người đã được chính chiến dịch gửi lời mời kết bạn
thành công và sau đó đã được xác nhận có mặt trong nhóm đích.
"""

from __future__ import annotations

import json
from typing import Any

import requests

from core.zalo.dec import zalo_decode
from core.zalo.enc import zalo_encode
from core.zalo.zalo_config import get_zpw_ver
from core.zalo.zalo_headers import zalo_mobile_headers

FRIEND_REMOVE_URL = "https://tt-friend-wpa.chat.zalo.me/api/friend/remove"


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


def remove_friend(
    fid: str,
    imei: str,
    zpw_enk: str,
    cookies: str,
    *,
    zpw_ver: str | None = None,
    timeout: int = 30,
) -> tuple[dict, dict]:
    """Gọi ``/api/friend/remove`` với payload ``fid`` và ``imei``."""
    fid = str(fid or "").strip()
    imei = str(imei or "").strip()
    zpw_enk = str(zpw_enk or "").strip()
    cookies = str(cookies or "").strip()
    if not fid:
        raise ValueError("fid không được để trống.")
    if not imei:
        raise ValueError("IMEI không được để trống.")
    if not zpw_enk:
        raise ValueError("zpwEnk không được để trống.")
    if not cookies:
        raise ValueError("Cookies không được để trống.")

    payload = {"fid": fid, "imei": imei}
    plaintext = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    encoded_params = zalo_encode(plaintext, zpw_enk, url_encode=True)

    response = requests.post(
        FRIEND_REMOVE_URL,
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
