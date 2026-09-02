# -*- coding: utf-8 -*-
"""Gửi tin nhắn dạng link card khi nội dung chứa link nhóm Zalo.

Luồng (theo bundle Zalo Web, hàm sendLinkMessage):
1. Nội dung chứa link https://zalo.me/g/... -> gọi /api/group/link/ginfo lấy
   thông tin nhóm (tên, ảnh) trước.
2. Gửi tin qua:
   - Nhóm : POST https://tt-group-wpa.chat.zalo.me/api/group/sendlink
            payload có grid, mentionInfo "[]", imei.
   - 1-1  : POST https://tt-chat3-wpa.chat.zalo.me/api/message/link
            payload có toId (chú ý: toId, không phải toid).
   Payload chung: {msg, href, src, title, desc, language, thumb, type, media, ttl, clientId}.
3. Lỗi ở bất kỳ bước nào -> caller fallback gửi text thường, không mất tin.
"""
import json
import re
import time
from typing import Optional, Tuple

import requests

from core.zalo.enc import zalo_encode
from core.zalo.dec import zalo_decode
from core.zalo.zalo_config import get_zpw_ver
from core.zalo.zalo_headers import zalo_mobile_headers

GROUP_LINK_RE = re.compile(r"https?://zalo\.me/g/[A-Za-z0-9_]+", re.IGNORECASE)

GROUP_SENDLINK_URL = "https://tt-group-wpa.chat.zalo.me/api/group/sendlink"
MESSAGE_LINK_URL = "https://tt-chat3-wpa.chat.zalo.me/api/message/link"
# API Zalo Web dùng để lấy metadata link (getLinkInformation trong bundle):
# trả về thumb photolinkv2 có chữ ký server + title/desc/src/media chuẩn.
PARSELINK_URL = "https://tt-files-wpa.chat.zalo.me/api/message/parselink"

DEFAULT_GROUP_LINK_DESC = "Bấm vào đây để tham gia nhóm trên Zalo"
# Media mô tả thumbnail card, giá trị theo request thật của Zalo Web.
DEFAULT_MEDIA = {
    "type": 0,
    "count": 0,
    "mediaTitle": "",
    "artist": "",
    "streamUrl": "",
    "stream_icon": "",
    "tWidth": 486,
    "tHeight": 256,
    "tType": 1,
}


def _cookies_str_to_dict(cookies: str) -> dict:
    cookie_dict = {}
    for item in (cookies or "").split(";"):
        item = item.strip()
        if "=" in item:
            key, value = item.split("=", 1)
            cookie_dict[key.strip()] = value.strip()
    return cookie_dict


def _decode_data_field(resp_json: dict, zpw_enk: str):
    data_field = resp_json.get("data", "")
    if isinstance(data_field, str) and data_field:
        try:
            decoded = zalo_decode(data_field, zpw_enk)
        except Exception:
            return None
        if isinstance(decoded, str):
            try:
                return json.loads(decoded)
            except Exception:
                return None
        return decoded
    return data_field


def extract_zalo_group_link(text: str) -> str:
    """Trả link nhóm Zalo đầu tiên trong nội dung, "" nếu không có."""
    m = GROUP_LINK_RE.search(str(text or ""))
    return m.group(0) if m else ""


def parse_link_info(link: str, zpw_enk: str, cookies: str, imei: str,
                    zpw_ver: str = None, timeout: int = 15) -> dict:
    """Gọi /api/message/parselink (như Zalo Web) để lấy metadata link.

    Response chứa sẵn mọi field cho sendlink: thumb dạng photolinkv2 có chữ ký
    server, title, desc, src, href, media.
    """
    zpw_ver = get_zpw_ver(zpw_ver)
    payload = {"link": link, "version": 1, "imei": str(imei or "")}
    enc_params = zalo_encode(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        zpw_enk,
        url_encode=False,
    )
    response = requests.get(
        PARSELINK_URL,
        params={"zpw_ver": zpw_ver, "zpw_type": "30", "params": enc_params},
        headers=zalo_mobile_headers(),
        cookies=_cookies_str_to_dict(cookies),
        timeout=timeout,
    )
    if response.status_code != 200:
        return {"ok": False, "message": f"parselink HTTP {response.status_code}"}
    decoded = _decode_data_field(response.json(), zpw_enk)
    if not isinstance(decoded, dict):
        return {"ok": False, "message": "parselink không giải mã được response"}
    if decoded.get("error_code", 0) not in (0, None):
        return {"ok": False, "message": f"parselink error_code={decoded.get('error_code')}"}
    # Cấu trúc: decoded.data.data = {thumb, title, desc, src, href, media}
    outer = decoded.get("data") or {}
    inner = outer.get("data") if isinstance(outer, dict) else None
    if not isinstance(inner, dict) or not inner:
        return {"ok": False, "message": "parselink không có data"}
    media = inner.get("media")
    if isinstance(media, dict):
        # Bổ sung kích thước thumbnail card như request thật của web.
        media = {**media, "tWidth": 486, "tHeight": 256, "tType": 1}
    return {
        "ok": True,
        "title": str(inner.get("title") or "").strip(),
        "desc": str(inner.get("desc") or "").strip(),
        "thumb": str(inner.get("thumb") or "").strip(),
        "src": str(inner.get("src") or "zalo.me").strip(),
        "href": str(inner.get("href") or link).strip(),
        "media": media,
    }


