"""Lấy hoặc kích hoạt link tham gia nhóm Zalo."""

from __future__ import annotations

import json
from typing import Any

import requests

from core.zalo.dec import zalo_decode
from core.zalo.enc import zalo_encode
from core.zalo.zalo_config import get_zpw_ver
from core.zalo.zalo_headers import zalo_mobile_headers

GROUP_LINK_NEW_URL = "https://tt-group-wpa.chat.zalo.me/api/group/link/new"
GROUP_LINK_DETAIL_URL = "https://tt-group-wpa.chat.zalo.me/api/group/link/detail"


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


def _call_group_link_api(
    url: str,
    grid: str,
    imei: str,
    zpw_enk: str,
    cookies: str,
    *,
    zpw_ver: str | None = None,
    timeout: int = 30,
) -> tuple[dict, dict]:
    grid = str(grid or "").strip()
    imei = str(imei or "").strip()
    zpw_enk = str(zpw_enk or "").strip()
    cookies = str(cookies or "").strip()
    if not grid:
        raise ValueError("Group ID không được để trống.")
    if not imei:
        raise ValueError("IMEI không được để trống.")
    if not zpw_enk:
        raise ValueError("zpwEnk không được để trống.")
    if not cookies:
        raise ValueError("Cookies không được để trống.")

    payload = {"grid": grid, "imei": imei}
    plaintext = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    encoded_params = zalo_encode(plaintext, zpw_enk, url_encode=True)
    request_url = (
        f"{url}?zpw_ver={get_zpw_ver(zpw_ver)}"
        f"&zpw_type=30&params={encoded_params}"
    )

    response = requests.get(
        request_url,
        headers=zalo_mobile_headers(),
        cookies=_cookie_dict(cookies),
        timeout=timeout,
    )
    response.raise_for_status()
    response_json = response.json()
    data_field = response_json.get("data", "")
    if isinstance(data_field, str) and data_field:
        decoded_data = zalo_decode(data_field, zpw_enk)
    else:
        decoded_data = data_field
    return response_json, _decoded_dict(decoded_data)


def _normalize_group_link_result(response_json: dict, decoded_data: dict) -> dict:
    response_json = response_json if isinstance(response_json, dict) else {}
    decoded_data = _decoded_dict(decoded_data)
    raw_code = decoded_data.get("error_code", response_json.get("error_code", -1))
    try:
        error_code = int(raw_code)
    except (TypeError, ValueError):
        error_code = -1
    error_message = str(
        decoded_data.get("error_message")
        or decoded_data.get("message")
        or response_json.get("error_message")
        or response_json.get("message")
        or ""
    ).strip()

    data = decoded_data.get("data") if isinstance(decoded_data.get("data"), dict) else {}
    if not data and isinstance(response_json.get("data"), dict):
        data = response_json.get("data") or {}

    link = str(data.get("link") or "").strip()
    expiration_date = data.get("expiration_date", data.get("expirationDate", 0))
    try:
        expiration_date = int(expiration_date or 0)
    except (TypeError, ValueError):
        expiration_date = 0
    enabled = data.get("enabled", 0)
    try:
        enabled = int(enabled or 0)
    except (TypeError, ValueError):
        enabled = 0

    if error_code != 0:
        raise RuntimeError(error_message or f"Zalo trả về mã lỗi {error_code} khi lấy link nhóm.")
    if not link:
        raise RuntimeError("Zalo báo thành công nhưng không trả về link tham gia nhóm.")
    if enabled != 1:
        raise RuntimeError("Link tham gia nhóm hiện chưa được bật.")

    return {
        "link": link,
        "expirationDate": expiration_date,
        "enabled": enabled,
        "errorCode": error_code,
        "errorMessage": error_message,
        "response": response_json,
        "decoded": decoded_data,
    }


def create_group_link(
    grid: str,
    imei: str,
    zpw_enk: str,
    cookies: str,
    *,
    zpw_ver: str | None = None,
    timeout: int = 30,
) -> dict:
    """Kích hoạt và lấy link tham gia cho nhóm vừa tạo qua ``/link/new``."""
    response_json, decoded_data = _call_group_link_api(
        GROUP_LINK_NEW_URL,
        grid,
        imei,
        zpw_enk,
        cookies,
        zpw_ver=zpw_ver,
        timeout=timeout,
    )
    return _normalize_group_link_result(response_json, decoded_data)


def get_group_link_detail(
    grid: str,
    imei: str,
    zpw_enk: str,
    cookies: str,
    *,
    zpw_ver: str | None = None,
    timeout: int = 30,
) -> dict:
    """Lấy link tham gia hiện có của nhóm qua ``/link/detail``."""
    response_json, decoded_data = _call_group_link_api(
        GROUP_LINK_DETAIL_URL,
        grid,
        imei,
        zpw_enk,
        cookies,
        zpw_ver=zpw_ver,
        timeout=timeout,
    )
    return _normalize_group_link_result(response_json, decoded_data)
