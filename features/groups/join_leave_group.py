"""
Hàm vào nhóm / rời nhóm Zalo Web.

Dùng cho các luồng cần tạm tham gia nhóm bằng link để lấy dữ liệu hợp lệ,
sau đó rời nhóm silent. Module này chỉ thực hiện request API; UI quyết định
có hiển thị thông báo hay không.
"""
import json
from typing import Any, Dict, Tuple

import requests

from core.zalo.enc import zalo_encode
from core.zalo.dec import zalo_decode
from core.zalo.zalo_config import get_zpw_ver
from core.zalo.zalo_headers import zalo_mobile_headers

JOIN_GROUP_URL = "https://tt-group-wpa.chat.zalo.me/api/group/link/join"
LEAVE_GROUP_URL = "https://tt-group-wpa.chat.zalo.me/api/group/leave"


def _cookies_str_to_dict(cookies: str) -> dict:
    cookie_dict = {}
    for item in (cookies or "").split(";"):
        item = item.strip()
        if "=" in item:
            key, value = item.split("=", 1)
            cookie_dict[key.strip()] = value.strip()
    return cookie_dict


def _compact_json(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _decode_response_data(resp_json: Dict[str, Any], zpw_enk: str) -> Any:
    data_field = resp_json.get("data") if isinstance(resp_json, dict) else None
    if isinstance(data_field, str) and data_field:
        return zalo_decode(data_field, zpw_enk)
    return data_field if data_field is not None else resp_json


def _safe_json_response(response: requests.Response) -> Dict[str, Any]:
    try:
        return response.json()
    except Exception:
        return {
            "_raw_text": response.text,
            "_http_status": response.status_code,
        }


def normalize_decoded(decoded: Any) -> Dict[str, Any]:
    """Đưa decoded về dict để các nơi gọi dễ kiểm tra."""
    if isinstance(decoded, dict):
        return decoded
    if isinstance(decoded, str):
        try:
            parsed = json.loads(decoded)
            return parsed if isinstance(parsed, dict) else {"raw": parsed}
        except Exception:
            return {"raw": decoded}
    if decoded is None:
        return {}
    return {"raw": decoded}


def is_zalo_success(response_json: Dict[str, Any], decoded: Any) -> bool:
    """
    Thành công khi decoded.error_code = 0.
    Nếu decoded không có error_code thì fallback sang response_json.error_code.
    """
    decoded_obj = normalize_decoded(decoded)
    error_code = decoded_obj.get("error_code")
    if error_code is None and isinstance(response_json, dict):
        error_code = response_json.get("error_code")

    try:
        return int(error_code or 0) == 0
    except Exception:
        return False


def join_group_by_link(
    link: str,
    zpw_enk: str,
    cookies: str,
    zpw_ver: str = None,
    timeout: int = 30,
) -> Tuple[Dict[str, Any], Any]:
    """
    Vào nhóm bằng link.

    Endpoint:
        GET /api/group/link/join?zpw_ver=...&zpw_type=30&params=...

    Payload trước mã hóa:
        {
          "link": "https://zalo.me/g/...",
          "clientLang": "vi"
        }

    Returns:
        (response_json, decoded_data)
    """
    link = (link or "").strip()
    if not link:
        raise ValueError("Thiếu link nhóm để vào nhóm")
    if not zpw_enk:
        raise ValueError("Thiếu zpwEnk")
    if not cookies:
        raise ValueError("Thiếu cookies")

    zpw_ver = get_zpw_ver(zpw_ver)
    payload = {
        "link": link,
        "clientLang": "vi",
    }

    plaintext = _compact_json(payload)
    # Với GET, để requests tự URL-encode query string, tránh double encode.
    encoded = zalo_encode(plaintext, zpw_enk, url_encode=False)

    response = requests.get(
        JOIN_GROUP_URL,
        params={
            "zpw_ver": zpw_ver,
            "zpw_type": "30",
            "params": encoded,
        },
        headers=zalo_mobile_headers(),
        cookies=_cookies_str_to_dict(cookies),
        timeout=timeout,
    )

    response_json = _safe_json_response(response)
    try:
        decoded_data = _decode_response_data(response_json, zpw_enk)
    except Exception as exc:
        decoded_data = {
            "_decode_error": str(exc),
            "_response_json": response_json,
        }

    return response_json, decoded_data


def leave_group(
    grid: str,
    imei: str,
    zpw_enk: str,
    cookies: str,
    zpw_ver: str = None,
    silent: int = 1,
    timeout: int = 30,
) -> Tuple[Dict[str, Any], Any]:
    """
    Rời nhóm.

    Endpoint:
        POST /api/group/leave?zpw_ver=...&zpw_type=30

    Payload trước mã hóa:
        {
          "grids": ["9025632183667716948"],
          "imei": "...",
          "silent": 1,
          "language": "vi"
        }

    Returns:
        (response_json, decoded_data)
    """
    grid = str(grid or "").strip()
    if not grid:
        raise ValueError("Thiếu groupId để rời nhóm")
    if not zpw_enk:
        raise ValueError("Thiếu zpwEnk")
    if not cookies:
        raise ValueError("Thiếu cookies")

    zpw_ver = get_zpw_ver(zpw_ver)
    payload = {
        "grids": [grid],
        "imei": str(imei or ""),
        "silent": int(silent),
        "language": "vi",
    }

    plaintext = _compact_json(payload)
    encoded = zalo_encode(plaintext, zpw_enk, url_encode=True)

    response = requests.post(
        LEAVE_GROUP_URL,
        params={"zpw_ver": zpw_ver, "zpw_type": "30"},
        data=f"params={encoded}",
        headers=zalo_mobile_headers(),
        cookies=_cookies_str_to_dict(cookies),
        timeout=timeout,
    )

    response_json = _safe_json_response(response)
    try:
        decoded_data = _decode_response_data(response_json, zpw_enk)
    except Exception as exc:
        decoded_data = {
            "_decode_error": str(exc),
            "_response_json": response_json,
        }

    return response_json, decoded_data


def leave_groups(
    grids,
    imei: str,
    zpw_enk: str,
    cookies: str,
    zpw_ver: str = None,
    silent: int = 1,
    timeout: int = 30,
) -> Tuple[Dict[str, Any], Any]:
    """
    Rời nhiều nhóm trong một request.

    Payload giống leave_group nhưng grids là list.
    """
    clean_grids = []
    seen = set()
    for grid in grids or []:
        gid = str(grid or "").strip()
        if gid and gid not in seen:
            seen.add(gid)
            clean_grids.append(gid)

    if not clean_grids:
        raise ValueError("Thiếu danh sách groupId để rời nhóm")
    if not zpw_enk:
        raise ValueError("Thiếu zpwEnk")
    if not cookies:
        raise ValueError("Thiếu cookies")

    zpw_ver = get_zpw_ver(zpw_ver)
    payload = {
        "grids": clean_grids,
        "imei": str(imei or ""),
        "silent": int(silent),
        "language": "vi",
    }

    plaintext = _compact_json(payload)
    encoded = zalo_encode(plaintext, zpw_enk, url_encode=True)

    response = requests.post(
        LEAVE_GROUP_URL,
        params={"zpw_ver": zpw_ver, "zpw_type": "30"},
        data=f"params={encoded}",
        headers=zalo_mobile_headers(),
        cookies=_cookies_str_to_dict(cookies),
        timeout=timeout,
    )

    response_json = _safe_json_response(response)
    try:
        decoded_data = _decode_response_data(response_json, zpw_enk)
    except Exception as exc:
        decoded_data = {
            "_decode_error": str(exc),
            "_response_json": response_json,
        }

    return response_json, decoded_data
