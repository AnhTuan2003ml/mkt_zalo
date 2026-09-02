# -*- coding: utf-8 -*-
"""Gửi ảnh qua Zalo Web API theo luồng 2 bước đã phân tích từ bundle
test/js/1.7a40934bc92f49b7036d.js:

Bước 1 — upload byte ảnh theo chunk:
    POST https://tt-files-wpa.chat.zalo.me/api/message/photo_original/upload
         ?zpw_ver=..&zpw_type=30&params=<AES(metadata)>&type=2
    (nhóm: /api/group/photo_original/upload, type=11)
    - Mỗi chunk là 1 request multipart với field "chunkContent".
    - metadata: {totalChunk, fileName, clientId, totalSize, imei, chunkId, toid|grid}
      (bundle tạo checksum rồi `delete y.checksum` trước khi gửi).
    - Chunk cuối trả về: {photoId, thumbUrl, normalUrl, hdUrl, finished}.

Bước 2 — tạo tin nhắn chứa ảnh (sendMsgPhotoAsync trong bundle):
    POST https://tt-files-wpa.chat.zalo.me/api/message/photo_original/send
         ?zpw_ver=..&zpw_type=30&nretry=0
    body: params=<AES(JSON payload)> với photoId + các URL do bước 1 trả về.
"""
import hashlib
import io
import json
import time
from typing import Optional, Tuple

import requests

from core.zalo.enc import zalo_encode
from core.zalo.dec import zalo_decode
from core.zalo.zalo_config import get_zpw_ver
from core.zalo.zalo_headers import zalo_mobile_headers

FILE_DOMAIN = "https://tt-files-wpa.chat.zalo.me"
# Zalo Web chia chunk theo cấu hình; 1MB là mức an toàn cho ảnh.
CHUNK_SIZE = 1024 * 1024


def _cookies_str_to_dict(cookies: str) -> dict:
    cookie_dict = {}
    for item in (cookies or "").split(";"):
        item = item.strip()
        if "=" in item:
            key, value = item.split("=", 1)
            cookie_dict[key.strip()] = value.strip()
    return cookie_dict


