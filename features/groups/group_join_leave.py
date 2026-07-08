"""API tham gia/rời nhóm Zalo theo session account hiện tại.

Module này chỉ gom logic request/mã hóa/giải mã để app.py và service dùng chung.
Không hard-code cookie, zpw_enk hay imei; mọi thông tin lấy từ account đã đăng nhập.
"""

import json
from typing import Any, Dict, Iterable, List, Tuple
import requests

from core.zalo.dec import zalo_decode
from core.zalo.enc import zalo_encode
from core.zalo.zalo_config import get_zpw_ver
from core.zalo.zalo_headers import zalo_mobile_headers

JOIN_LINK_URL = "https://tt-group-wpa.chat.zalo.me/api/group/link/join"
LEAVE_GROUP_URL = "https://tt-group-wpa.chat.zalo.me/api/group/leave"


def zalo_desktop_headers(extra=None):
    """Headers giống request capture từ chat.zalo.me desktop."""
    headers = {
        "accept": "application/json, text/plain, */*",
        "accept-language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
        "cache-control": "no-cache",
        "content-type": "application/x-www-form-urlencoded",
        "origin": "https://chat.zalo.me",
        "pragma": "no-cache",
        "priority": "u=1, i",
        "referer": "https://chat.zalo.me/",
        "sec-ch-ua": '"Google Chrome";v="149", "Chromium";v="149", "Not)A;Brand";v="24"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-site",
        "user-agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/149.0.0.0 Safari/537.36"
        ),
    }
    if extra:
        headers.update(extra)
    return headers


def cookies_str_to_dict(cookies: str) -> dict:
    cookie_dict = {}
    for item in (cookies or "").split(";"):
        item = item.strip()
        if "=" in item:
            key, value = item.split("=", 1)
            cookie_dict[key.strip()] = value.strip()
    return cookie_dict


