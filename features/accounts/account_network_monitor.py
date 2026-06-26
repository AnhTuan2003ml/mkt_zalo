"""
Theo dõi Chrome DevTools Protocol — bắt getLoginInfo, jr/userinfo, cookies.
"""
import base64
import json
import os
import sys
import threading
import time
from urllib.parse import parse_qs, urlparse

import requests

try:
    import websocket
except ImportError:
    websocket = None

_UTILS_DIR = os.path.dirname(os.path.abspath(__file__))
if _UTILS_DIR not in sys.path:
    sys.path.insert(0, _UTILS_DIR)

try:
    from features.accounts.get_loginInfo import decrypt_zalo_v2_cipher
except ImportError:
    from features.accounts.get_loginInfo import decrypt_zalo_v2_cipher

try:
    from features.accounts.account_manager import get_account, update_account, account_capture_complete
except ImportError:
    from features.accounts.account_manager import get_account, update_account, account_capture_complete

GET_LOGIN_INFO_MARKER = "/api/login/getLoginInfo"
USERINFO_URL_PREFIX = "https://jr.chat.zalo.me/jr/userinfo"
SERVER_INFO_URL_PREFIX = "https://wpa.chat.zalo.me/api/login/getServerInfo"
GROUP_LIST_API_PREFIX = "https://tt-group-wpa.chat.zalo.me/api/group/getlg/v4"
ZALO_URL = "https://chat.zalo.me/"
MONITOR_TIMEOUT_SEC = 300  # 5 phút để bắt group list nếu người dùng mở
CONNECT_RETRY_SEC = 2

ZALO_HOST_MARKERS = ("zalo.me", "zadn.vn", "zing.vn")
ZALO_COOKIE_URLS = [
    "https://chat.zalo.me",
    "https://wpa.chat.zalo.me",
    "https://jr.chat.zalo.me",
    "https://api-wpa.chat.zalo.me",
    "https://tt-profile-wpa.chat.zalo.me",
]

_monitors = {}
_request_meta = {}
_pending_group_bodies = {}


def _log(msg):
    print(f"[AccountMonitor] {msg}")


def start_account_network_monitor(account_id: str, debug_port: int):
    if websocket is None:
        _log("Thiếu package websocket-client. Chạy: pip install websocket-client")
        return

    stop_account_network_monitor(account_id)

    stop_event = threading.Event()
    thread = threading.Thread(
        target=_monitor_worker,
        args=(account_id, int(debug_port), stop_event),
        daemon=True,
        name=f"zalo-monitor-{account_id[:8]}",
    )
    _monitors[account_id] = {"thread": thread, "stop": stop_event}
    thread.start()
    _log(f"Started monitor account={account_id} port={debug_port}")


def stop_account_network_monitor(account_id: str):
    entry = _monitors.pop(account_id, None)
    if entry:
        entry["stop"].set()
    _request_meta.pop(account_id, None)


def _is_zalo_url(url: str) -> bool:
    if not url:
        return False
    return any(m in url for m in ZALO_HOST_MARKERS)


def _is_target_api_url(url: str) -> bool:
    if not url:
        return False
    return (GET_LOGIN_INFO_MARKER in url or 
            url.startswith(USERINFO_URL_PREFIX) or 
            url.startswith(SERVER_INFO_URL_PREFIX) or
            url.startswith(GROUP_LIST_API_PREFIX))


def _header_get(headers: dict, key: str) -> str:
    if not headers:
        return ""
    for k, v in headers.items():
        if k.lower() == key.lower():
            return v or ""
    return ""


def _cookies_from_associated(associated_cookies) -> str:
    parts = []
    seen = set()
    for item in associated_cookies or []:
        cookie_obj = item.get("cookie") if isinstance(item, dict) else None
        if not cookie_obj:
            continue
        name = cookie_obj.get("name")
        value = cookie_obj.get("value")
        if name and value is not None and name not in seen:
            seen.add(name)
            parts.append(f"{name}={value}")
    return "; ".join(parts)


def _format_network_cookies(cookies_list) -> str:
    parts = []
    seen = set()
    for c in cookies_list or []:
        if not isinstance(c, dict):
            continue
        name = c.get("name")
        value = c.get("value")
        if name and value is not None and name not in seen:
            seen.add(name)
            parts.append(f"{name}={value}")
    return "; ".join(parts)