def _as_json_obj(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return None
    return value


def _decode_data_field(resp_json: dict, zpw_enk: str):
    """Field `data` trong response Zalo có thể là chuỗi đã mã hóa AES."""
    data_field = resp_json.get("data", "")
    if isinstance(data_field, str) and data_field:
        try:
            decoded = zalo_decode(data_field, zpw_enk)
        except Exception:
            return None
        return _as_json_obj(decoded)
    return _as_json_obj(data_field)


def get_image_info(image_bytes: bytes) -> Tuple[int, int, str]:
    """Trả (width, height, format) của ảnh; raise ValueError nếu không phải ảnh."""
    from PIL import Image
    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            return int(img.width), int(img.height), (img.format or "").lower()
    except Exception as exc:
        raise ValueError(f"File không phải ảnh hợp lệ: {exc}") from exc


def upload_photo(
    image_bytes: bytes,
    file_name: str,
    to_id: str,
    zpw_enk: str,
    cookies: str,
    imei: str,
    is_group: bool = False,
    zpw_ver: str = None,
    timeout: int = 60,
) -> dict:
    """Upload ảnh theo chunk, trả {photoId, thumbUrl, normalUrl, hdUrl}."""
    zpw_ver = get_zpw_ver(zpw_ver)
    to_id = str(to_id or "").strip()
    if not image_bytes:
        return {"ok": False, "message": "Thiếu dữ liệu ảnh"}
    if not to_id:
        return {"ok": False, "message": "Thiếu người/nhóm nhận"}

    client_id = int(time.time() * 1000)
    total_size = len(image_bytes)
    chunks = [image_bytes[i:i + CHUNK_SIZE] for i in range(0, total_size, CHUNK_SIZE)]
    total_chunk = len(chunks)

    if is_group:
        upload_url = FILE_DOMAIN + "/api/group/photo_original/upload"
        url_type = "11"
    else:
        upload_url = FILE_DOMAIN + "/api/message/photo_original/upload"
        url_type = "2"

    # Upload là multipart: PHẢI bỏ content-type cố định (x-www-form-urlencoded)
    # trong zalo_mobile_headers để requests tự set multipart/form-data; boundary=...
    # Nếu không, server không parse được chunkContent -> error_code 115.
    headers = zalo_mobile_headers()
    for key in list(headers.keys()):
        if key.lower() == "content-type":
            headers.pop(key)
    cookie_dict = _cookies_str_to_dict(cookies)

    result = {}
    for index, chunk in enumerate(chunks, start=1):
        # Metadata giống bundle: totalChunk/fileName/clientId/totalSize/imei/chunkId
        # + toid hoặc grid (checksum bị bundle xóa trước khi gửi).
        metadata = {
            "totalChunk": total_chunk,
            "fileName": file_name,
            "clientId": client_id,
            "totalSize": total_size,
            "imei": str(imei or ""),
            "chunkId": index,
        }
        if is_group:
            metadata["grid"] = to_id
        else:
            metadata["toid"] = to_id

        enc_params = zalo_encode(
            json.dumps(metadata, ensure_ascii=False, separators=(",", ":")),
            zpw_enk,
            url_encode=False,
        )
        files = {"chunkContent": (file_name, chunk, "application/octet-stream")}
        response = requests.post(
            upload_url,
            params={
                "zpw_ver": zpw_ver,
                "zpw_type": "30",
                "params": enc_params,
                "type": url_type,
            },
            files=files,
            headers=headers,
            cookies=cookie_dict,
            timeout=timeout,
        )
        if response.status_code != 200:
            return {"ok": False, "message": f"Upload ảnh lỗi HTTP {response.status_code}", "clientId": client_id}

        resp_json = response.json()
        error_code = resp_json.get("error_code", 0)
        data = _decode_data_field(resp_json, zpw_enk) or {}
        # Payload giải mã có thể lồng thêm 1 lớp:
        # {"error_code":0,"error_message":"Successful.","data":{photoId, ...}}
        # -> lấy error_code trong lớp giải mã (nếu có) rồi unwrap về object chứa photoId.
        if isinstance(data, dict) and "error_code" in data and "photoId" not in data:
            inner_code = data.get("error_code")
            if inner_code not in (0, 1, None):
                msg = data.get("error_message") or f"error_code={inner_code}"
                return {"ok": False, "message": f"Upload ảnh bị từ chối: {msg}", "clientId": client_id}
            inner = _as_json_obj(data.get("data"))
            if isinstance(inner, dict):
                data = inner
        # error_code=0 + finished: xong; error_code=1 + photoId: đã nhận nhưng
        # URL có thể về chậm (bundle chờ event async) -> vẫn nhận nếu đủ URL.
        if error_code not in (0, 1, None):
            msg = resp_json.get("error_message") or f"error_code={error_code}"
            return {"ok": False, "message": f"Upload ảnh bị từ chối: {msg}", "clientId": client_id}
        if isinstance(data, dict) and data:
            result.update({k: v for k, v in data.items() if v not in (None, "")})

    photo_id = result.get("photoId") or result.get("photo_id")
    normal_url = result.get("normalUrl", "")
    thumb_url = result.get("thumbUrl", "")
    hd_url = result.get("hdUrl", "")
    # Bundle coi hợp lệ khi: photoId && hdUrl, hoặc photoId && normalUrl && thumbUrl.
    if not photo_id or not (hd_url or (normal_url and thumb_url)):
        # URL trả chậm qua kênh async: thử poll lại bằng cách gửi lại chunk cuối
        # không khả thi phía server -> báo lỗi rõ ràng.
        return {
            "ok": False,
            "message": f"Upload xong nhưng thiếu photoId/URL. Response: {json.dumps(result, ensure_ascii=False)[:300]}",
            "clientId": client_id,
        }

    return {
        "ok": True,
        "photoId": str(photo_id),
        "clientId": client_id,
        "thumbUrl": thumb_url or normal_url or hd_url,
        "normalUrl": normal_url or hd_url,
        "hdUrl": hd_url or normal_url,
        "rawUrl": normal_url or hd_url,
        "message": "Upload ảnh thành công",
    }


def send_photo_message(
    upload_info: dict,
    to_id: str,
    zpw_enk: str,
    cookies: str,
    imei: str,
    desc: str = "",
    width: int = 0,
    height: int = 0,
    checksum: str = "",
    is_group: bool = False,
    zpw_ver: str = None,
    timeout: int = 30,
) -> Tuple[dict, Optional[dict]]:
    """Bước 2: POST photo_original/send với photoId + URL từ bước upload."""
    zpw_ver = get_zpw_ver(zpw_ver)
    payload = {
        "photoId": str(upload_info.get("photoId", "")),
        "clientId": upload_info.get("clientId") or int(time.time() * 1000),
        "desc": desc or "",
        "width": int(width or 0),
        "height": int(height or 0),
        "previewThumb": "",
        "rawUrl": upload_info.get("rawUrl", ""),
        # oriUrl BẮT BUỘC với /api/group/photo_original/send (thiếu -> error 112);
        # gửi 1-1 không cần nhưng thêm vào không ảnh hưởng.
        "oriUrl": upload_info.get("normalUrl", "") or upload_info.get("hdUrl", ""),
        "thumbUrl": upload_info.get("thumbUrl", ""),
        "normalUrl": upload_info.get("normalUrl", ""),
        "hdUrl": upload_info.get("hdUrl", ""),
        "language": "vi",
        "zsource": 301,
        "jcp": json.dumps({"sendSource": 301}, separators=(",", ":")),
        "ttl": 0,
        "imei": str(imei or ""),
    }
    if checksum:
        payload["checksum"] = checksum
    if is_group:
        send_url = FILE_DOMAIN + "/api/group/photo_original/send"
        payload["grid"] = str(to_id)
        # Bundle luôn gắn visibility cùng grid cho mọi API gửi nhóm
        # (sendZText/_sendPhoto/sendPhotoByUrl); thiếu -> error_code 112.
        payload["visibility"] = 0
    else:
        send_url = FILE_DOMAIN + "/api/message/photo_original/send"
        payload["toid"] = str(to_id)

    enc_params = zalo_encode(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        zpw_enk,
        url_encode=False,
    )
    response = requests.post(
        send_url,
        params={"zpw_ver": zpw_ver, "zpw_type": "30", "nretry": "0"},
        data={"params": enc_params},
        headers=zalo_mobile_headers(),
        cookies=_cookies_str_to_dict(cookies),
        timeout=timeout,
    )
    try:
        resp_json = response.json()
    except Exception as exc:
        raise RuntimeError(f"Response photo_original/send không phải JSON: {response.text[:300]}") from exc
    decoded = _decode_data_field(resp_json, zpw_enk)
    return resp_json, decoded


def send_photo(
    image_bytes: bytes,
    file_name: str,
    to_id: str,
    zpw_enk: str,
    cookies: str,
    imei: str,
    desc: str = "",
    is_group: bool = False,
    zpw_ver: str = None,
) -> dict:
    """Luồng đầy đủ: kiểm tra ảnh -> upload chunk -> photo_original/send."""
    try:
        width, height, _fmt = get_image_info(image_bytes)
    except ValueError as exc:
        return {"ok": False, "message": str(exc)}

    upload_info = upload_photo(
        image_bytes, file_name, to_id, zpw_enk, cookies, imei,
        is_group=is_group, zpw_ver=zpw_ver,
    )
    if not upload_info.get("ok"):
        return {"ok": False, "message": upload_info.get("message", "Upload ảnh thất bại"), "step": "upload"}

    checksum = hashlib.md5(image_bytes).hexdigest()
    resp_json, decoded = send_photo_message(
        upload_info, to_id, zpw_enk, cookies, imei,
        desc=desc, width=width, height=height, checksum=checksum,
        is_group=is_group, zpw_ver=zpw_ver,
    )

    error_code = None
    error_message = ""
    if isinstance(decoded, dict):
        error_code = decoded.get("error_code", decoded.get("errorCode"))
        error_message = str(decoded.get("error_message", decoded.get("errorMessage", "")) or "")
    if error_code is None and isinstance(resp_json, dict):
        error_code = resp_json.get("error_code", resp_json.get("errorCode"))
        error_message = str(resp_json.get("error_message", resp_json.get("errorMessage", "")) or "")

    ok = str(error_code) in ("0", "None") or (isinstance(decoded, dict) and decoded.get("msgId"))
    return {
        "ok": bool(ok),
        "message": "Đã gửi ảnh" if ok else (error_message or f"Gửi ảnh lỗi error_code={error_code}"),
        "step": "send",
        "photoId": upload_info.get("photoId"),
        "urls": {
            "thumbUrl": upload_info.get("thumbUrl"),
            "normalUrl": upload_info.get("normalUrl"),
            "hdUrl": upload_info.get("hdUrl"),
        },
        "response": resp_json,
        "decoded": decoded,
    }