def _fetch_link_og_tags(link: str, cookies: str, timeout: int = 10) -> dict:
    """Lấy og:title / og:image / og:description của trang link nhóm.

    Phải gửi kèm cookie account Zalo: không có phiên thì zalo.me/g/... redirect
    sang trang login và og:image chỉ là ảnh nền login. og:image thật có dạng
    https://qr.zalo.me/sl/2/<id> — đúng nguồn Zalo Web dùng làm thumb card.
    """
    tags = {}
    try:
        response = requests.get(
            link,
            headers={"User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
            )},
            cookies=_cookies_str_to_dict(cookies),
            timeout=timeout,
            allow_redirects=True,
        )
        if response.status_code != 200:
            return tags
        html = response.text
        for prop in ("og:title", "og:image", "og:description"):
            m = re.search(r'<meta[^>]+property=["\']' + prop + r'["\'][^>]+content=["\']([^"\']+)', html, re.I)
            if not m:
                m = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']' + prop + r'["\']', html, re.I)
            if m:
                tags[prop] = m.group(1).strip()
    except Exception as exc:
        print(f"[send_link] Không lấy được og tags từ {link}: {exc}", flush=True)
    return tags


def resolve_group_link_info(link: str, zpw_enk: str, cookies: str, zpw_ver: str = None,
                            imei: str = "") -> dict:
    """Bước 1: lấy thông tin link nhóm để dựng link card.

    Nguồn chính: /api/message/parselink (đúng như Zalo Web) — trả nguyên bộ
    thumb photolinkv2 + title + desc + src + media.
    Fallback: /api/group/link/ginfo (tên nhóm, ảnh nhóm) + og tags của trang link.
    """
    try:
        parsed = parse_link_info(link, zpw_enk, cookies, imei, zpw_ver=zpw_ver)
        if parsed.get("ok") and parsed.get("title"):
            return parsed
        print(f"[send_link] parselink không đủ dữ liệu ({parsed.get('message', 'thiếu title')}), fallback ginfo", flush=True)
    except Exception as exc:
        print(f"[send_link] parselink lỗi: {exc}. Fallback ginfo", flush=True)

    from features.groups.get_group import resolve_group_id_from_url
    result = resolve_group_id_from_url(link, zpw_enk, cookies, zpw_ver=zpw_ver)
    if not result.get("ok"):
        return {"ok": False, "message": result.get("message", "Không lấy được thông tin nhóm từ link")}
    info = result.get("group_info") or {}
    title = str(info.get("name") or info.get("gridName") or "").strip()
    avatar = str(info.get("fullAvt") or info.get("avt") or info.get("avatar") or "").strip()

    og = _fetch_link_og_tags(link, cookies)
    thumb = str(og.get("og:image") or "").strip() or avatar
    if not title:
        title = str(og.get("og:title") or "").strip()
    desc = str(og.get("og:description") or "").strip() or DEFAULT_GROUP_LINK_DESC

    return {
        "ok": True,
        "groupId": result.get("group_id", ""),
        "title": title,
        "thumb": thumb,
        "desc": desc,
        "src": "zalo.me",
        "href": link,
        "media": None,
        "raw": info,
    }