def _valid_zalo_cookie(cookie_str: str) -> bool:
    if not cookie_str or len(cookie_str) < 8:
        return False
    lower = cookie_str.lower()
    return any(k in lower for k in ("zpw_sek", "zpsid", "__zi", "zpdid"))


def _merge_request_meta(account_id: str, request_id: str, url: str = "",
                        headers: dict = None, associated_cookies=None):
    if account_id not in _request_meta:
        _request_meta[account_id] = {}
    meta = _request_meta[account_id].setdefault(
        request_id, {"url": "", "headers": {}}
    )
    if url:
        meta["url"] = url
    if headers:
        if isinstance(headers, dict):
            meta["headers"].update(headers)
        elif isinstance(headers, list):
            for h in headers:
                if isinstance(h, dict):
                    n = h.get("name") or h.get("key")
                    v = h.get("value")
                    if n:
                        meta["headers"][n] = v

    cookie = _header_get(meta["headers"], "cookie")
    if not cookie and associated_cookies:
        cookie = _cookies_from_associated(associated_cookies)
        if cookie:
            meta["headers"]["Cookie"] = cookie
    return meta


def _extract_cookie_from_meta(meta: dict) -> str:
    if not meta:
        return ""
    return _header_get(meta.get("headers") or {}, "cookie")


def _save_cookies(account_id: str, cookie_str: str, source: str = ""):
    if not _valid_zalo_cookie(cookie_str):
        return False
    update_account(account_id, cookies=cookie_str.strip())
    _log(f"Đã lưu cookies ({source}) account={account_id} len={len(cookie_str)}")
    return True


def _get_zalo_ws_url(debug_port: int, deadline: float):
    while time.time() < deadline:
        try:
            resp = requests.get(
                f"http://127.0.0.1:{debug_port}/json",
                timeout=3,
            )
            tabs = resp.json()
            for tab in tabs:
                url = tab.get("url", "")
                ws_url = tab.get("webSocketDebuggerUrl")
                if ws_url and "chat.zalo.me" in url:
                    return ws_url
            for tab in tabs:
                ws_url = tab.get("webSocketDebuggerUrl")
                if ws_url and tab.get("type") == "page":
                    return ws_url
        except Exception:
            pass
        time.sleep(CONNECT_RETRY_SEC)
    return None




def _navigate_to_zalo_after_network_enabled(ws, next_id_fn, account_id: str):
    """
    Sau khi monitor đã bật Network.enable, tự mở Zalo.
    Có 2 cách:
    1) Page.navigate
    2) Runtime.evaluate window.location.href fallback
    """
    try:
        ws.send(json.dumps({"id": next_id_fn(), "method": "Page.enable", "params": {}}))
        ws.send(json.dumps({"id": next_id_fn(), "method": "Runtime.enable", "params": {}}))

        nav_id = next_id_fn()
        ws.send(json.dumps({
            "id": nav_id,
            "method": "Page.navigate",
            "params": {"url": ZALO_URL}
        }))

        _log(f"🚀 Đã gửi Page.navigate sang Zalo account={account_id} url={ZALO_URL}")

        # Fallback sau Page.navigate: ép location.href
        time.sleep(0.5)
        ws.send(json.dumps({
            "id": next_id_fn(),
            "method": "Runtime.evaluate",
            "params": {
                "expression": "window.location.href = 'https://chat.zalo.me/';",
                "awaitPromise": False
            }
        }))

        _log(f"🚀 Đã gửi fallback window.location.href sang Zalo account={account_id}")
        return True

    except Exception as e:
        _log(f"❌ Không điều hướng được sang Zalo account={account_id}: {e}")
        return False


def _trigger_page_reload(ws, next_id_fn):
    """Send CDP command to reload current page."""
    try:
        if ws and next_id_fn:
            cmd_id = next_id_fn()
            ws.send(json.dumps({
                "id": cmd_id,
                "method": "Page.reload",
                "params": {}
            }))
            return True
    except Exception:
        pass
    return False


def _parse_login_url(url: str):
    try:
        qs = parse_qs(urlparse(url).query)
        zcid = (qs.get("zcid") or [""])[0]
        zcid_ext = (qs.get("zcid_ext") or [""])[0]
        return zcid, zcid_ext
    except Exception:
        return "", ""


