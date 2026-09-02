"""
Service xử lý toàn bộ luồng lấy thành viên nhóm Zalo.

Nguyên tắc chính:
- UI/API có thể nhập link hoặc groupId.
- Nếu nhập link: luôn resolve link -> groupId trước.
- Sau khi có groupId: luôn lấy UID thành viên qua /api/group/getmg với grids.
- Không để route Flask và schedule worker copy lại cùng một logic.
"""

import re
import time
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from features.groups.get_group import resolve_group_id_from_url
from features.groups.group_join_leave import join_group_by_link, leave_group
from features.members.get_members import get_members, get_members_by_group_id

LogFunc = Optional[Callable[[str, str], None]]


def log_message(callback: LogFunc, message: str, msg_type: str = "info") -> None:
    if not callback:
        return
    try:
        callback(message, msg_type)
    except TypeError:
        callback(message)


def is_not_member_error(message: str) -> bool:
    msg = str(message or "").lower()
    return (
        "không phải thành viên" in msg
        or "khong phai thanh vien" in msg
        or "not a member" in msg
        or "account hiện tại không phải thành viên" in msg
        or "không có quyền xem" in msg
    )


_ZERO_WIDTH_CHARS_RE = re.compile(r"[\u200b\u200c\u200d\ufeff]")
_GROUP_ID_MIN_DIGITS = 6
_GROUP_ID_LABEL_PREFIX = r"""(?:
    ["']?(?:group|grid)[\s_-]*id["']?
    |
    ["']?id[\s_-]*(?:nh[oó]m|nhom)?["']?
    |
    ["']?m[aã][\s_-]*(?:nh[oó]m|nhom)["']?
)\s*(?:[:=：#-]\s*)?"""
_GROUP_ID_LABEL_PATTERNS = (
    # ID số bị format từ Excel/clipboard: ``ID: 123 456 789``.
    re.compile(r"(?ix)" + _GROUP_ID_LABEL_PREFIX + r"[\"']?([g]?\d[\d\s,.]{5,179})[\"']?"),
    # ID được dán từ JSON/log: ``{\"groupId\":\"abc_123\"}``.
    re.compile(r"(?ix)" + _GROUP_ID_LABEL_PREFIX + r"[\"']?([a-z0-9_-]{6,160})[\"']?"),
)


def _clean_group_input_text(raw: str) -> str:
    text = _ZERO_WIDTH_CHARS_RE.sub("", str(raw or ""))
    return text.strip().strip("\"'`“”‘’")


def _normalize_group_id_candidate(value: str, *, explicit: bool = False) -> str:
    """Chuẩn hóa Group ID được dán từ UI, JSON, Excel hoặc nhãn ``ID:``.

    Zalo thường trả Group ID dạng số. Một số màn hình/đoạn log cũ thêm tiền tố
    ``g`` hoặc chèn khoảng trắng/dấu phân cách khi sao chép, vì vậy cần làm sạch
    trước khi gọi ``/api/group/getmg``. Mã link mời dạng chữ-số không nhãn vẫn
    được giữ là link code để không phá luồng nhập link hiện có.
    """
    candidate = _clean_group_input_text(value).strip("[](){}<>")
    if not candidate:
        return ""

    # Dạng g123456... từng được các module getmg cũ chấp nhận.
    if re.fullmatch(r"[gG]\d+", candidate):
        candidate = candidate[1:]

    # ID bị định dạng từ bảng tính hoặc copy có khoảng trắng: 123 456 789.
    if re.fullmatch(r"\d[\d\s,\.]*", candidate):
        compact = re.sub(r"[\s,\.]", "", candidate)
        if compact.isdigit() and len(compact) >= _GROUP_ID_MIN_DIGITS:
            return compact

    if candidate.isdigit() and len(candidate) >= _GROUP_ID_MIN_DIGITS:
        return candidate

    # Chỉ chấp nhận ID chữ-số khi người dùng ghi rõ groupId/gridId/ID nhóm.
    if explicit and re.fullmatch(r"[A-Za-z0-9_-]{6,160}", candidate):
        if candidate[:1].lower() == "g" and candidate[1:].isdigit():
            return candidate[1:]
        return candidate
    return ""


