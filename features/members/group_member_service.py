"""
Service xử lý toàn bộ luồng lấy thành viên nhóm Zalo.

Nguyên tắc chính:
- UI/API có thể nhập link hoặc groupId.
- Nếu nhập link: luôn resolve link -> groupId trước.
- Sau khi có groupId: luôn lấy UID thành viên qua /api/group/getmg với grids.
- Không để route Flask và schedule worker copy lại cùng một logic.
"""

import re
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from features.groups.get_group import resolve_group_id_from_url
from features.members.get_members import get_members, get_members_by_group_id

LogFunc = Optional[Callable[[str, str], None]]


def log_message(callback: LogFunc, message: str, msg_type: str = "info") -> None:
    if not callback:
        return
    try:
        callback(message, msg_type)
    except TypeError:
        callback(message)


def normalize_group_input(raw: str) -> Tuple[str, str]:
    raw = (raw or "").strip()
    if not raw:
        return "", ""
    low = raw.lower()
    if low.startswith("http://") or low.startswith("https://"):
        return "link", raw
    if "zalo.me/g/" in low:
        if not low.startswith("http"):
            raw = "https://" + raw
        return "link", raw
    if re.fullmatch(r"\d+", raw):
        return "group_id", raw
    return "link", "https://zalo.me/g/" + raw


def _extract_member_uid(member) -> str:
    if isinstance(member, dict):
        uid = (
            member.get("id")
            or member.get("uid")
            or member.get("userId")
            or member.get("user_id")
            or ""
        )
    else:
        uid = member
    uid = str(uid or "").strip()
    if uid and "_" in uid:
        uid = uid.rsplit("_", 1)[0]
    return uid.strip()


def unique_uids(items: Iterable) -> List[str]:
    result: List[str] = []
    seen = set()
    for item in items or []:
        uid = _extract_member_uid(item)
        if uid and uid not in seen:
            seen.add(uid)
            result.append(uid)
    return result


def build_member_map(members: Iterable) -> Dict[str, dict]:
    member_map: Dict[str, dict] = {}
    for member in members or []:
        uid = _extract_member_uid(member)
        if uid and isinstance(member, dict):
            member_map[uid] = member
    return member_map


def merge_group_info(base: Optional[dict], extra: Optional[dict]) -> dict:
    merged = dict(base or {})
    if isinstance(extra, dict):
        for key, value in extra.items():
            if value not in (None, "", [], {}):
                merged[key] = value
    return merged


def get_group_id_from_group_info(group_info: Optional[dict]) -> str:
    if not isinstance(group_info, dict):
        return ""
    return str(
        group_info.get("groupId")
        or group_info.get("gridId")
        or group_info.get("id")
        or ""
    ).strip()


def _cookies_str_to_dict(cookies: str) -> dict:
    cookie_dict = {}
    for item in cookies.split(";"):
        item = item.strip()
        if "=" in item:
            key, value = item.split("=", 1)
            cookie_dict[key.strip()] = value.strip()
    return cookie_dict


def resolve_group_link_to_group_id(link: str, zpw_enk: str, cookies: str,
                                   callback: LogFunc = None, zpw_ver: str = None) -> Tuple[str, dict, Dict[str, dict]]:
    log_message(callback, f"Đang chuyển link nhóm thành groupId: {link}", "loading")
    result = resolve_group_id_from_url(link, zpw_enk, cookies, zpw_ver=zpw_ver)
    if not result.get("ok"):
        raise ValueError(result.get("message", "Không lấy được groupId từ link nhóm"))
    group_id = result.get("group_id", "")
    group_info = result.get("group_info", {})
    if not group_id:
        raise ValueError("Không lấy được groupId từ link nhóm. Link có thể sai, hết hạn hoặc account không có quyền truy cập.")
    total_member = group_info.get("totalMember", 0)
    group_name = group_info.get("name", "N/A")
    log_message(callback, f"Đã resolve link -> groupId: {group_id}", "success")
    log_message(callback, f"Tên nhóm: {group_name} | Tổng thành viên API báo: {total_member}", "info")
    return group_id, group_info, {}


def resolve_group_input_to_group_id(raw: str, zpw_enk: str, cookies: str,
                                    callback: LogFunc = None, zpw_ver: str = None) -> Tuple[str, str, dict, Dict[str, dict]]:
    input_type, normalized = normalize_group_input(raw)
    if not normalized:
        raise ValueError("Thiếu Group Link hoặc Group ID")
    if input_type == "link":
        group_id, group_info, member_map = resolve_group_link_to_group_id(
            normalized, zpw_enk, cookies, callback=callback, zpw_ver=zpw_ver
        )
        return input_type, group_id, group_info, member_map
    group_id = normalized
    log_message(callback, f"Input là groupId: {group_id}", "info")
    return input_type, group_id, {}, {}