def _extract_imei_from_url(url: str) -> str:
    """
    IMEI/Zalo client id phải lấy từ payload/query của Zalo Web.
    Ví dụ:
    /api/login/getServerInfo?imei=228878ef-...&type=30...
    """
    try:
        qs = parse_qs(urlparse(url).query)
        imei = (qs.get("imei") or [""])[0]
        return str(imei or "").strip()
    except Exception:
        return ""


def _save_imei_from_url(account_id: str, url: str, source: str = "") -> bool:
    imei = _extract_imei_from_url(url)
    if not imei:
        return False

    update_account(account_id, imei=imei)
    _log(f"✅ Đã lưu imei từ {source or 'url'} account={account_id}: {imei}")
    return True


def _process_login_info(account_id: str, url: str, headers: dict, body_text: str):
    zcid, zcid_ext = _parse_login_url(url)
    if not zcid or not zcid_ext:
        _log(f"getLoginInfo thiếu zcid/zcid_ext account={account_id}")
        return

    cookie = _header_get(headers, "cookie")
    if cookie:
        _save_cookies(account_id, cookie, "getLoginInfo-header")

    try:
        payload = json.loads(body_text)
    except json.JSONDecodeError:
        return

    if payload.get("error_code", -1) != 0:
        return

    cipher_b64 = payload.get("data", "")
    if not cipher_b64 or not isinstance(cipher_b64, str):
        return

    try:
        plain = decrypt_zalo_v2_cipher(zcid, zcid_ext, cipher_b64)
        decrypted = json.loads(plain)
    except Exception as e:
        _log(f"Giải mã getLoginInfo lỗi: {e}")
        return

    inner = decrypted.get("data") if isinstance(decrypted, dict) else {}
    if not isinstance(inner, dict):
        return

    zpw_enk = inner.get("zpw_enk", "")
    if not zpw_enk:
        return

    fields = {
        "zpwEnk": zpw_enk,
        "loginCaptured": True,
    }
    if cookie:
        fields["cookies"] = cookie

    uid = inner.get("uid")
    if uid:
        fields["uid"] = str(uid)

    phone = inner.get("phone_number")
    if phone:
        fields["phoneNumber"] = str(phone)

    update_account(account_id, **fields)
    _log(f"✅ Đã lưu zpwEnk account={account_id}")


def _process_userinfo(account_id: str, headers: dict, body_text: str):
    cookie = _header_get(headers, "cookie")
    if cookie:
        _save_cookies(account_id, cookie, "userinfo-header")

    try:
        payload = json.loads(body_text)
    except json.JSONDecodeError:
        return

    if payload.get("error_code", -1) != 0:
        return

    data = payload.get("data") or {}
    if not data.get("logged"):
        return

    info = data.get("info") or {}
    name = (info.get("name") or "").strip()
    avatar_url = (info.get("avatar") or "").strip()

    if not name and not avatar_url:
        return

    fields = {"userinfoCaptured": True}
    if name:
        fields["name"] = name
    if avatar_url:
        fields["avatarUrl"] = avatar_url
    if cookie:
        fields["cookies"] = cookie

    update_account(account_id, **fields)
    _log(f"Đã lưu userinfo name={name!r} account={account_id}")


