"""
Quản lý nhóm Zalo của tài khoản.
Lấy danh sách nhóm từ API response và lưu vào account.
"""
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from core.zalo.dec import zalo_decode
from core.zalo.zalo_config import get_zpw_ver


def extract_groups_from_getlg_response(response_data: str, zpw_enk: str) -> list:
    """
    Giải mã response từ getlg API và trích xuất danh sách group IDs.
    
    API: https://tt-group-wpa.chat.zalo.me/api/group/getlg/v4
    
    Response format (after decryption):
    {
      "error_code": 0,
      "data": {
        "gridVerMap": {
          "group_id_1": "version_1",
          "group_id_2": "version_2",
          ...
        }
      }
    }
    
    Args:
        response_data: Dữ liệu encrypted từ response.data
        zpw_enk: Khóa giải mã
    
    Returns:
        List[dict] - Danh sách nhóm: [{"groupId": "...", "version": "..."}, ...]
    """
    try:
        if not response_data or not zpw_enk:
            print(f"[group_manager] Missing data or key: response_data={bool(response_data)}, zpw_enk={bool(zpw_enk)}")
            return []
        
        print(f"[group_manager] Decoding: response_data len={len(response_data)}, zpw_enk len={len(zpw_enk)}")
        
        # Giải mã một lần (response body chứa encrypted data)
        decrypted = zalo_decode(response_data, zpw_enk)
        if not decrypted:
            print(f"[group_manager] zalo_decode returned empty")
            return []
        
        print(f"[group_manager] Decrypted type={type(decrypted)}")
        
        if isinstance(decrypted, str):
            decrypted = json.loads(decrypted)
        
        print(f"[group_manager] JSON keys={list(decrypted.keys()) if isinstance(decrypted, dict) else 'N/A'}")
        
        # Lấy data object
        data_obj = decrypted.get("data", {})
        if not isinstance(data_obj, dict):
            print(f"[group_manager] 'data' field is not dict: {type(data_obj)}")
            return []
        
        print(f"[group_manager] Data object keys={list(data_obj.keys())}")
        
        # Trích xuất gridVerMap từ data
        grid_ver_map = data_obj.get("gridVerMap", {})
        if not grid_ver_map:
            print(f"[group_manager] No gridVerMap found in data")
            return []
        
        print(f"[group_manager] gridVerMap keys count={len(grid_ver_map)}")
        
        # Chuyển đổi thành danh sách
        groups = []
        for group_id, version in grid_ver_map.items():
            # Chỉ lưu groupId, không cần lưu version
            groups.append(str(group_id))
        
        print(f"[group_manager] Extracted {len(groups)} group IDs")
        return groups
    
    except Exception as e:
        import traceback
        print(f"[group_manager] Error extracting groups: {e}")
        print(f"[group_manager] Traceback: {traceback.format_exc()}")
        return []


_fetching_accounts = set()
_fetching_lock = threading.Lock()


def _normalize_group_ids(group_ids: list) -> list:
    """Loại trùng group_id, giữ đúng thứ tự ban đầu."""
    clean_ids = []
    seen = set()
    for gid in group_ids or []:
        gid = str(gid).strip()
        if gid and gid not in seen:
            seen.add(gid)
            clean_ids.append(gid)
    return clean_ids


def _merge_old_group_info(account_id: str, group_ids: list) -> list:
    """Lưu placeholder trước để UI có danh sách group ngay, sau đó worker sẽ cập nhật chi tiết."""
    try:
        from features.accounts.account_manager import get_account
    except ImportError:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from features.accounts.account_manager import get_account

    acc = get_account(account_id) or {}
    old_groups = acc.get("personalGroups") or []
    old_map = {}
    for g in old_groups:
        if isinstance(g, dict) and g.get("groupId"):
            old_map[str(g.get("groupId"))] = g

    detailed_groups = []
    for gid in group_ids:
        old = old_map.get(gid, {})
        detailed_groups.append({
            "groupId": gid,
            "name": old.get("name") or f"Group {gid[:8]}",
            "avatar": old.get("avatar") or "",
            "fullAvt": old.get("fullAvt") or "",
            "memberCount": old.get("memberCount") or 0,
            "fetchStatus": old.get("fetchStatus") or "pending",
        })
    return detailed_groups


