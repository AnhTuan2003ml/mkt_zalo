import json
import requests
from core.zalo.enc import zalo_encode
from core.zalo.dec import zalo_decode
from core.zalo.zalo_headers import zalo_mobile_headers


def resolve_group_id_from_url(link: str, zpw_enk: str, cookies: str,
                               zpw_ver: str = None) -> dict:
    """
    Resolve URL nhóm Zalo thành groupId.
    Chỉ làm nhiệm vụ resolve groupId, không lấy members.
    API: https://tt-group-wpa.chat.zalo.me/api/group/link/ginfo

    Returns:
        {
            "ok": True/False,
            "group_id": "...",
            "group_info": {...},
            "message": "..."
        }
    """
    from core.zalo.zalo_config import get_zpw_ver as _get_zpw_ver
    link = (link or "").strip()
    if not link:
        return {"ok": False, "group_id": "", "group_info": {}, "message": "Thiếu link nhóm"}
    zpw_ver = _get_zpw_ver(zpw_ver)
    payload = {
        "link": link,
        "avatar_size": 120,
        "member_avatar_size": 120,
        "mpage": 1,
    }
    plaintext = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    encoded = zalo_encode(plaintext, zpw_enk, url_encode=True)
    url = "https://tt-group-wpa.chat.zalo.me/api/group/link/ginfo"
    headers = zalo_mobile_headers()
    cookie_dict = {}
    for item in cookies.split(";"):
        item = item.strip()
        if "=" in item:
            key, value = item.split("=", 1)
            cookie_dict[key.strip()] = value.strip()
    try:
        response = requests.post(
            url,
            params={"zpw_ver": zpw_ver, "zpw_type": "30"},
            data=f"params={encoded}",
            headers=headers,
            cookies=cookie_dict,
            timeout=15,
        )
        if response.status_code != 200:
            return {"ok": False, "group_id": "", "group_info": {}, "message": f"HTTP {response.status_code}"}
        resp_json = response.json()
        if resp_json.get("error_code", 0) not in (0, None):
            return {"ok": False, "group_id": "", "group_info": {}, "message": resp_json.get("error_message", "Lỗi API")}
        data_field = resp_json.get("data", "")
        if isinstance(data_field, str) and data_field:
            decoded = zalo_decode(data_field, zpw_enk)
        else:
            decoded = data_field
        if decoded is None:
            return {"ok": False, "group_id": "", "group_info": {}, "message": "Giải mã response thất bại"}
        if isinstance(decoded, str):
            try:
                decoded = json.loads(decoded)
            except Exception:
                pass
        if not isinstance(decoded, dict):
            return {"ok": False, "group_id": "", "group_info": {}, "message": "Response không hợp lệ"}
        data_obj = decoded.get("data", {})
        if isinstance(data_obj, str):
            try:
                data_obj = json.loads(data_obj)
            except Exception:
                pass
        if not isinstance(data_obj, dict):
            return {"ok": False, "group_id": "", "group_info": {}, "message": "Dữ liệu nhóm không hợp lệ"}
        group_id = str(data_obj.get("groupId", "") or "").strip()
        if not group_id:
            return {"ok": False, "group_id": "", "group_info": {}, "message": "Không lấy được groupId từ link"}
        group_info = {
            "groupId": group_id,
            "gridId": group_id,
            "name": data_obj.get("name", ""),
            "desc": data_obj.get("desc", ""),
            "type": data_obj.get("type", 0),
            "creatorId": data_obj.get("creatorId", ""),
            "adminIds": data_obj.get("adminIds", []),
            "avt": data_obj.get("avt", ""),
            "fullAvt": data_obj.get("fullAvt", ""),
            "totalMember": data_obj.get("totalMember", 0),
            "setting": data_obj.get("setting", {}),
            "hasMoreMember": data_obj.get("hasMoreMember", 0),
        }
        return {"ok": True, "group_id": group_id, "group_info": group_info, "message": "OK"}
    except requests.exceptions.RequestException as e:
        return {"ok": False, "group_id": "", "group_info": {}, "message": f"Lỗi kết nối: {e}"}
    except Exception as e:
        return {"ok": False, "group_id": "", "group_info": {}, "message": f"Lỗi: {e}"}