def _process_server_info(account_id: str, url: str, headers: dict, body_text: str):
    """Process getServerInfo response to capture zpwEnk/server data and imei."""
    # IMEI phải lấy từ query của getServerInfo, không tự sinh UUID, không lấy serial máy.
    _save_imei_from_url(account_id, url, "process_server_info")

    # Try to parse zcid/zcid_ext from URL
    zcid, zcid_ext = _parse_login_url(url)
    
    cookie = _header_get(headers, "cookie")
    if cookie:
        _save_cookies(account_id, cookie, "serverinfo-header")

    try:
        payload = json.loads(body_text)
    except json.JSONDecodeError:
        _log(f"[DEBUG] getServerInfo JSON parse error: {body_text[:100]}")
        return

    if payload.get("error_code", -1) != 0:
        _log(f"[DEBUG] getServerInfo error_code: {payload.get('error_code')}")
        return

    # Check if response has encrypted data
    cipher_b64 = payload.get("data", "")
    decrypted_data = None
    
    # Log what we're processing
    _log(f"[DEBUG] getServerInfo cipher_b64 len={len(cipher_b64) if cipher_b64 else 0}, zcid={bool(zcid)}, zcid_ext={bool(zcid_ext)}")
    
    # Try to decrypt if encrypted (like getLoginInfo format)
    if cipher_b64 and isinstance(cipher_b64, str) and zcid and zcid_ext:
        try:
            plain = decrypt_zalo_v2_cipher(zcid, zcid_ext, cipher_b64)
            decrypted_payload = json.loads(plain)
            decrypted_data = decrypted_payload.get("data", {}) if isinstance(decrypted_payload, dict) else {}
            _log(f"[DEBUG] getServerInfo decrypted, keys={list(decrypted_data.keys())}")
        except Exception as e:
            _log(f"Giải mã getServerInfo lỗi: {e}")
    
    # If not encrypted or decryption failed, use raw data
    if not decrypted_data:
        decrypted_data = payload.get("data", {}) if isinstance(payload.get("data"), dict) else {}
        _log(f"[DEBUG] getServerInfo raw data, keys={list(decrypted_data.keys())}")
    
    if not isinstance(decrypted_data, dict):
        _log(f"[DEBUG] getServerInfo data not dict: {type(decrypted_data)}")
        return
    
    # Extract zpwEnk from decrypted data
    zpw_enk = decrypted_data.get("zpw_enk") or decrypted_data.get("zpwEnk", "")
    
    _log(f"[DEBUG] getServerInfo zpw_enk={zpw_enk[:20] if zpw_enk else 'EMPTY'}")
    
    if zpw_enk:
        fields = {
            "zpwEnk": zpw_enk,
        }
        if cookie:
            fields["cookies"] = cookie
        
        update_account(account_id, **fields)
        _log(f"✅ Đã lưu zpwEnk từ getServerInfo account={account_id}")
    else:
        _log(f"📡 Captured getServerInfo account={account_id}")


def _process_group_list(account_id: str, headers: dict, body_text: str, zpw_enk: str):
    """
    Process getlg/v4 response to extract group list and fetch group details.
    
    API: https://tt-group-wpa.chat.zalo.me/api/group/getlg/v4
    Response: {"error_code": 0, "data": "encrypted_string"}
    """
    try:
        from group_manager import extract_groups_from_getlg_response, save_account_groups
    except ImportError:
        from .group_manager import extract_groups_from_getlg_response, save_account_groups
    
    if not zpw_enk:
        _pending_group_bodies[account_id] = {
            "headers": headers or {},
            "body": body_text or "",
            "savedAt": time.time(),
        }
        _log(f"[group_list] Đã bắt getlg/v4 nhưng chưa có zpwEnk, tạm giữ body account={account_id}")
        return
    
    try:
        payload = json.loads(body_text)
    except json.JSONDecodeError as e:
        _log(f"[group_list] JSON decode error: {e}")
        return
    
    error_code = payload.get("error_code", -1)
    if error_code != 0:
        _log(f"[group_list] API error_code={error_code}")
        return
    
    response_data = payload.get("data", "")
    if not response_data:
        _log(f"[group_list] Không có data trong response")
        return
    
    _log(f"[group_list] Extracting: zpw_enk len={len(zpw_enk)}, data len={len(response_data)}")
    
    # Extract group IDs
    group_ids = extract_groups_from_getlg_response(response_data, zpw_enk)
    
    if group_ids:
        _log(f"[group_list] Extracted {len(group_ids)} group IDs")
        
        # Get cookies for fetching group details. Ưu tiên cookie ngay trên request getlg/v4,
        # vì trên máy khác account cookies trong file data có thể chưa kịp lưu hoặc đã hết hạn.
        acc = get_account(account_id)
        cookies = _header_get(headers, "cookie") or (acc.get("cookies", "") if acc else "")
        if cookies:
            _save_cookies(account_id, cookies, "getlg-header")
        
        # Lưu group IDs trước; group_manager sẽ tự chạy thread lấy chi tiết nhóm song song
        save_account_groups(account_id, group_ids, zpw_enk, cookies)
        _log(f"✅ Đã lưu {len(group_ids)} group IDs và đã kích hoạt lấy chi tiết nhóm song song account={account_id}")
        
        # Dừng monitor sau khi đã có ID nhóm; thread lấy chi tiết nhóm vẫn chạy nền
        _log(f"Dừng monitor account={account_id} sau khi lấy được danh sách ID nhóm")
        stop_account_network_monitor(account_id)
    else:
        _log(f"[group_list] Không trích xuất được nhóm account={account_id}")



