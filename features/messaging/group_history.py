"""Lấy LỊCH SỬ tin nhắn nhóm qua Zalo Cloud Message API ``/api/cm/getrecentv2``.

Khác với ``get-last-msgs`` (chỉ 1 tin mới nhất mỗi nhóm), API này trả về NHIỀU
tin và cho phép PHÂN TRANG LÙI -> dùng để lấy TẤT CẢ tin mới kể từ một mốc
(``sinceMsgId``) đã lưu cho tới hiện tại, không bỏ sót khi có nhiều tin giữa 2
lần quét.

Cơ chế (rút từ bundle Zalo Web + kiểm chứng thực tế):
    GET https://tt-group-cm.chat.zalo.me/api/cm/getrecentv2
        ?zpw_ver=..&zpw_type=30&params=<AES({groupId,globalMsgId,count,msgIds,imei,src})>&nretry=0
    - ``globalMsgId="0"``  -> trang tin MỚI NHẤT.
    - ``globalMsgId=<minMsgId lần trước>`` -> trang CŨ HƠN kế tiếp.
    Response (sau giải mã, field ``data`` có thể là chuỗi JSON lồng):
        {"groupMsgs":[...], "minMsgId", "maxMsgId", "lastMsgId", "hasMore", "isOld"}

Host ``tt-group-cm`` là service ``group_cloud_message`` trong zpw_service_map_v3.
Session (cookies + zpwEnk) PHẢI còn hạn; nếu hết hạn API trả error_code 600
("zpw_sek bị thiếu hoặc không đúng") -> cần refresh phiên đăng nhập của account.
"""

import json

import requests

from core.zalo.enc import zalo_encode
from core.zalo.dec import zalo_decode
from core.zalo.zalo_headers import zalo_mobile_headers
from features.messaging.last_messages import _cookie_dict, _normalize_group_msg

# Service group_cloud_message (getServerInfo -> zpw_service_map_v3.group_cloud_message)
GROUP_CM_HOST = "https://tt-group-cm.chat.zalo.me"
GETRECENT_URL = GROUP_CM_HOST + "/api/cm/getrecentv2"

# Giới hạn số trang khi phân trang lùi để tránh lặp vô hạn nếu server trả bất thường.
_MAX_PAGES = 20


def _strip_gid(group_id: str) -> str:
    gid = str(group_id or "").strip()
    return gid[1:] if gid.startswith("g") else gid


def _decode_inner(resp_json: dict, zpw_enk: str):
    """Giải mã field ``data`` -> dict lịch sử ({groupMsgs, minMsgId, hasMore, ...})."""
    if resp_json.get("error_code", 0) not in (0, None):
        return None, resp_json.get("error_message", "Lỗi API"), resp_json.get("error_code")

    data_field = resp_json.get("data", "")
    if not (isinstance(data_field, str) and data_field):
        return None, "Response rỗng", resp_json.get("error_code")

    try:
        decoded = zalo_decode(data_field, zpw_enk)
    except Exception as e:  # noqa: BLE001
        return None, f"Giải mã thất bại: {e}", None
    if not isinstance(decoded, dict):
        return None, "Response không hợp lệ", None

    inner = decoded.get("data", decoded)
    if isinstance(inner, str):
        try:
            inner = json.loads(inner)
        except Exception:  # noqa: BLE001
            return None, "data lồng không phải JSON", None
    if not isinstance(inner, dict):
        return None, "data lồng không hợp lệ", None
    return inner, "OK", 0


def _fetch_page(group_id: str, global_msg_id, count: int, cookies: str,
                zpw_enk: str, imei: str, zpw_ver: str, timeout: int):
    """Gọi 1 trang getrecentv2. Trả (inner_dict | None, message, error_code)."""
    params_obj = {
        "groupId": _strip_gid(group_id),
        "globalMsgId": str(global_msg_id),
        "count": int(count),
        "msgIds": [],
        "imei": imei,
        "src": -1,
    }
    plaintext = json.dumps(params_obj, ensure_ascii=False, separators=(",", ":"))
    encoded = zalo_encode(plaintext, zpw_enk, url_encode=False)
    try:
        response = requests.get(
            GETRECENT_URL,
            params={"zpw_ver": zpw_ver, "zpw_type": "30", "params": encoded, "nretry": "0"},
            headers=zalo_mobile_headers(),
            cookies=_cookie_dict(cookies),
            timeout=timeout,
            # Gọi thẳng, không qua system proxy (tool bắt gói làm fail SSL).
            proxies={"http": None, "https": None},
        )
    except requests.exceptions.RequestException as e:
        return None, f"Lỗi kết nối: {e}", None
    if response.status_code != 200:
        return None, f"HTTP {response.status_code}", None
    try:
        resp_json = response.json()
    except Exception:  # noqa: BLE001
        return None, "Response không phải JSON", None
    return _decode_inner(resp_json, zpw_enk)