def _fetch_one_group_detail(group_id: str, zpw_enk: str, cookies: str, zpw_ver: str = None, imei: str = "") -> dict:
    """
    Chỉ lấy thông tin nhóm từ getmg-v2.
    Không lấy profile từng thành viên, không gọi get_single_profile.
    """
    try:
        try:
            from features.members.get_members import get_members
        except ImportError:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            from features.members.get_members import get_members

        _response_json, _decoded_data, group_info, mem_list = get_members(
            group_id, zpw_enk, cookies,
            imei=imei,
            timeout=15,
            zpw_ver=get_zpw_ver(zpw_ver)
        )
        group_info = group_info or {}
        mem_list = mem_list or []

        name = group_info.get("grid_name") or group_info.get("name") or ""
        avatar = group_info.get("grid_avatar") or ""
        full_avt = group_info.get("grid_fullAvt") or ""
        member_count = group_info.get("grid_totalMember") or len(mem_list) or 0

        # Nếu API không trả được thông tin thật thì không đánh dấu done.
        # Tránh UI thấy pending/placeholder mãi mà không biết lỗi.
        if not name and not avatar and not full_avt and not member_count:
            raise RuntimeError(f"getmg-v2 không trả chi tiết nhóm. Có thể cookie/zpwEnk hết hạn hoặc mạng máy này không gọi được Zalo API. response={_response_json}")

        return {
            "groupId": str(group_id),
            "name": name or f"Group {str(group_id)[:8]}",
            "desc": group_info.get("grid_desc") or "",
            "avatar": avatar,
            "fullAvt": full_avt,
            "memberCount": member_count,
            "fetchStatus": "done",
        }
    except Exception as e:
        print(f"[group_manager] Fetch detail failed group={group_id}: {e}", flush=True)
        return {
            "groupId": str(group_id),
            "name": f"Group {str(group_id)[:8]}",
            "avatar": "",
            "fullAvt": "",
            "memberCount": 0,
            "fetchStatus": "error",
            "error": str(e),
        }


def fetch_and_update_group_details_parallel(account_id: str, group_ids: list, zpw_enk: str, cookies: str = "",
                                            max_workers: int = 4, zpw_ver: str = None):
    """
    Chạy song song API getmg-v2 để lấy name/avatar/memberCount cho từng group.
    Hàm này chạy trong thread riêng, không block monitor.
    """
    try:
        from features.accounts.account_manager import get_account, update_account
    except ImportError:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from features.accounts.account_manager import get_account, update_account

    group_ids = _normalize_group_ids(group_ids)
    if not group_ids or not zpw_enk or not cookies:
        print(f"[group_manager] Skip detail fetch: ids={len(group_ids)}, zpw_enk={bool(zpw_enk)}, cookies={bool(cookies)}", flush=True)
        # Không để trạng thái pending vô hạn khi thiếu credential.
        try:
            acc = get_account(account_id) or {}
            old_groups = acc.get("personalGroups") or []
            marked = []
            for gid in group_ids:
                old = next((g for g in old_groups if isinstance(g, dict) and str(g.get("groupId")) == str(gid)), {})
                marked.append({
                    "groupId": str(gid),
                    "name": old.get("name") or f"Group {str(gid)[:8]}",
                    "avatar": old.get("avatar") or "",
                    "fullAvt": old.get("fullAvt") or "",
                    "memberCount": old.get("memberCount") or 0,
                    "fetchStatus": "error",
                    "error": "Thiếu cookies hoặc zpwEnk nên chưa lấy được chi tiết nhóm. Hãy mở lại tài khoản Zalo để monitor bắt lại session."
                })
            update_account(account_id, personalGroups=marked)
        except Exception as e:
            print(f"[group_manager] Mark error failed: {e}", flush=True)
        return False

    with _fetching_lock:
        if account_id in _fetching_accounts:
            print(f"[group_manager] Detail fetch already running account={account_id}", flush=True)
            return False
        _fetching_accounts.add(account_id)

    started = time.time()
    results = {}
    zpw_ver = get_zpw_ver(zpw_ver)
    workers = max(1, min(int(max_workers or 4), len(group_ids)))
    print(f"[group_manager] 🚀 Fetching {len(group_ids)} group details parallel, workers={workers}", flush=True)

    try:
        acc = get_account(account_id) or {}
        imei = acc.get("imei", "")
        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_map = {
                executor.submit(_fetch_one_group_detail, gid, zpw_enk, cookies, zpw_ver, imei): gid
                for gid in group_ids
            }
            for future in as_completed(future_map):
                gid = str(future_map[future])
                try:
                    info = future.result()
                except Exception as e:
                    info = {
                        "groupId": gid,
                        "name": f"Group {gid[:8]}",
                        "avatar": "",
                        "fullAvt": "",
                        "memberCount": 0,
                        "fetchStatus": "error",
                        "error": str(e),
                    }
                results[gid] = info
                print(f"[group_manager] ✅ Detail {len(results)}/{len(group_ids)} group={gid}", flush=True)

        # Merge theo thứ tự group_ids gốc, giữ lại dữ liệu cũ nếu API fail.
        acc = get_account(account_id) or {}
        old_groups = acc.get("personalGroups") or []
        old_map = {}
        for g in old_groups:
            if isinstance(g, dict) and g.get("groupId"):
                old_map[str(g.get("groupId"))] = g

        merged = []
        for gid in group_ids:
            old = old_map.get(gid, {})
            new = results.get(gid, {})
            merged.append({
                "groupId": gid,
                "name": new.get("name") or old.get("name") or f"Group {gid[:8]}",
                "desc": new.get("desc") or old.get("desc") or "",
                "avatar": new.get("avatar") or old.get("avatar") or "",
                "fullAvt": new.get("fullAvt") or old.get("fullAvt") or "",
                "memberCount": new.get("memberCount") or old.get("memberCount") or 0,
                "fetchStatus": new.get("fetchStatus") or old.get("fetchStatus") or "done",
            })

        update_account(account_id, personalGroups=merged)
        print(f"[group_manager] ✅ Updated {len(merged)} detailed personalGroups in {time.time() - started:.1f}s", flush=True)
        return True
    finally:
        with _fetching_lock:
            _fetching_accounts.discard(account_id)