def _try_process_pending_group_list(account_id: str):
    pending = _pending_group_bodies.get(account_id)
    if not pending:
        return False

    acc = get_account(account_id)
    if not acc:
        return False

    zpw_enk = (acc.get("zpwEnk") or "").strip()
    if not zpw_enk:
        return False

    body = pending.get("body") or ""
    headers = pending.get("headers") or {}

    if not body:
        return False

    _log(f"[group_list] Có zpwEnk rồi, xử lý lại getlg/v4 đã giữ account={account_id}")
    _pending_group_bodies.pop(account_id, None)
    _process_group_list(account_id, headers, body, zpw_enk)
    return True


def _handle_get_cookies_result(account_id: str, result: dict, merged_store: dict):
    for c in result.get("cookies") or []:
        name = c.get("name")
        value = c.get("value")
        if name and value is not None:
            merged_store[name] = value
    cookie_str = "; ".join(f"{k}={v}" for k, v in merged_store.items())
    if _valid_zalo_cookie(cookie_str):
        _save_cookies(account_id, cookie_str, "Network.getCookies")


def _request_network_cookies(ws, account_id: str, next_id_fn,
                             pending_cookie_cmds: dict, merged_store: dict):
    for url in ZALO_COOKIE_URLS:
        cmd_id = next_id_fn()
        pending_cookie_cmds[cmd_id] = merged_store
        ws.send(
            json.dumps(
                {
                    "id": cmd_id,
                    "method": "Network.getCookies",
                    "params": {"urls": [url]},
                }
            )
        )