def normalize_group_input(raw: str) -> Tuple[str, str]:
    """Nhận link, mã link hoặc Group ID ở nhiều định dạng dán phổ biến."""
    text = _clean_group_input_text(raw)
    if not text:
        return "", ""

    low = text.lower()
    if low.startswith(("http://", "https://")):
        return "link", text
    if low.startswith("www."):
        return "link", "https://" + text
    if "zalo.me/g/" in low:
        if not low.startswith("http"):
            text = "https://" + text.lstrip("/")
        return "link", text

    # Hỗ trợ: ID: 123..., Group ID = g123..., {"groupId":"123..."}.
    for pattern in _GROUP_ID_LABEL_PATTERNS:
        match = pattern.search(text)
        if match:
            group_id = _normalize_group_id_candidate(match.group(1), explicit=True)
            if group_id:
                return "group_id", group_id

    # Hỗ trợ ID thuần, ID có tiền tố g và số bị chèn khoảng trắng/dấu chấm.
    direct_group_id = _normalize_group_id_candidate(text)
    if direct_group_id:
        return "group_id", direct_group_id

    # Chuỗi chữ-số không nhãn tiếp tục được hiểu là mã link mời như phiên bản cũ.
    return "link", "https://zalo.me/g/" + text.lstrip("/")

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
                                 imei: str = "",
                                 auto_join_when_not_member: bool = True,
                                 leave_after_auto_join: bool = True) -> dict:
    input_type, normalized_input = normalize_group_input(raw)
    input_type, group_id, resolved_group_info, member_map = resolve_group_input_to_group_id(
        raw, zpw_enk, cookies, callback=callback, zpw_ver=zpw_ver
    )

    auto_joined = False
    auto_left = False
    join_result = None
    leave_result = None

    try:
        members_group_info, uid_list = fetch_group_member_uids(
            group_id, zpw_enk, cookies, callback=callback, zpw_ver=zpw_ver, imei=imei
        )
    except ValueError as exc:
        err = str(exc)
        if not auto_join_when_not_member or not is_not_member_error(err):
            raise
        if input_type != "link" or not normalized_input:
            raise ValueError(
                err + " Nếu muốn hệ thống tự tham gia rồi lấy thành viên, hãy nhập link nhóm Zalo thay vì chỉ nhập groupId."
            )

        # Chạy ngầm: không đẩy log UI khi tự tham gia/rời nhóm.
        join_result = join_group_by_link(
            normalized_input, zpw_enk, cookies, zpw_ver=zpw_ver
        )
        if not join_result.get("ok"):
            msg = join_result.get("message", "Lỗi không xác định")
            code = join_result.get("error_code")
            suffix = f" [{code}]" if code not in (None, "") else ""
            raise ValueError(f"Không lấy được thành viên nhóm {group_id}: tự tham gia bằng link thất bại{suffix}: {msg}")

        auto_joined = True
        time.sleep(1.0)

        fetch_failed = False
        try:
            members_group_info, uid_list = fetch_group_member_uids(
                group_id, zpw_enk, cookies, callback=callback, zpw_ver=zpw_ver, imei=imei
            )
        except Exception:
            fetch_failed = True
            raise
        finally:
            # Rời nhóm đã tự join ngầm khi: caller yêu cầu rời ngay, HOẶC lần lấy
            # UID thứ hai lỗi — khi đó caller không nhận được payload nên không
            # thể tự rời, account sẽ kẹt lại trong nhóm nếu không rời tại đây.
            if auto_joined and (leave_after_auto_join or fetch_failed):
                try:
                    leave_result = leave_group(
                        [group_id], imei=imei, zpw_enk=zpw_enk, cookies=cookies, zpw_ver=zpw_ver
                    )
                    auto_left = bool(leave_result.get("ok"))
                except Exception as leave_exc:
                    log_message(callback, f"Không rời được nhóm đã tự tham gia: {leave_exc}", "warn")

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
        "autoJoined": auto_joined,
        "autoLeft": auto_left,
        "joinResult": join_result,
        "leaveResult": leave_result,
    }


def format_group_info(group_info: Optional[dict], group_id: str = "", fallback_total: int = 0) -> dict:
    group_info = group_info or {}
    gid = str(
        group_info.get("groupId")
        or group_info.get("gridId")
        or group_id
        or ""
    ).strip()
    admin_ids = group_info.get("adminIds") or group_info.get("grid_adminIds") or []
    if not isinstance(admin_ids, (list, tuple)):
        admin_ids = [admin_ids]
    return {
        "groupId": gid,
        "name": group_info.get("name", group_info.get("grid_name", "")),
        "desc": group_info.get("desc", group_info.get("grid_desc", "")),
        "avt": group_info.get("avt", group_info.get("grid_avatar", "")),
        "fullAvt": group_info.get("fullAvt", group_info.get("grid_fullAvt", "")),
        "totalMember": group_info.get("totalMember", group_info.get("grid_totalMember", fallback_total)),
        "creatorId": str(group_info.get("creatorId") or group_info.get("grid_creatorId") or ""),
        "adminIds": [str(x) for x in admin_ids if str(x or "").strip()],
    }