def fetch_group_member_uids(group_id: str, zpw_enk: str, cookies: str,
                            callback: LogFunc = None, zpw_ver: str = None,
                            imei: str = "") -> Tuple[dict, List[str]]:
    group_id = str(group_id or "").strip()
    if not group_id:
        raise ValueError("Thiếu groupId")
    log_message(callback, f"Đang lấy thành viên bằng groupId: {group_id}", "loading")
    cookie_dict = _cookies_str_to_dict(cookies)
    result = get_members_by_group_id(
        group_id=group_id,
        cookie_dict=cookie_dict,
        zpw_enk=zpw_enk,
        imei=imei,
        zpw_ver=zpw_ver,
        mcount=500,
        max_pages=200,
    )
    if not result.get("ok"):
        err = result.get("message", "Không lấy được danh sách thành viên")
        raise ValueError(err)
    uids = result.get("uids", [])
    if not uids:
        err = "Không lấy được danh sách thành viên từ groupId này. Nhóm có thể không tồn tại hoặc account không có quyền truy cập."
        raise ValueError(err)
    raw_group_info = result.get("raw_group_info", {}) or {}
    total_member = int(raw_group_info.get("totalMember", 0))
    log_message(callback, f"Lấy được {len(uids)}/{total_member or '?'} UID từ groupId.", "success")
    group_info = {
        "groupId": group_id,
        "gridId": group_id,
        "grid_name": raw_group_info.get("name", "") or raw_group_info.get("gridName", ""),
        "grid_desc": raw_group_info.get("desc", ""),
        "grid_type": raw_group_info.get("type", 0),
        "grid_creatorId": raw_group_info.get("creatorId", ""),
        "grid_adminIds": raw_group_info.get("adminIds", []),
        "grid_avatar": raw_group_info.get("avt", "") or raw_group_info.get("avatar", ""),
        "grid_fullAvt": raw_group_info.get("fullAvt", ""),
        "grid_totalMember": total_member,
        "grid_setting": raw_group_info.get("setting", {}),
        "grid_hasMoreMember": raw_group_info.get("hasMoreMember", 0),
    }
    return group_info, uids


def fetch_group_members_by_input(raw: str, zpw_enk: str, cookies: str,
                                 callback: LogFunc = None, zpw_ver: str = None,
                                 imei: str = "") -> dict:
    input_type, group_id, resolved_group_info, member_map = resolve_group_input_to_group_id(
        raw, zpw_enk, cookies, callback=callback, zpw_ver=zpw_ver
    )
    members_group_info, uid_list = fetch_group_member_uids(
        group_id, zpw_enk, cookies, callback=callback, zpw_ver=zpw_ver, imei=imei
    )
    group_info = merge_group_info(resolved_group_info, members_group_info)
    group_info.setdefault("groupId", group_id)
    group_info.setdefault("gridId", group_id)
    total_member = int(group_info.get("totalMember") or group_info.get("grid_totalMember") or 0)
    if total_member and len(uid_list) < total_member:
        setting = group_info.get("setting") or group_info.get("grid_setting") or {}
        reason = " Nhóm đang khóa xem thành viên." if isinstance(setting, dict) and setting.get("lockViewMember") == 1 else ""
        log_message(callback, f"Chỉ lấy được {len(uid_list)}/{total_member} UID từ API Zalo.{reason}", "warn")
    return {
        "inputType": input_type,
        "groupId": group_id,
        "groupInfo": group_info,
        "uidList": uid_list,
        "memberMap": member_map,
    }


def format_group_info(group_info: Optional[dict], group_id: str = "", fallback_total: int = 0) -> dict:
    group_info = group_info or {}
    gid = str(
        group_info.get("groupId")
        or group_info.get("gridId")
        or group_id
        or ""
    ).strip()
    return {
        "groupId": gid,
        "name": group_info.get("name", group_info.get("grid_name", "")),
        "desc": group_info.get("desc", group_info.get("grid_desc", "")),
        "avt": group_info.get("avt", group_info.get("grid_avatar", "")),
        "fullAvt": group_info.get("fullAvt", group_info.get("grid_fullAvt", "")),
        "totalMember": group_info.get("totalMember", group_info.get("grid_totalMember", fallback_total)),
    }