def _monitor_worker(account_id: str, debug_port: int, stop_event: threading.Event):
    deadline = time.time() + MONITOR_TIMEOUT_SEC
    msg_id = [0]
    pending_body = {}
    pending_cookie_cmds = {}
    cookie_merge = {}
    reload_done = [False]

    def next_id():
        msg_id[0] += 1
        return msg_id[0]

    while time.time() < deadline and not stop_event.is_set():
        if account_capture_complete(account_id):
            _log(f"Đủ dữ liệu, dừng monitor account={account_id}")
            break

        ws_url = _get_zalo_ws_url(debug_port, min(deadline, time.time() + 15))
        if not ws_url:
            time.sleep(CONNECT_RETRY_SEC)
            continue

        ws = None
        try:
            ws = websocket.create_connection(ws_url, timeout=10)
            ws.settimeout(1.0)

            ws.send(json.dumps({"id": next_id(), "method": "Network.enable", "params": {}}))
            
            try:
                ws.send(json.dumps({"id": next_id(), "method": "Network.setCacheDisabled", "params": {"cacheDisabled": True}}))
            except Exception:
                pass

            _navigate_to_zalo_after_network_enabled(ws, next_id, account_id)
            _request_network_cookies(
                ws, account_id, next_id, pending_cookie_cmds, cookie_merge
            )

            if account_id not in _request_meta:
                _request_meta[account_id] = {}

            while time.time() < deadline and not stop_event.is_set():
                if account_capture_complete(account_id):
                    break

                try:
                    raw = ws.recv()
                except websocket.WebSocketTimeoutException:
                    continue
                except Exception:
                    break

                if not raw:
                    continue

                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                if "method" in message:
                    method = message["method"]
                    params = message.get("params") or {}

                    if method == "Network.requestWillBeSent":
                        req = params.get("request") or {}
                        url = req.get("url", "")

                        # Bắt imei từ request getServerInfo:
                        # https://wpa.chat.zalo.me/api/login/getServerInfo?imei=...
                        if url.startswith(SERVER_INFO_URL_PREFIX):
                            _save_imei_from_url(account_id, url, "requestWillBeSent:getServerInfo")

                        rid = params.get("requestId")
                        if not rid:
                            continue
                        meta = _merge_request_meta(
                            account_id, rid, url=url,
                            headers=req.get("headers") or {},
                        )
                        if _is_target_api_url(url) or _is_zalo_url(url):
                            cookie = _extract_cookie_from_meta(meta)
                            if cookie:
                                _save_cookies(
                                    account_id, cookie,
                                    f"request:{url[:40]}",
                                )

                    elif method == "Network.requestWillBeSentExtraInfo":
                        rid = params.get("requestId")
                        if not rid:
                            continue
                        headers = params.get("headers") or {}
                        associated = params.get("associatedCookies") or []
                        meta = _merge_request_meta(
                            account_id, rid,
                            headers=headers,
                            associated_cookies=associated,
                        )
                        url = meta.get("url", "")
                        cookie = _extract_cookie_from_meta(meta)
                        if cookie and (_is_zalo_url(url) or _is_target_api_url(url)):
                            _save_cookies(
                                account_id, cookie,
                                "requestWillBeSentExtraInfo",
                            )

                    elif method == "Network.responseReceived":
                        response = params.get("response") or {}
                        url = response.get("url", "")
                        rid = params.get("requestId")
                        if not rid:
                            continue
                        if url.startswith(SERVER_INFO_URL_PREFIX):
                            _save_imei_from_url(account_id, url, "responseReceived:getServerInfo")

                        if _is_target_api_url(url):
                            _merge_request_meta(
                                account_id,
                                rid,
                                url=url,
                                headers=response.get("headers") or {},
                            )
                            cmd_id = next_id()
                            pending_body[cmd_id] = rid
                            ws.send(
                                json.dumps(
                                    {
                                        "id": cmd_id,
                                        "method": "Network.getResponseBody",
                                        "params": {"requestId": rid},
                                    }
                                )
                            )

                elif "id" in message:
                    mid = message["id"]

                    if mid in pending_cookie_cmds:
                        merged = pending_cookie_cmds.pop(mid)
                        _handle_get_cookies_result(
                            account_id, message.get("result") or {}, merged
                        )
                        continue

                    if mid in pending_body:
                        rid = pending_body.pop(mid)
                        meta = _request_meta.get(account_id, {}).get(rid, {})
                        url = meta.get("url", "")
                        headers = meta.get("headers") or {}

                        result = message.get("result") or {}
                        if result.get("error"):
                            continue

                        body = result.get("body", "")
                        if result.get("base64Encoded") and body:
                            try:
                                body = base64.b64decode(body).decode(
                                    "utf-8", errors="replace"
                                )
                            except Exception:
                                pass

                        if not body:
                            continue

                        if GET_LOGIN_INFO_MARKER in url:
                            _process_login_info(account_id, url, headers, body)
                            _try_process_pending_group_list(account_id)
                            # Reload page once after capturing login info để bắt getServerInfo
                            if not reload_done[0] and ws:
                                time.sleep(1)
                                _trigger_page_reload(ws, next_id)
                                reload_done[0] = True
                                _log(f"🔄 Reload page account={account_id}")
                        elif url.startswith(USERINFO_URL_PREFIX):
                            _process_userinfo(account_id, headers, body)
                        elif url.startswith(SERVER_INFO_URL_PREFIX):
                            _process_server_info(account_id, url, headers, body)
                            _try_process_pending_group_list(account_id)
                        elif url.startswith(GROUP_LIST_API_PREFIX):
                            # Get current zpwEnk to decrypt group list
                            acc = get_account(account_id)
                            if acc:
                                zpw_enk = acc.get("zpwEnk", "")
                                _process_group_list(account_id, headers, body, zpw_enk)

        except Exception as e:
            _log(f"Monitor lỗi account={account_id}: {e}")
        finally:
            if ws:
                try:
                    ws.close()
                except Exception:
                    pass

        if not stop_event.is_set() and not account_capture_complete(account_id):
            time.sleep(CONNECT_RETRY_SEC)

    _monitors.pop(account_id, None)
    _request_meta.pop(account_id, None)
    
    # Không auto tạo/lấy imei từ hệ thống.
    # IMEI phải được bắt từ request:
    # https://wpa.chat.zalo.me/api/login/getServerInfo?imei=...
    _log(f"Monitor kết thúc account={account_id}")