def send_link_message(
    to_id: str,
    message: str,
    href: str,
    title: str,
    thumb: str,
    zpw_enk: str,
    cookies: str,
    imei: str,
    is_group: bool = False,
    desc: str = DEFAULT_GROUP_LINK_DESC,
    src: str = "zalo.me",
    media: dict = None,
    zpw_ver: str = None,
    timeout: int = 30,
) -> Tuple[dict, Optional[dict]]:
    """Bước 2: gửi tin dạng link card (sendlink nhóm / message/link 1-1)."""
    zpw_ver = get_zpw_ver(zpw_ver)
    client_id = int(time.time() * 1000)
    payload = {
        "msg": message,
        "href": href,
        "src": src or "zalo.me",
        "title": title or href,
        "desc": desc,
        "language": "vi",
        "thumb": thumb or "",
        "type": 0,
        "media": json.dumps(media if isinstance(media, dict) and media else DEFAULT_MEDIA,
                            ensure_ascii=False, separators=(",", ":")),
        "ttl": 0,
        "clientId": client_id,
    }
    if is_group:
        url = GROUP_SENDLINK_URL
        payload["grid"] = str(to_id)
        payload["mentionInfo"] = "[]"
        payload["imei"] = str(imei or "")
    else:
        url = MESSAGE_LINK_URL
        payload["toId"] = str(to_id)
        payload["mentionInfo"] = ""
        payload["imei"] = str(imei or "")

    enc_params = zalo_encode(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        zpw_enk,
        url_encode=False,
    )
    response = requests.post(
        url,
        params={"zpw_ver": zpw_ver, "zpw_type": "30", "nretry": "0"},
        data={"params": enc_params},
        headers=zalo_mobile_headers(),
        cookies=_cookies_str_to_dict(cookies),
        timeout=timeout,
    )
    try:
        resp_json = response.json()
    except Exception as exc:
        raise RuntimeError(f"Response sendlink không phải JSON: {response.text[:300]}") from exc
    decoded = _decode_data_field(resp_json, zpw_enk)
    return resp_json, decoded


def send_message_smart(
    to_id: str,
    message: str,
    zpw_enk: str,
    cookies: str,
    imei: str,
    is_group: bool = False,
    zpw_ver: str = None,
    link_info: dict = None,
) -> dict:
    """Gửi tin nhắn thông minh: nội dung chứa link nhóm Zalo -> gửi link card
    (lấy thông tin nhóm trước); không có link hoặc lỗi -> gửi text thường.

    link_info: kết quả resolve_group_link_info đã cache (dùng cho worker gửi
    hàng loạt cùng một nội dung, tránh gọi ginfo lặp lại từng người nhận).

    Trả: {ok, sentAsLink, error, decoded}
    """
    message = str(message or "")
    link = extract_zalo_group_link(message)

    if link:
        try:
            info = link_info if isinstance(link_info, dict) and link_info.get("ok") else resolve_group_link_info(
                link, zpw_enk, cookies, zpw_ver=zpw_ver, imei=imei
            )
            if info.get("ok"):
                _, decoded = send_link_message(
                    to_id, message, info.get("href") or link,
                    info.get("title", ""), info.get("thumb", ""),
                    zpw_enk, cookies, imei,
                    is_group=is_group,
                    desc=info.get("desc") or DEFAULT_GROUP_LINK_DESC,
                    src=info.get("src") or "zalo.me",
                    media=info.get("media"),
                    zpw_ver=zpw_ver,
                )
                code = decoded.get("error_code", -1) if isinstance(decoded, dict) else -1
                if code == 0:
                    return {"ok": True, "sentAsLink": True, "error": "", "decoded": decoded}
                print(f"[send_link] sendlink lỗi error_code={code}, fallback gửi text thường", flush=True)
            else:
                print(f"[send_link] Không lấy được info nhóm từ link ({info.get('message')}), fallback text", flush=True)
        except Exception as exc:
            print(f"[send_link] Lỗi gửi link card: {exc}. Fallback gửi text thường", flush=True)

    # Text thường (hoặc fallback khi link card lỗi).
    if is_group:
        from features.groups.send_sms_group import send_group_msg
        _, decoded = send_group_msg(cookies, zpw_enk, to_id, imei, message, zpw_ver=zpw_ver)
    else:
        from features.messaging.send_sms import send_sms
        _, decoded = send_sms(cookies, zpw_enk, to_id, imei, message, zpw_ver=zpw_ver)
    code = decoded.get("error_code", -1) if isinstance(decoded, dict) else -1
    return {
        "ok": code == 0,
        "sentAsLink": False,
        "error": "" if code == 0 else f"Error code: {code}",
        "decoded": decoded,
    }
