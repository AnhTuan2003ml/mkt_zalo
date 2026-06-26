import json
from typing import Any, Dict, Iterable, List, Tuple

import requests

from core.zalo.enc import zalo_encode
from core.zalo.dec import zalo_decode
from core.zalo.zalo_headers import zalo_mobile_headers

GETMG_URL = "https://tt-group-wpa.chat.zalo.me/api/group/getmg"


def build_getmg_payload(group_id: str, page: int = 1, mcount: int = 500, imei: str = "") -> dict:
    """Payload đúng cho /api/group/getmg."""
    return {
        "grids": [str(group_id)],
        "avatar_size": 120,
        "member_avatar_size": 120,
        "mpage": int(page),
        "mcount": int(mcount),
        "imei": str(imei or ""),
    }


def _cookies_str_to_dict(cookies: str) -> dict:
    cookie_dict = {}
    for item in (cookies or "").split(";"):
        item = item.strip()
        if "=" in item:
            key, value = item.split("=", 1)
            cookie_dict[key.strip()] = value.strip()
    return cookie_dict


def _as_json_obj(value: Any) -> Any:
    """Nếu value là JSON string thì parse, ngược lại giữ nguyên."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return value
    return value


def _normalize_uid(value: Any) -> str:
    """Chuẩn hóa UID từ string hoặc object member."""
    if isinstance(value, dict):
        uid = (
            value.get("id")
            or value.get("uid")
            or value.get("userId")
            or value.get("user_id")
            or value.get("zaloId")
            or ""
        )
    else:
        uid = value
    uid = str(uid or "").strip()
    # Một số API có dạng uid_version, chỉ lấy uid ở trước dấu _.
    if uid and "_" in uid:
        uid = uid.rsplit("_", 1)[0]
    return uid.strip()


def _append_unique_uid(target: List[str], seen: set, value: Any) -> int:
    uid = _normalize_uid(value)
    if uid and uid not in seen:
        seen.add(uid)
        target.append(uid)
        return 1
    return 0


def _iter_values(value: Any) -> Iterable[Any]:
    if isinstance(value, dict):
        return value.values()
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return value
    return []


def _extract_group_obj_from_data(data_obj: Any, group_id: str) -> Dict[str, Any]:
    """
    /api/group/getmg có thể trả nhiều dạng:
    1) decoded.data[groupId] = {...}                 <- response thực tế bạn paste
    2) decoded.data.gridInfoMap[groupId] = {...}     <- một số parser/API cũ
    3) decoded.data.groups[groupId] = {...}
    4) decoded.data chính là group object.
    """
    data_obj = _as_json_obj(data_obj)
    if not isinstance(data_obj, dict):
        return {}

    gid = str(group_id or "").strip()

    # Dạng đúng đang thấy từ /api/group/getmg: data[groupId]
    raw_group = data_obj.get(gid) or data_obj.get(group_id)
    if not raw_group and gid.isdigit():
        raw_group = data_obj.get(int(gid))
    if isinstance(raw_group, dict):
        return raw_group

    # Dạng map cũ/khác.
    for map_key in ("gridInfoMap", "grid_info_map", "groups", "groupMap"):
        group_map = data_obj.get(map_key)
        group_map = _as_json_obj(group_map)
        if isinstance(group_map, dict):
            raw_group = group_map.get(gid) or group_map.get(group_id)
            if not raw_group and gid.isdigit():
                raw_group = group_map.get(int(gid))
            if not raw_group and len(group_map) == 1:
                raw_group = next(iter(group_map.values()))
            if isinstance(raw_group, dict):
                return raw_group

    # Nếu chính data_obj đã là group object.
    obj_gid = str(data_obj.get("groupId") or data_obj.get("gridId") or data_obj.get("id") or "").strip()
    if obj_gid and (not gid or obj_gid == gid):
        return data_obj

    # Fallback: nếu data_obj chỉ có một object con có groupId/gridId thì dùng nó.
    dict_children = [v for v in data_obj.values() if isinstance(v, dict)]
    if len(dict_children) == 1:
        child = dict_children[0]
        if child.get("groupId") or child.get("gridId") or child.get("memberIds"):
            return child

    return {}


def _extract_uids_from_group(raw_group: dict) -> list:
    """
    Ưu tiên memberIds vì /api/group/getmg trả UID đầy đủ ở field này.
    currentMems thường chỉ là dữ liệu hiển thị nhanh, có thể chỉ 1 vài người.
    """
    if not isinstance(raw_group, dict):
        return []

    uids: List[str] = []
    seen = set()

    # 1) Quan trọng nhất: memberIds là list UID đầy đủ trong response getmg.
    member_ids = raw_group.get("memberIds")
    if member_ids is not None:
        for item in _iter_values(member_ids):
            _append_unique_uid(uids, seen, item)
        if uids:
            return uids

    # 2) Fallback cho các response khác.
    for key in ("memVerList", "members", "currentMems", "updateMems", "admins", "adminIds", "memberVerMap"):
        val = raw_group.get(key)
        if val is None:
            continue
        for item in _iter_values(val):
            _append_unique_uid(uids, seen, item)

    return uids


def _extract_member_map_from_group(raw_group: dict) -> Dict[str, dict]:
    """Lấy các object member có sẵn để merge tên/avatar nếu API trả về."""
    member_map: Dict[str, dict] = {}
    if not isinstance(raw_group, dict):
        return member_map
    for key in ("currentMems", "updateMems", "admins", "members"):
        val = raw_group.get(key)
        for item in _iter_values(val):
            if isinstance(item, dict):
                uid = _normalize_uid(item)
                if uid:
                    member_map[uid] = item
    return member_map


def _parse_group_info(raw_group: dict, group_id: str) -> dict:
    if not isinstance(raw_group, dict):
        return {}
    gid = str(
        raw_group.get("groupId")
        or raw_group.get("gridId")
        or raw_group.get("id")
        or group_id
        or ""
    ).strip()
    return {
        "groupId": gid,
        "gridId": gid,
        "grid_name": raw_group.get("name", "") or raw_group.get("gridName", ""),
        "grid_desc": raw_group.get("desc", ""),
        "grid_type": raw_group.get("type", 0),
        "grid_creatorId": raw_group.get("creatorId", ""),
        "grid_adminIds": raw_group.get("adminIds", []),
        "grid_avatar": raw_group.get("avt", "") or raw_group.get("avatar", ""),
        "grid_fullAvt": raw_group.get("fullAvt", ""),
        "grid_totalMember": raw_group.get("totalMember", 0),
        "grid_setting": raw_group.get("setting", {}),
        "grid_hasMoreMember": raw_group.get("hasMoreMember", 0),
    }


def _has_more_pages(raw_group: dict, got_uids: int, total_member: int) -> bool:
    if total_member and got_uids >= total_member:
        return False
    return bool(raw_group.get("hasMoreMember") or raw_group.get("hasMore") or raw_group.get("more") or 0)


def _error_result(
    group_id: str,
    message: str,
    pages_fetched: int = 0,
    total_member: int = 0,
    raw_group_info: dict = None,
    error_code: int = None,
) -> dict:
    """Chuẩn hóa kết quả lỗi để service phía trên dừng ngay, không gọi profile."""
    result = {
        "ok": False,
        "group_id": group_id,
        "members": [],
        "uids": [],
        "member_map": {},
        "total": total_member or 0,
        "pages": pages_fetched,
        "raw_group_info": raw_group_info or {},
        "message": message or "Không lấy được danh sách thành viên",
    }
    if error_code is not None:
        result["error_code"] = error_code
    return result


def _is_permission_denied_error(error_code: Any, message: str = "") -> bool:
    """Các lỗi chắc chắn không nên retry sang page tiếp theo."""
    msg = str(message or "").lower()
    try:
        code = int(error_code)
    except Exception:
        code = error_code
    return code in {164} or "không phải thành viên" in msg or "khong phai thanh vien" in msg


def get_members(group_id: str, zpw_enk: str, cookies: str,
                imei: str = "", timeout: int = 15, zpw_ver: str = None) -> tuple:
    """
    API tương thích code cũ: trả response_json, decoded_data, group_info, mem_list.
    mem_list ở đây là list UID lấy từ memberIds.
    """
    from core.zalo.zalo_config import get_zpw_ver as _get_zpw_ver
    zpw_ver = _get_zpw_ver(zpw_ver)
    cookie_dict = _cookies_str_to_dict(cookies)
    result = get_members_by_group_id(
        group_id=group_id,
        cookie_dict=cookie_dict,
        zpw_enk=zpw_enk,
        imei=imei,
        zpw_ver=zpw_ver,
        mcount=500,
        timeout=timeout,
        max_pages=1,
    )
    raw_group_info = result.get("raw_group_info", {}) or {}
    group_info = _parse_group_info(raw_group_info, group_id)
    mem_list = result.get("uids", [])
    response_json = {} if not result.get("ok") else {"ok": True, "message": result.get("message", "")}
    decoded_data = result
    return response_json, decoded_data, group_info, mem_list


def get_members_by_group_id(
    group_id: str,
    cookie_dict: dict,
    zpw_enk: str,
    imei: str,
    zpw_ver: str = "637",
    mcount: int = 500,
    timeout: int = 30,
    max_pages: int = 200,
) -> dict:
    group_id = str(group_id or "").strip()
    if not group_id:
        return {"ok": False, "group_id": group_id, "members": [], "uids": [], "member_map": {}, "total": 0, "pages": 0, "raw_group_info": {}, "message": "Thiếu groupId"}
    if not zpw_enk:
        return {"ok": False, "group_id": group_id, "members": [], "uids": [], "member_map": {}, "total": 0, "pages": 0, "raw_group_info": {}, "message": "Thiếu zpw_enk"}

    from core.zalo.zalo_config import get_zpw_ver as _get_zpw_ver
    zpw_ver = _get_zpw_ver(zpw_ver)

    headers = zalo_mobile_headers()
    all_uids: List[str] = []
    seen_uids = set()
    member_map: Dict[str, dict] = {}
    raw_group_info: Dict[str, Any] = {}
    pages_fetched = 0
    total_member = 0
    mpage = 1
    no_new_uids_count = 0
    last_error = ""

    while mpage <= max_pages:
        payload = build_getmg_payload(group_id, page=mpage, mcount=mcount, imei=imei)
        plaintext = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        try:
            encoded = zalo_encode(plaintext, zpw_enk, url_encode=True)
            response = requests.post(
                GETMG_URL,
                params={"zpw_ver": zpw_ver, "zpw_type": "30"},
                data=f"params={encoded}",
                headers=headers,
                cookies=cookie_dict or {},
                timeout=timeout,
            )
            pages_fetched += 1

            if response.status_code != 200:
                last_error = f"HTTP {response.status_code}"
                print(f"[get_members] group={group_id} page={mpage} {last_error}", flush=True)
                mpage += 1
                continue

            resp_json = response.json()
            if resp_json.get("error_code", 0) not in (0, None):
                error_code = resp_json.get("error_code")
                last_error = resp_json.get("error_message") or f"error_code={error_code}"
                print(f"[get_members] group={group_id} page={mpage} response error: {last_error}. Dừng, không lặp page tiếp.", flush=True)
                return _error_result(
                    group_id=group_id,
                    message=f"API getmg trả lỗi: {last_error}",
                    pages_fetched=pages_fetched,
                    total_member=total_member,
                    raw_group_info=raw_group_info,
                    error_code=error_code,
                )

            data_field = resp_json.get("data", "")
            if isinstance(data_field, str) and data_field:
                try:
                    decoded = zalo_decode(data_field, zpw_enk)
                except Exception as e:
                    last_error = f"decode error: {e}"
                    print(f"[get_members] group={group_id} page={mpage} {last_error}", flush=True)
                    mpage += 1
                    continue
            else:
                decoded = data_field

            decoded = _as_json_obj(decoded)
            if not isinstance(decoded, dict):
                last_error = f"decoded không phải dict: {type(decoded).__name__}"
                print(f"[get_members] group={group_id} page={mpage} {last_error}", flush=True)
                mpage += 1
                continue

            if decoded.get("error_code", 0) not in (0, None):
                error_code = decoded.get("error_code")
                last_error = decoded.get("error_message") or f"decoded error_code={error_code}"
                if _is_permission_denied_error(error_code, last_error):
                    message = f"Không lấy được thành viên nhóm {group_id}: {last_error}. Account hiện tại không phải thành viên nhóm này hoặc không có quyền xem danh sách thành viên."
                else:
                    message = f"API getmg trả lỗi: {last_error}"
                print(f"[get_members] group={group_id} page={mpage} decoded error: {last_error}. Dừng, không lặp page tiếp.", flush=True)
                return _error_result(
                    group_id=group_id,
                    message=message,
                    pages_fetched=pages_fetched,
                    total_member=total_member,
                    raw_group_info=raw_group_info,
                    error_code=error_code,
                )

            data_obj = _as_json_obj(decoded.get("data", decoded))
            if not isinstance(data_obj, dict):
                last_error = f"decoded.data không phải dict: {type(data_obj).__name__}"
                print(f"[get_members] group={group_id} page={mpage} {last_error}", flush=True)
                mpage += 1
                continue

            if data_obj.get("removedsGroup") or data_obj.get("removedGroups") or data_obj.get("removed_groups"):
                return _error_result(
                    group_id=group_id,
                    message="Nhóm không còn trong account hoặc không có quyền truy cập",
                    pages_fetched=pages_fetched,
                    total_member=total_member,
                    raw_group_info=raw_group_info,
                )

            raw_group = _extract_group_obj_from_data(data_obj, group_id)
            if not isinstance(raw_group, dict) or not raw_group:
                last_error = f"Không thấy group object trong decoded.data. data_keys={list(data_obj.keys())[:20]}"
                print(f"[get_members] group={group_id} page={mpage} {last_error}", flush=True)
                mpage += 1
                continue

            if not raw_group_info:
                raw_group_info = raw_group
            else:
                # Cập nhật field mới nếu page sau trả thêm dữ liệu.
                for key, value in raw_group.items():
                    if value not in (None, "", [], {}):
                        raw_group_info[key] = value

            try:
                total_member = int(raw_group.get("totalMember") or total_member or 0)
            except Exception:
                total_member = total_member or 0

            member_map.update(_extract_member_map_from_group(raw_group))
            page_uids = _extract_uids_from_group(raw_group)
            new_count = 0
            for uid in page_uids:
                if uid not in seen_uids:
                    seen_uids.add(uid)
                    all_uids.append(uid)
                    new_count += 1

            print(
                f"[get_members] group={group_id} page={mpage} "
                f"memberIds={len(raw_group.get('memberIds') or [])} new={new_count} "
                f"total={len(all_uids)}/{total_member or '?'}",
                flush=True,
            )

            if total_member and len(all_uids) >= total_member:
                break

            has_more = _has_more_pages(raw_group, len(all_uids), total_member)
            if not has_more:
                break

            if new_count == 0:
                no_new_uids_count += 1
                if no_new_uids_count >= 2:
                    last_error = "API báo còn trang nhưng page sau không có UID mới; dừng để tránh lặp vô hạn"
                    break
            else:
                no_new_uids_count = 0

            mpage += 1

        except requests.exceptions.Timeout:
            pages_fetched += 1
            last_error = "timeout"
            print(f"[get_members] group={group_id} page={mpage} timeout", flush=True)
            mpage += 1
        except Exception as e:
            pages_fetched += 1
            last_error = str(e)
            print(f"[get_members] group={group_id} page={mpage} error: {e}", flush=True)
            mpage += 1

    ok = len(all_uids) > 0
    message = f"Lấy được {len(all_uids)}/{total_member or '?'} thành viên sau {pages_fetched} trang"
    if not ok and last_error:
        message = f"{message}. Lỗi cuối: {last_error}"

    return {
        "ok": ok,
        "group_id": group_id,
        "members": list(all_uids),
        "uids": list(all_uids),
        "member_map": member_map,
        "total": total_member or len(all_uids),
        "pages": pages_fetched,
        "raw_group_info": raw_group_info,
        "message": message,
    }
