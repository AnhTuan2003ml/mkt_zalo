"""
Module kiểm tra riêng endpoint Zalo Web:
    POST https://tt-group-wpa.chat.zalo.me/api/group/getmg

Mục đích:
- Chạy độc lập để xem response thật của API getmg.
- Tự đọc cookies / zpwEnk / imei từ data/accounts.json.
- Cho phép dán groupId hoặc link nhóm Zalo.
- Không import vào flow chính của app nếu chưa chọn endpoint cuối cùng.

Cách chạy từ thư mục gốc dự án:
    python -m features.members.group_getmg

Hoặc chạy trực tiếp:
    python features/members/group_getmg.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

import requests


# Cho phép chạy trực tiếp file: python features/members/group_getmg.py
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.zalo.enc import zalo_encode
from core.zalo.dec import zalo_decode
from core.zalo.zalo_config import get_zpw_ver
from core.zalo.zalo_headers import zalo_mobile_headers

GETMG_URL = "https://tt-group-wpa.chat.zalo.me/api/group/getmg"
LINK_GINFO_URL = "https://tt-group-wpa.chat.zalo.me/api/group/link/ginfo"
DEFAULT_MCOUNT = 500
DEFAULT_TIMEOUT = 30


def _project_root() -> Path:
    """Tìm root dự án dựa theo vị trí file hiện tại."""
    cur = Path(__file__).resolve()
    for parent in [cur.parent, *cur.parents]:
        if (parent / "app.py").exists() and (parent / "core").exists():
            return parent
    return PROJECT_ROOT


def _accounts_path() -> Path:
    return _project_root() / "data" / "accounts.json"


def load_accounts() -> List[Dict[str, Any]]:
    path = _accounts_path()
    if not path.exists():
        raise FileNotFoundError(
            f"Không thấy {path}. Hãy mở tool và capture account trước."
        )
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    accounts = data.get("accounts", []) if isinstance(data, dict) else []
    if not isinstance(accounts, list):
        return []
    usable = []
    for acc in accounts:
        if not isinstance(acc, dict):
            continue
        if acc.get("cookies") and acc.get("zpwEnk"):
            usable.append(acc)
    return usable


def choose_account(accounts: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not accounts:
        raise RuntimeError("Không có account nào đủ cookies + zpwEnk trong data/accounts.json")

    env_account_id = os.environ.get("ZALO_ACCOUNT_ID", "").strip()
    if env_account_id:
        for acc in accounts:
            if str(acc.get("accountId", "")) == env_account_id:
                return acc
        raise RuntimeError(f"Không tìm thấy ZALO_ACCOUNT_ID={env_account_id}")

    if len(accounts) == 1:
        acc = accounts[0]
        print(f"Dùng account duy nhất: {acc.get('name') or acc.get('accountId')}")
        return acc

    print("Danh sách account có thể dùng:")
    for i, acc in enumerate(accounts, start=1):
        print(
            f"  {i}. {acc.get('name') or 'No name'} | "
            f"accountId={acc.get('accountId')} | "
            f"cookies={len(acc.get('cookies') or '')} ký tự | "
            f"zpwEnk={len(acc.get('zpwEnk') or '')} ký tự | "
            f"imei={'có' if acc.get('imei') else 'trống'}"
        )

    raw = input("Chọn account số [1]: ").strip() or "1"
    idx = int(raw) - 1
    if idx < 0 or idx >= len(accounts):
        raise RuntimeError("Số account không hợp lệ")
    return accounts[idx]


def cookies_to_dict(cookies: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for item in (cookies or "").split(";"):
        item = item.strip()
        if not item or "=" not in item:
            continue
        key, value = item.split("=", 1)
        out[key.strip()] = value.strip()
    return out


def compact_json(data: Dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def decode_zalo_api_response(resp_json: Dict[str, Any], zpw_enk: str) -> Any:
    """Decode field data nếu response trả encrypted data."""
    data_field = resp_json.get("data") if isinstance(resp_json, dict) else None
    if isinstance(data_field, str) and data_field:
        return zalo_decode(data_field, zpw_enk)
    return data_field if data_field is not None else resp_json


def post_zalo_api(
    url: str,
    payload: Dict[str, Any],
    cookies: str,
    zpw_enk: str,
    zpw_ver: str,
    timeout: int = DEFAULT_TIMEOUT,
) -> Tuple[requests.Response, Dict[str, Any], Any]:
    plaintext = compact_json(payload)
    encoded = zalo_encode(plaintext, zpw_enk, url_encode=True)
    response = requests.post(
        url,
        params={"zpw_ver": zpw_ver, "zpw_type": "30"},
        data=f"params={encoded}",
        headers=zalo_mobile_headers(),
        cookies=cookies_to_dict(cookies),
        timeout=timeout,
    )
    try:
        resp_json = response.json()
    except Exception:
        resp_json = {"_raw_text": response.text}
    try:
        decoded = decode_zalo_api_response(resp_json, zpw_enk)
    except Exception as e:
        decoded = {"_decode_error": str(e), "_response_json": resp_json}
    return response, resp_json, decoded


def is_group_link(value: str) -> bool:
    v = (value or "").strip().lower()
    return v.startswith("http://") or v.startswith("https://") or "zalo.me/g/" in v


def resolve_group_id_from_link(link: str, cookies: str, zpw_enk: str, zpw_ver: str) -> Tuple[str, Dict[str, Any]]:
    payload = {
        "link": link.strip(),
        "avatar_size": 120,
        "member_avatar_size": 120,
        "mpage": 1,
    }
    response, resp_json, decoded = post_zalo_api(
        LINK_GINFO_URL,
        payload,
        cookies=cookies,
        zpw_enk=zpw_enk,
        zpw_ver=zpw_ver,
        timeout=DEFAULT_TIMEOUT,
    )
    data_obj = decoded.get("data", decoded) if isinstance(decoded, dict) else {}
    if isinstance(data_obj, str):
        try:
            data_obj = json.loads(data_obj)
        except Exception:
            data_obj = {}
    group_id = ""
    if isinstance(data_obj, dict):
        group_id = str(data_obj.get("groupId") or data_obj.get("gridId") or "").strip()
    debug = {
        "endpoint": LINK_GINFO_URL,
        "http_status": response.status_code,
        "request_payload": payload,
        "response_json": resp_json,
        "decoded": decoded,
        "resolved_group_id": group_id,
    }
    if not group_id:
        raise RuntimeError("Không resolve được groupId từ link. Xem decoded của link/ginfo để biết lý do.")
    return group_id, debug


def normalize_group_input(raw: str, cookies: str, zpw_enk: str, zpw_ver: str) -> Tuple[str, Dict[str, Any]]:
    raw = (raw or "").strip()
    if not raw:
        raise ValueError("Thiếu groupId hoặc group link")
    if is_group_link(raw):
        print(f"Đang resolve link -> groupId: {raw}")
        group_id, link_debug = resolve_group_id_from_link(raw, cookies, zpw_enk, zpw_ver)
        print(f"Đã resolve groupId: {group_id}")
        return group_id, link_debug
    return raw.replace("g", "", 1) if raw.startswith("g") else raw, {}


def build_getmg_payload(group_id: str, page: int = 1, mcount: int = DEFAULT_MCOUNT, imei: str = "") -> Dict[str, Any]:
    return {
        "grids": [str(group_id)],
        "avatar_size": 120,
        "member_avatar_size": 120,
        "mpage": int(page),
        "mcount": int(mcount),
        "imei": str(imei or ""),
    }


def extract_group_obj_from_decoded(decoded: Any, group_id: str) -> Dict[str, Any]:
    """Lấy group object từ response decoded của /api/group/getmg."""
    if not isinstance(decoded, dict):
        return {}
    data_obj = decoded.get("data", decoded)
    if isinstance(data_obj, str):
        try:
            data_obj = json.loads(data_obj)
        except Exception:
            return {}
    if not isinstance(data_obj, dict):
        return {}

    gid = str(group_id or "").strip()
    raw_group = data_obj.get(gid) or data_obj.get(group_id)
    if not raw_group and gid.isdigit():
        raw_group = data_obj.get(int(gid))
    if isinstance(raw_group, dict):
        return raw_group

    grid_map = data_obj.get("gridInfoMap") or data_obj.get("grid_info_map") or data_obj.get("groups")
    if isinstance(grid_map, dict):
        raw_group = grid_map.get(gid) or grid_map.get(group_id)
        if not raw_group and gid.isdigit():
            raw_group = grid_map.get(int(gid))
        if not raw_group and len(grid_map) == 1:
            raw_group = next(iter(grid_map.values()))
        if isinstance(raw_group, dict):
            return raw_group

    if data_obj.get("groupId") or data_obj.get("gridId") or data_obj.get("memberIds"):
        return data_obj
    return {}


def extract_member_ids_from_decoded(decoded: Any, group_id: str) -> List[str]:
    """Trả về danh sách UID từ field memberIds của response getmg."""
    raw_group = extract_group_obj_from_decoded(decoded, group_id)
    member_ids = raw_group.get("memberIds") if isinstance(raw_group, dict) else []
    result: List[str] = []
    seen = set()
    if isinstance(member_ids, dict):
        iterable = member_ids.values()
    elif isinstance(member_ids, list):
        iterable = member_ids
    else:
        iterable = []
    for item in iterable:
        uid = str(item or "").strip()
        if uid and "_" in uid:
            uid = uid.rsplit("_", 1)[0]
        if uid and uid not in seen:
            seen.add(uid)
            result.append(uid)
    return result


def _safe_len(value: Any) -> str:
    if isinstance(value, (list, dict, tuple, set)):
        return str(len(value))
    if value is None:
        return "0"
    return "không phải list/dict"


def print_decoded_summary(decoded: Any, group_id: str) -> None:
    print("\n===== SUMMARY DECODED =====")
    print(f"decoded type: {type(decoded).__name__}")
    if not isinstance(decoded, dict):
        print("Decoded không phải dict, hãy mở file output để xem raw.")
        return

    print(f"top keys: {list(decoded.keys())}")
    data_obj = decoded.get("data", decoded)
    if isinstance(data_obj, str):
        try:
            data_obj = json.loads(data_obj)
        except Exception:
            pass
    if not isinstance(data_obj, dict):
        print(f"data type: {type(data_obj).__name__}")
        return

    print(f"data keys: {list(data_obj.keys())[:20]}")
    raw_group = extract_group_obj_from_decoded(decoded, group_id)
    if not isinstance(raw_group, dict) or not raw_group:
        print("Không tìm thấy group object. Có thể response không nằm ở data[groupId].")
        return

    print(f"group keys: {list(raw_group.keys())}")
    print(f"name/gridName: {raw_group.get('name') or raw_group.get('gridName')}")
    print(f"totalMember: {raw_group.get('totalMember')}")
    print(f"hasMoreMember: {raw_group.get('hasMoreMember')}")
    for key in ["memberIds", "currentMems", "memVerList", "members", "admins", "adminIds", "updateMems", "topMember", "memberVerMap"]:
        if key in raw_group:
            print(f"{key}: {_safe_len(raw_group.get(key))}")
    member_ids = extract_member_ids_from_decoded(decoded, group_id)
    print(f"\nEXTRACT memberIds -> UID count: {len(member_ids)}")
    if member_ids:
        print("10 UID đầu:", member_ids[:10])


def save_debug_output(name: str, payload: Dict[str, Any], resp_json: Dict[str, Any], decoded: Any, link_debug: Dict[str, Any]) -> Path:
    out_dir = _project_root() / "data" / "api_debug"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    path = out_dir / f"{name}_{ts}.json"
    body = {
        "endpoint": GETMG_URL,
        "request_payload": payload,
        "link_resolve_debug": link_debug,
        "response_json": resp_json,
        "decoded": decoded,
        "extracted_member_ids": extract_member_ids_from_decoded(decoded, str(payload.get("grids", [""])[0])),
    }
    with path.open("w", encoding="utf-8") as f:
        json.dump(body, f, ensure_ascii=False, indent=2)
    return path


def main() -> None:
    print("=== Test endpoint /api/group/getmg ===")
    accounts = load_accounts()
    acc = choose_account(accounts)

    cookies = (acc.get("cookies") or "").strip()
    zpw_enk = (acc.get("zpwEnk") or "").strip()
    imei = (acc.get("imei") or "").strip()
    zpw_ver = get_zpw_ver()

    print(f"\nAccount: {acc.get('name') or acc.get('accountId')}")
    print(f"zpw_ver: {zpw_ver}")
    print(f"imei: {'có' if imei else 'trống'}")

    raw = input("\nDán groupId hoặc link nhóm: ").strip()
    group_id, link_debug = normalize_group_input(raw, cookies, zpw_enk, zpw_ver)

    page_raw = input("mpage [1]: ").strip() or "1"
    mcount_raw = input(f"mcount [{DEFAULT_MCOUNT}]: ").strip() or str(DEFAULT_MCOUNT)
    page = int(page_raw)
    mcount = int(mcount_raw)

    payload = build_getmg_payload(group_id, page=page, mcount=mcount, imei=imei)
    print("\nEndpoint:", GETMG_URL)
    print("Payload plaintext:")
    print(json.dumps(payload, ensure_ascii=False, indent=2))

    response, resp_json, decoded = post_zalo_api(
        GETMG_URL,
        payload,
        cookies=cookies,
        zpw_enk=zpw_enk,
        zpw_ver=zpw_ver,
        timeout=DEFAULT_TIMEOUT,
    )

    print(f"\nHTTP status: {response.status_code}")
    if isinstance(resp_json, dict):
        print(f"response_json keys: {list(resp_json.keys())}")
        if "error_code" in resp_json:
            print(f"response error_code: {resp_json.get('error_code')} | message: {resp_json.get('error_message')}")

    print_decoded_summary(decoded, group_id)

    out_path = save_debug_output(
        name=f"group_getmg_{group_id}_p{page}",
        payload=payload,
        resp_json=resp_json,
        decoded=decoded,
        link_debug=link_debug,
    )
    print(f"\nĐã lưu full response tại: {out_path}")


if __name__ == "__main__":
    main()