def get_group_info(link: str, zpw_enk: str, cookies: str, mpage: int = 1,
                   avatar_size: int = 120, member_avatar_size: int = 120,
                   zpw_ver: str = None):
    from core.zalo.zalo_config import get_zpw_ver as _get_zpw_ver
    zpw_ver = _get_zpw_ver(zpw_ver)
    payload = {
        "link": link,
        "avatar_size": avatar_size,
        "member_avatar_size": member_avatar_size,
        "mpage": mpage,
    }
    plaintext = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    encoded = zalo_encode(plaintext, zpw_enk, url_encode=True)
    url = "https://tt-group-wpa.chat.zalo.me/api/group/link/ginfo"
    headers = zalo_mobile_headers()
    cookie_dict = {}
    for item in cookies.split(";"):
        item = item.strip()
        if "=" in item:
            key, value = item.split("=", 1)
            cookie_dict[key.strip()] = value.strip()
    response = requests.post(
        url,
        params={"zpw_ver": zpw_ver, "zpw_type": "30"},
        data=f"params={encoded}",
        headers=headers,
        cookies=cookie_dict,
    )
    response_json = response.json()
    data_field = response_json.get("data", "")
    if isinstance(data_field, str):
        decoded_data = zalo_decode(data_field, zpw_enk)
    else:
        decoded_data = data_field
    if decoded_data is None:
        return response_json, None, {}, []
    data_obj = decoded_data.get("data", {})
    group_info = {
        "groupId": data_obj.get("groupId", ""),
        "name": data_obj.get("name", ""),
        "desc": data_obj.get("desc", ""),
        "type": data_obj.get("type", 0),
        "creatorId": data_obj.get("creatorId", ""),
        "adminIds": data_obj.get("adminIds", []),
        "avt": data_obj.get("avt", ""),
        "fullAvt": data_obj.get("fullAvt", ""),
        "totalMember": data_obj.get("totalMember", 0),
        "setting": data_obj.get("setting", {}),
        "hasMoreMember": data_obj.get("hasMoreMember", 0),
    }
    members = []
    seen_uids = set()

    def add_member(uid, source=None):
        uid = str(uid or "").strip()
        if not uid or uid in seen_uids:
            return
        seen_uids.add(uid)
        item = {"id": uid}
        if isinstance(source, dict):
            item.update({
                "id": source.get("id", "") or source.get("uid", "") or source.get("userId", "") or uid,
                "dName": source.get("dName", ""),
                "zaloName": source.get("zaloName", ""),
                "avatar": source.get("avatar", ""),
                "avatar_25": source.get("avatar_25", ""),
                "accountStatus": source.get("accountStatus", 0),
                "type": source.get("type", 0),
            })
        members.append(item)

    for mem in data_obj.get("currentMems", []) or []:
        if isinstance(mem, dict):
            add_member(mem.get("id") or mem.get("uid") or mem.get("userId"), mem)
        else:
            add_member(mem)
    for uid in data_obj.get("memberIds", []) or []:
        add_member(uid)
    for item in data_obj.get("memVerList", []) or []:
        if isinstance(item, str):
            add_member(item.rsplit("_", 1)[0] if "_" in item else item)
        elif isinstance(item, dict):
            add_member(item.get("id") or item.get("uid") or item.get("userId"), item)
    return response_json, decoded_data, group_info, members


def get_all_group_members(link: str, zpw_enk: str, cookies: str,
                          avatar_size: int = 120, member_avatar_size: int = 120,
                          callback=None, zpw_ver: str = None):
    def log(msg, msg_type="info"):
        if callback:
            callback(msg, msg_type)
    mpage = 1
    all_members = []
    group_info = None
    while True:
        log(f"Đang lấy trang {mpage}...", "loading")
        _, _, ginfo, mems = get_group_info(
            link, zpw_enk, cookies,
            mpage=mpage,
            avatar_size=avatar_size,
            member_avatar_size=member_avatar_size,
            zpw_ver=zpw_ver,
        )
        if group_info is None:
            group_info = ginfo
            log(f"Tên nhóm: {ginfo.get('name', 'N/A')}", "info")
            log(f"Tổng số thành viên: {ginfo.get('totalMember', 0)}", "info")
        old_count = len(all_members)
        seen_ids = {
            str(mem.get("id") or mem.get("uid") or mem.get("userId") or "").strip()
            for mem in all_members
            if isinstance(mem, dict)
        }
        for mem in mems:
            uid = str(mem.get("id") or mem.get("uid") or mem.get("userId") or "").strip() if isinstance(mem, dict) else ""
            if uid and uid not in seen_ids:
                seen_ids.add(uid)
                all_members.append(mem)
        fetched = len(all_members)
        total = group_info.get("totalMember", fetched)
        log(f"Đã lấy {fetched}/{total} thành viên (trang {mpage})", "loading")
        if fetched >= total:
            break
        if fetched == old_count:
            log(f"API không trả thêm UID ở trang {mpage}; dừng để tránh lặp vô hạn. Hiện chỉ có {fetched}/{total} UID.", "warn")
            break
        mpage += 1
    total = (group_info or {}).get("totalMember", len(all_members))
    if total and len(all_members) < total:
        log(f"Chỉ lấy được {len(all_members)}/{total} UID do API không trả đủ danh sách thành viên.", "warn")
    else:
        log(f"Hoàn thành! Tổng cộng {len(all_members)} thành viên.", "success")
    return group_info, all_members
