"""Lấy danh sách bạn bè của tài khoản Zalo.

Dùng đúng API mà Zalo Web gọi (commandId 11137):
    GET https://tt-profile-wpa.chat.zalo.me/api/social/friend/getfriends
với query `params` là JSON mã hóa AES bằng zpw_enk:
    {"incInvalid":1,"page":1,"count":20000,"avatar_size":120,"actiontime":0,"imei":...}
Response: {"error_code":0,"data":"<AES>"} -> zalo_decode -> {"data":[...bạn bè...]}
"""
import json

import requests

from core.zalo.enc import zalo_encode
from core.zalo.dec import zalo_decode
from core.zalo.zalo_config import get_zpw_ver
from core.zalo.zalo_headers import zalo_mobile_headers

FRIENDS_URL = "https://tt-profile-wpa.chat.zalo.me/api/social/friend/getfriends"


def _cookie_dict(cookies):
    result = {}
    for item in str(cookies or "").split(";"):
        item = item.strip()
        if "=" in item:
            key, value = item.split("=", 1)
            result[key] = value
    return result


def _normalize_friend(item):
    if not isinstance(item, dict):
        return None
    uid = str(item.get("userId") or item.get("uid") or item.get("id") or "").strip()
    if not uid:
        return None
    return {
        "userId": uid,
        "zaloName": item.get("zaloName") or item.get("displayName") or item.get("dName") or "",
        "displayName": item.get("displayName") or item.get("zaloName") or item.get("dName") or "",
        "username": item.get("username") or item.get("globalId") or "",
        "avatar": item.get("avatar") or item.get("avatar_25") or "",
        "gender": item.get("gender", None),
        "sdob": item.get("sdob") or item.get("dob_str") or "",
        "dob": item.get("dob", 0),
        "phoneNumber": item.get("phoneNumber") or item.get("phone") or "",
        "status": item.get("status") or "",
        "lastActionTime": item.get("lastActionTime") or item.get("actionTime") or 0,
    }


def get_friend_list(
    zpw_enk,
    cookies,
    imei="",
    zpw_ver=None,
    avatar_size=120,
    page=1,
    count=20000,
    timeout=30,
):
    """Trả về danh sách bạn bè đã chuẩn hóa của tài khoản (list[dict])."""
    payload = {
        "incInvalid": 1,
        "page": int(page),
        "count": int(count),
        "avatar_size": int(avatar_size),
        "actiontime": 0,
        "imei": str(imei or ""),
    }
    plaintext = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    params = zalo_encode(plaintext, zpw_enk, url_encode=False)

    response = requests.get(
        FRIENDS_URL,
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
            f"Zalo trả lỗi {error_code}: {response_json.get('error_message') or 'không lấy được danh sách bạn bè'}"
        )

    data_field = response_json.get("data", "")
    if isinstance(data_field, str) and data_field:
        decoded = zalo_decode(data_field, zpw_enk)
    else:
        decoded = data_field

    # decoded có thể là {"data":[...]} hoặc trực tiếp [...]
    raw_items = []
    if isinstance(decoded, dict):
        inner = decoded.get("data", [])
        if isinstance(inner, list):
            raw_items = inner
        elif isinstance(inner, dict):
            raw_items = list(inner.values())
    elif isinstance(decoded, list):
        raw_items = decoded

    friends = []
    seen = set()
    for item in raw_items:
        friend = _normalize_friend(item)
        if friend and friend["userId"] not in seen:
            seen.add(friend["userId"])
            friends.append(friend)

    # Sắp xếp theo tên cho dễ nhìn
    friends.sort(key=lambda f: (f.get("zaloName") or f.get("displayName") or "").lower())
    return friends