def _norm_msgs(inner: dict, group_id: str) -> list:
    out = []
    for m in (inner.get("groupMsgs") or []):
        if not isinstance(m, dict):
            continue
        gm = _normalize_group_msg(m)
        if not gm.get("msgId"):
            continue
        if not gm.get("groupId"):
            gm["groupId"] = _strip_gid(group_id)
        out.append(gm)
    return out


def fetch_recent_group_messages(group_id: str, count: int = 20, cookies: str = "",
                                zpw_enk: str = "", imei: str = "", zpw_ver: str = None,
                                timeout: int = 20) -> dict:
    """Lấy trang tin MỚI NHẤT của nhóm (globalMsgId='0').

    Returns {"ok", "groupMsgs":[mới->cũ], "minMsgId", "maxMsgId", "hasMore", "message"}.
    """
    from core.zalo.zalo_config import get_zpw_ver as _get_zpw_ver

    if not _strip_gid(group_id):
        return {"ok": False, "groupMsgs": [], "message": "Thiếu groupId"}
    zpw_ver = _get_zpw_ver(zpw_ver)

    inner, msg, ec = _fetch_page(group_id, "0", count, cookies, zpw_enk, imei, zpw_ver, timeout)
    if inner is None:
        return {"ok": False, "groupMsgs": [], "message": msg, "errorCode": ec}
    msgs = _norm_msgs(inner, group_id)
    msgs.sort(key=lambda x: int(x.get("msgId") or 0), reverse=True)
    return {
        "ok": True,
        "groupMsgs": msgs,
        "minMsgId": inner.get("minMsgId"),
        "maxMsgId": inner.get("maxMsgId"),
        "hasMore": bool(inner.get("hasMore")),
        "message": "OK",
    }


def fetch_group_messages_since(group_id: str, since_msg_id="0", count: int = 20,
                               cookies: str = "", zpw_enk: str = "", imei: str = "",
                               zpw_ver: str = None, timeout: int = 20,
                               max_pages: int = _MAX_PAGES) -> dict:
    """Lấy TẤT CẢ tin có ``msgId > since_msg_id`` (cũ->mới), phân trang lùi tới mốc.

    - ``since_msg_id="0"`` hoặc rỗng: chỉ trả trang mới nhất (không cố lấy toàn bộ
      lịch sử nhóm — tránh kéo hàng nghìn tin ở lần chạy đầu).
    - Ngược lại: gọi getrecentv2 từ '0', gom tin, nếu ``minMsgId`` của trang vẫn
      lớn hơn mốc và còn ``hasMore`` thì gọi tiếp với ``globalMsgId=minMsgId`` cho
      tới khi chạm mốc (hoặc hết trang / đạt ``max_pages``).

    Returns {"ok", "newMsgs":[cũ->mới, msgId>mốc], "latestMsgId", "reachedMark", "message"}.
    """
    from core.zalo.zalo_config import get_zpw_ver as _get_zpw_ver

    if not _strip_gid(group_id):
        return {"ok": False, "newMsgs": [], "message": "Thiếu groupId"}
    zpw_ver = _get_zpw_ver(zpw_ver)

    try:
        mark = int(str(since_msg_id or "0").strip() or "0")
    except (TypeError, ValueError):
        mark = 0

    collected = {}          # msgId(int) -> normalized msg (khử trùng lặp giữa các trang)
    anchor = "0"
    reached_mark = (mark == 0)
    latest_msg_id = 0
    last_err = "OK"

    for _ in range(max(1, max_pages)):
        inner, msg, ec = _fetch_page(group_id, anchor, count, cookies, zpw_enk,
                                     imei, zpw_ver, timeout)
        if inner is None:
            # Trang đầu lỗi -> báo lỗi; lỗi ở trang sau -> dừng, giữ tin đã gom.
            if not collected:
                return {"ok": False, "newMsgs": [], "message": msg, "errorCode": ec}
            last_err = msg
            break

        page_msgs = _norm_msgs(inner, group_id)
        for gm in page_msgs:
            mid = int(gm.get("msgId") or 0)
            if mid > latest_msg_id:
                latest_msg_id = mid
            if mid > mark:
                collected[mid] = gm

        page_min = inner.get("minMsgId")
        try:
            page_min_int = int(page_min) if page_min is not None else 0
        except (TypeError, ValueError):
            page_min_int = 0

        # Mốc 0 (lần đầu) -> chỉ lấy trang mới nhất.
        if mark == 0:
            break
        # Đã lấy tới hoặc vượt mốc -> đủ.
        if page_min_int and page_min_int <= mark:
            reached_mark = True
            break
        # Hết tin cũ hơn.
        if not inner.get("hasMore") or not page_min_int:
            break
        # Trang tiếp theo (cũ hơn).
        if str(page_min_int) == str(anchor):
            break  # không tiến triển -> tránh lặp vô hạn
        anchor = str(page_min_int)

    new_msgs = [collected[k] for k in sorted(collected.keys())]  # cũ -> mới
    return {
        "ok": True,
        "newMsgs": new_msgs,
        "latestMsgId": str(latest_msg_id) if latest_msg_id else (str(mark) if mark else "0"),
        "reachedMark": reached_mark,
        "message": last_err,
    }