def start_fetch_group_details_parallel(account_id: str, group_ids: list, zpw_enk: str, cookies: str = "",
                                       max_workers: int = 4, zpw_ver: str = None):
    """Start worker daemon để monitor không bị treo khi lấy chi tiết nhóm."""
    thread = threading.Thread(
        target=fetch_and_update_group_details_parallel,
        args=(account_id, group_ids, zpw_enk, cookies, max_workers, get_zpw_ver(zpw_ver)),
        daemon=True,
        name=f"group-detail-fetch-{str(account_id)[:8]}",
    )
    thread.start()
    return thread



def save_account_groups(account_id: str, group_ids: list, zpw_enk: str = "", cookies: str = ""):
    """
    Lưu danh sách group ID ngay sau khi giải mã getlg/v4.
    Sau đó chạy thread riêng để lấy chi tiết nhóm song song.
    Không lấy chi tiết thành viên/profile từng thành viên.
    """
    try:
        from features.accounts.account_manager import update_account
    except ImportError:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from features.accounts.account_manager import update_account

    clean_ids = _normalize_group_ids(group_ids)
    if not clean_ids:
        print("[group_manager] No group_ids to save", flush=True)
        return False

    # 1) Lưu placeholder trước để UI/monitor thấy có personalGroups ngay.
    placeholder_groups = _merge_old_group_info(account_id, clean_ids)
    update_account(account_id, personalGroups=placeholder_groups)
    print(f"[group_manager] ✅ Saved {len(placeholder_groups)} personalGroups placeholders immediately", flush=True)

    # 2) Sau đó lấy chi tiết name/avatar/memberCount song song, không block monitor.
    if zpw_enk and cookies:
        start_fetch_group_details_parallel(account_id, clean_ids, zpw_enk, cookies, max_workers=4)
        print(f"[group_manager] 🚀 Started parallel detail fetch for {len(clean_ids)} groups", flush=True)
    else:
        print(f"[group_manager] Skip parallel detail fetch: zpw_enk={bool(zpw_enk)}, cookies={bool(cookies)}", flush=True)

    return True



def get_account_groups(account_id: str) -> list:
    """
    Lấy danh sách nhóm cá nhân của tài khoản.
    
    Args:
        account_id: ID tài khoản
    
    Returns:
        List[dict] - Danh sách nhóm
    """
    try:
        from features.accounts.account_manager import get_account
    except ImportError:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from features.accounts.account_manager import get_account
    
    account = get_account(account_id)
    if not account:
        return []
    
    return account.get("personalGroups", [])


if __name__ == "__main__":
    import sys as _sys

    def _safe_reconfigure():
        try:
            if _sys.stdout and hasattr(_sys.stdout, 'reconfigure'):
                _sys.stdout.reconfigure(encoding='utf-8')
        except Exception:
            pass

    _safe_reconfigure()

    # Test
    print("[group_manager] Test mode")