def _as_json_obj(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return value
    return value


def _decode_zalo_response(response_json: dict, zpw_enk: str) -> Any:
    data_field = response_json.get("data", "") if isinstance(response_json, dict) else ""
    if isinstance(data_field, str) and data_field:
        return zalo_decode(data_field, zpw_enk)
    return data_field


def _extract_api_error(decoded: Any, response_json: dict = None) -> Tuple[int, str]:
    """Trả về (error_code, error_message) ưu tiên decoded rồi response_json."""
    decoded = _as_json_obj(decoded)
    response_json = response_json if isinstance(response_json, dict) else {}

    candidates = []
    if isinstance(decoded, dict):
        candidates.append(decoded)
        data_obj = _as_json_obj(decoded.get("data"))
        if isinstance(data_obj, dict):
            candidates.append(data_obj)
    candidates.append(response_json)

    for obj in candidates:
        if not isinstance(obj, dict):
            continue
        code = obj.get("error_code")
        msg = obj.get("error_message") or obj.get("message") or ""
        if code not in (None, ""):
            try:
                return int(code), str(msg or "")
            except Exception:
                return code, str(msg or "")
    return 0, ""


def _ok_result(decoded: Any, response_json: dict) -> bool:
    code, _msg = _extract_api_error(decoded, response_json)
    return code in (0, None)


def _extract_group_id_from_decoded(decoded: Any) -> str:
    decoded = _as_json_obj(decoded)
    if not isinstance(decoded, dict):
        return ""

    candidates = [decoded]
    data_obj = _as_json_obj(decoded.get("data"))
    if isinstance(data_obj, dict):
        candidates.append(data_obj)
        for value in data_obj.values():
            value = _as_json_obj(value)
            if isinstance(value, dict):
                candidates.append(value)

    for obj in candidates:
        gid = str(
            obj.get("groupId")
            or obj.get("gridId")
            or obj.get("grid")
            or obj.get("id")
            or ""
        ).strip()
        if gid:
            return gid
    return ""


def join_group_by_link(
    link: str,
    zpw_enk: str,
    cookies: str,
    zpw_ver: str = None,
    timeout: int = 30,
) -> dict:
    """Tham gia nhóm bằng link mời Zalo.

    Payload trước mã hóa:
        {"link": "https://zalo.me/g/...", "clientLang": "vi"}

    Endpoint thực tế dùng GET và đưa params đã mã hóa lên query string.
    """
    link = str(link or "").strip()
    if not link:
        return {"ok": False, "message": "Thiếu link nhóm", "groupId": ""}
    if not zpw_enk:
        return {"ok": False, "message": "Thiếu zpw_enk", "groupId": ""}

    # Zalo nhận link dạng đầy đủ. Nếu người dùng chỉ nhập zalo.me/g/xxx thì tự thêm https://
    if link.startswith("zalo.me/g/"):
        link = "https://" + link

    payload = {"link": link, "clientLang": "vi"}
    plaintext = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    # Quan trọng: endpoint join là GET. Không truyền `params={"params": encoded}`
    # sau khi đã url_encode=True, vì requests sẽ encode thêm lần nữa (% -> %25)
    # và Zalo trả "Tham số không hợp lệ". Ghép query thủ công để giữ params
    # đúng dạng capture: ...&params=abc%2Bxyz%3D%3D
    encoded = zalo_encode(plaintext, zpw_enk, url_encode=True)
    join_url = (
        f"{JOIN_LINK_URL}?zpw_ver={get_zpw_ver(zpw_ver)}"
        f"&zpw_type=30&params={encoded}"
    )

    headers = zalo_desktop_headers()
    cookie_dict = cookies_str_to_dict(cookies)

    try:
        response = requests.get(
            join_url,
            headers=headers,
            cookies=cookie_dict,
            timeout=timeout,
        )
        if response.status_code != 200:
            return {"ok": False, "message": f"HTTP {response.status_code}", "groupId": ""}

        response_json = response.json()
        decoded = _decode_zalo_response(response_json, zpw_enk)
        code, msg = _extract_api_error(decoded, response_json)
        ok = _ok_result(decoded, response_json)
        return {
            "ok": ok,
            "message": msg or ("Tham gia nhóm thành công" if ok else f"API join trả lỗi: {code}"),
            "error_code": code,
            "groupId": _extract_group_id_from_decoded(decoded),
            "response": response_json,
            "decoded": decoded,
        }
    except requests.exceptions.RequestException as e:
        return {"ok": False, "message": f"Lỗi kết nối join nhóm: {e}", "groupId": ""}
    except Exception as e:
        return {"ok": False, "message": f"Lỗi join nhóm: {e}", "groupId": ""}


def leave_group(
    grids: Iterable[str],
    imei: str,
    zpw_enk: str,
    cookies: str,
    silent: int = 1,
    language: str = "vi",
    zpw_ver: str = None,
    timeout: int = 30,
) -> dict:
    """Rời một hoặc nhiều nhóm Zalo.

    Payload trước mã hóa:
        {"grids": ["..."], "imei": "...", "silent": 1, "language": "vi"}
    """
    grid_list: List[str] = [str(x).strip() for x in (grids or []) if str(x or "").strip()]
    if not grid_list:
        return {"ok": False, "message": "Thiếu groupId cần rời", "grids": []}
    if not zpw_enk:
        return {"ok": False, "message": "Thiếu zpw_enk", "grids": grid_list}

    payload = {
        "grids": grid_list,
        "imei": str(imei or ""),
        "silent": int(silent),
        "language": language or "vi",
    }
    plaintext = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    encoded = zalo_encode(plaintext, zpw_enk, url_encode=True)

    headers = zalo_desktop_headers()
    cookie_dict = cookies_str_to_dict(cookies)

    try:
        response = requests.post(
            LEAVE_GROUP_URL,
            params={"zpw_ver": get_zpw_ver(zpw_ver), "zpw_type": "30"},
            data=f"params={encoded}",
            headers=headers,
            cookies=cookie_dict,
            timeout=timeout,
        )
        if response.status_code != 200:
            return {"ok": False, "message": f"HTTP {response.status_code}", "grids": grid_list}

        response_json = response.json()
        decoded = _decode_zalo_response(response_json, zpw_enk)
        code, msg = _extract_api_error(decoded, response_json)
        ok = _ok_result(decoded, response_json)
        return {
            "ok": ok,
            "message": msg or ("Rời nhóm thành công" if ok else f"API leave trả lỗi: {code}"),
            "error_code": code,
            "grids": grid_list,
            "response": response_json,
            "decoded": decoded,
        }
    except requests.exceptions.RequestException as e:
        return {"ok": False, "message": f"Lỗi kết nối rời nhóm: {e}", "grids": grid_list}
    except Exception as e:
        return {"ok": False, "message": f"Lỗi rời nhóm: {e}", "grids": grid_list}
