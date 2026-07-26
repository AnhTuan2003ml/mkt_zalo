"""API và bộ xử lý dùng chung để mời thành viên vào nhóm Zalo.

Điểm quan trọng:
- Zalo có thể trả ``errorMembers`` cho các trường hợp không phải lỗi thật.
- Mã 263 nghĩa là lời mời đã được gửi vào hộp tin nhắn chờ.
- Mã 262 nghĩa là lời mời đã tồn tại trong hộp tin nhắn chờ.
- Cả trang Mời vào nhóm và Sao chép nhóm phải dùng chung bộ phân loại này.
"""

from __future__ import annotations

import json
import re
import sys
import time
from typing import Any, Iterable

import requests

from core.zalo.dec import zalo_decode
from core.zalo.enc import zalo_encode
from core.zalo.zalo_config import get_zpw_ver
from core.zalo.zalo_headers import zalo_mobile_headers


def _safe_reconfigure() -> None:
    try:
        if sys.stdout and hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


_safe_reconfigure()

# Các mã kết quả thành viên được Zalo Web xử lý như trạng thái đã gửi/chờ xử lý,
# không phải lỗi cứng.
INVITE_CODE_MEMBER_ALREADY = 178
INVITE_CODE_WAITING_APPROVAL = 240
INVITE_CODE_EXISTED_WAITING_APPROVAL = 252
INVITE_CODE_ALREADY_INV_BOX = 262
INVITE_CODE_SENT_INV_BOX = 263

INVITE_ACCEPTED_CODES = {
    INVITE_CODE_WAITING_APPROVAL,
    INVITE_CODE_EXISTED_WAITING_APPROVAL,
    INVITE_CODE_ALREADY_INV_BOX,
    INVITE_CODE_SENT_INV_BOX,
}
INVITE_JOINED_CODES = {INVITE_CODE_MEMBER_ALREADY}


def parse_grid_input(value: str) -> str:
    """Trích xuất Group ID từ link ``zalo.me/g/...`` hoặc giữ nguyên ID."""
    value = str(value or "").strip()
    if value.startswith("http"):
        match = re.search(r"/g/(\d+)", value)
        if match:
            return match.group(1)
    if re.fullmatch(r"\d+", value):
        return value
    return value


def _cookie_dict(cookies: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in str(cookies or "").split(";"):
        item = item.strip()
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        result[key.strip()] = value.strip()
    return result


def _decoded_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {"raw": value}
        except Exception:
            return {"raw": value}
    return {}


def _to_int(value: Any, default: int = -1) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _extract_uid(item: Any) -> str:
    if isinstance(item, dict):
        return str(
            item.get("userId")
            or item.get("uid")
            or item.get("id")
            or item.get("memberId")
            or item.get("toUid")
            or ""
        ).strip()
    return str(item or "").strip()


def _iter_items(value: Any) -> list[Any]:
    if value is None or value == "":
        return []
    return value if isinstance(value, list) else [value]


def _extract_success_member_ids(response_json: dict, decoded: dict) -> set[str]:
    result: set[str] = set()
    for root in (decoded, response_json):
        if not isinstance(root, dict):
            continue
        data = root.get("data") if isinstance(root.get("data"), dict) else {}
        error_data = data.get("error_data") if isinstance(data.get("error_data"), dict) else {}
        root_error_data = root.get("error_data") if isinstance(root.get("error_data"), dict) else {}
        for raw in (
            data.get("successMembers"),
            root.get("successMembers"),
            data.get("success_members"),
            root.get("success_members"),
            error_data.get(0),
            error_data.get("0"),
            root_error_data.get(0),
            root_error_data.get("0"),
            data.get(0),
            data.get("0"),
            root.get(0),
            root.get("0"),
        ):
            for item in _iter_items(raw):
                uid = _extract_uid(item)
                if uid:
                    result.add(uid)
    return result


def _extract_error_entries(response_json: dict, decoded: dict) -> dict[str, dict]:
    """Chuẩn hóa nhiều dạng ``errorMembers`` thành map UID -> mã/thông báo.

    Zalo có thể trả một trong các dạng:
    - ``{"263": ["uid1", "uid2"]}``
    - ``[{"userId": "uid", "errorCode": 263}]``
    - ``["uid1"]`` kèm mã lỗi chung
    """
    entries: dict[str, dict] = {}

    global_code = _to_int(
        decoded.get("error_code", response_json.get("error_code", -1)),
        -1,
    )
    global_message = _first_text(
        decoded.get("error_message"),
        decoded.get("message"),
        response_json.get("error_message"),
        response_json.get("message"),
    )

    candidates: list[Any] = []
    for root in (decoded, response_json):
        if not isinstance(root, dict):
            continue
        data = root.get("data") if isinstance(root.get("data"), dict) else {}
        candidates.extend([
            data.get("errorMembers"),
            root.get("errorMembers"),
            data.get("error_members"),
            root.get("error_members"),
            # Phản hồi thật của Zalo Web thường đặt map mã lỗi -> UID ở đây.
            data.get("error_data"),
            root.get("error_data"),
        ])

        # Một số phiên bản trả trực tiếp các khóa số như {"263": [uid]}.
        numeric_root = {
            key: value
            for key, value in root.items()
            if str(key).lstrip("-").isdigit()
        }
        numeric_data = {
            key: value
            for key, value in data.items()
            if str(key).lstrip("-").isdigit()
        }
        if numeric_root:
            candidates.append(numeric_root)
        if numeric_data:
            candidates.append(numeric_data)

    for raw in candidates:
        if not raw:
            continue

        # Dạng map mã lỗi -> danh sách UID.
        if isinstance(raw, dict) and not _extract_uid(raw):
            for raw_code, raw_members in raw.items():
                code = _to_int(raw_code, global_code)
                # Mã 0 trong error_data là danh sách thành viên thành công.
                if code == 0:
                    continue
                for item in _iter_items(raw_members):
                    uid = _extract_uid(item)
                    if not uid:
                        continue
                    item_code = code
                    item_message = global_message
                    if isinstance(item, dict):
                        item_code = _to_int(
                            item.get("errorCode", item.get("error_code", item.get("code", code))),
                            code,
                        )
                        item_message = _first_text(
                            item.get("errorMessage"),
                            item.get("error_message"),
                            item.get("message"),
                            global_message,
                        )
                    entries[uid] = {"code": item_code, "message": item_message}
            continue

        for item in _iter_items(raw):
            uid = _extract_uid(item)
            if not uid:
                continue
            code = global_code
            message = global_message
            if isinstance(item, dict):
                code = _to_int(
                    item.get("errorCode", item.get("error_code", item.get("code", global_code))),
                    global_code,
                )
                message = _first_text(
                    item.get("errorMessage"),
                    item.get("error_message"),
                    item.get("message"),
                    global_message,
                )
            entries[uid] = {"code": code, "message": message}

    return entries


def _result_message(code: int, fallback: str = "") -> tuple[str, str]:
    """Trả về ``(delivery, message)`` cho từng mã Zalo."""
    if str(fallback or "").strip().lower().rstrip(".") in {"successful", "success"}:
        fallback = ""
    if code == INVITE_CODE_SENT_INV_BOX:
        return "pending_inbox", "Đã gửi lời mời vào tin nhắn chờ của người nhận."
    if code == INVITE_CODE_ALREADY_INV_BOX:
        return "pending_inbox", "Lời mời đã tồn tại trong tin nhắn chờ của người nhận."
    if code == INVITE_CODE_WAITING_APPROVAL:
        return "pending_approval", "Đã gửi yêu cầu; đang chờ quản trị viên nhóm duyệt."
    if code == INVITE_CODE_EXISTED_WAITING_APPROVAL:
        return "pending_approval", "Yêu cầu đã tồn tại; đang chờ quản trị viên nhóm duyệt."
    if code == INVITE_CODE_MEMBER_ALREADY:
        return "already_joined", "Thành viên đã có trong nhóm đích."
    return "failed", fallback or (f"Zalo từ chối lời mời, mã {code}." if code >= 0 else "Zalo từ chối lời mời.")


def classify_invite_response(
    member_ids: Iterable[str],
    response_json: Any,
    decoded_data: Any,
) -> dict:
    """Phân loại kết quả từng UID từ phản hồi mời nhóm của Zalo."""
    response_json = response_json if isinstance(response_json, dict) else {}
    decoded = _decoded_dict(decoded_data)
    clean_ids: list[str] = []
    seen: set[str] = set()
    for raw_uid in member_ids or []:
        uid = str(raw_uid or "").strip()
        if uid and uid not in seen:
            seen.add(uid)
            clean_ids.append(uid)

    global_code = _to_int(
        decoded.get("error_code", response_json.get("error_code", -1)),
        -1,
    )
    global_message = _first_text(
        decoded.get("error_message"),
        decoded.get("message"),
        response_json.get("error_message"),
        response_json.get("message"),
    )
    success_ids = _extract_success_member_ids(response_json, decoded)
    error_entries = _extract_error_entries(response_json, decoded)

    members: list[dict] = []
    accepted_count = 0
    invited_count = 0
    joined_count = 0
    failed_count = 0
    inbox_count = 0

    for uid in clean_ids:
        error = error_entries.get(uid)
        if error:
            code = _to_int(error.get("code"), global_code)
            fallback = _first_text(error.get("message"), global_message)
            if code in INVITE_JOINED_CODES:
                delivery, message = _result_message(code, fallback)
                status = "joined"
                accepted_count += 1
                joined_count += 1
            elif code in INVITE_ACCEPTED_CODES:
                delivery, message = _result_message(code, fallback)
                status = "invited"
                accepted_count += 1
                invited_count += 1
                if delivery == "pending_inbox":
                    inbox_count += 1
            else:
                delivery, message = _result_message(code, fallback)
                status = "failed"
                failed_count += 1
        elif global_code == 0 or uid in success_ids:
            code = 0
            delivery = "direct"
            status = "invited"
            message = "Zalo đã nhận yêu cầu mời; đang kiểm tra trạng thái vào nhóm."
            accepted_count += 1
            invited_count += 1
        else:
            code = global_code
            delivery = "failed"
            status = "failed"
            message = global_message or f"Zalo trả về mã lỗi {global_code}."
            failed_count += 1

        members.append({
            "userId": uid,
            "status": status,
            "code": code,
            "delivery": delivery,
            "message": message,
        })

    return {
        "success": failed_count == 0,
        "partial": accepted_count > 0 and failed_count > 0,
        "errorCode": global_code,
        "errorMessage": global_message,
        "total": len(clean_ids),
        "acceptedCount": accepted_count,
        "invitedCount": invited_count,
        "joinedCount": joined_count,
        "failedCount": failed_count,
        "inboxCount": inbox_count,
        "members": members,
        "response": response_json,
        "decoded": decoded,
    }


def invite_group(
    grid: str,
    members: list,
    imei: str,
    zpw_enk: str,
    cookies: str,
    zpw_ver: str | None = None,
) -> tuple:
    """Gọi trực tiếp API mời thành viên vào nhóm Zalo."""
    grid = parse_grid_input(grid)
    clean_members = [str(uid or "").strip() for uid in members or [] if str(uid or "").strip()]

    payload = {
        "grid": grid,
        "members": clean_members,
        "memberTypes": [-1] * len(clean_members),
        "imei": imei,
        "clientLang": "vi",
    }
    plaintext = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    params = zalo_encode(plaintext, zpw_enk, url_encode=True)

    response = requests.post(
        "https://tt-group-wpa.chat.zalo.me/api/group/invite/v2",
        params={"zpw_ver": get_zpw_ver(zpw_ver), "zpw_type": "30"},
        data=f"params={params}",
        headers=zalo_mobile_headers(),
        cookies=_cookie_dict(cookies),
        timeout=30,
    )
    response.raise_for_status()
    response_json = response.json()
    data_field = response_json.get("data", "")
    if isinstance(data_field, str) and data_field:
        decoded_data = zalo_decode(data_field, zpw_enk)
    else:
        decoded_data = data_field
    return response_json, decoded_data


def _chunks(items: list[str], size: int):
    for index in range(0, len(items), size):
        yield items[index:index + size]


def invite_members_to_group(
    grid: str,
    members: Iterable[str],
    imei: str,
    zpw_enk: str,
    cookies: str,
    *,
    zpw_ver: str | None = None,
    batch_size: int = 50,
    retry_global_failure_individually: bool = True,
) -> dict:
    """Mời theo batch và trả về kết quả chuẩn hóa dùng chung cho mọi màn hình."""
    clean_members: list[str] = []
    seen: set[str] = set()
    for raw_uid in members or []:
        uid = str(raw_uid or "").strip()
        if uid and uid not in seen:
            seen.add(uid)
            clean_members.append(uid)

    batch_size = max(1, min(_to_int(batch_size, 50), 100))
    all_results: dict[str, dict] = {}
    batches: list[dict] = []

    for batch_no, member_batch in enumerate(_chunks(clean_members, batch_size), start=1):
        response_json, decoded = invite_group(
            grid,
            member_batch,
            imei,
            zpw_enk,
            cookies,
            zpw_ver=zpw_ver,
        )
        parsed = classify_invite_response(member_batch, response_json, decoded)

        # Nếu cả batch bị lỗi ở cấp yêu cầu, thử từng UID một để lấy kết quả chính
        # xác và tránh việc một UID lỗi làm hỏng toàn bộ batch.
        if (
            retry_global_failure_individually
            and parsed.get("acceptedCount", 0) == 0
            and parsed.get("failedCount", 0) == len(member_batch)
            and len(member_batch) > 1
        ):
            individual_members: list[dict] = []
            for uid in member_batch:
                one_response, one_decoded = invite_group(
                    grid,
                    [uid],
                    imei,
                    zpw_enk,
                    cookies,
                    zpw_ver=zpw_ver,
                )
                one_parsed = classify_invite_response([uid], one_response, one_decoded)
                individual_members.extend(one_parsed.get("members") or [])
            parsed = {
                "success": all(item.get("status") != "failed" for item in individual_members),
                "partial": any(item.get("status") != "failed" for item in individual_members)
                and any(item.get("status") == "failed" for item in individual_members),
                "errorCode": parsed.get("errorCode", -1),
                "errorMessage": parsed.get("errorMessage", ""),
                "total": len(individual_members),
                "acceptedCount": sum(1 for item in individual_members if item.get("status") in {"invited", "joined"}),
                "invitedCount": sum(1 for item in individual_members if item.get("status") == "invited"),
                "joinedCount": sum(1 for item in individual_members if item.get("status") == "joined"),
                "failedCount": sum(1 for item in individual_members if item.get("status") == "failed"),
                "inboxCount": sum(1 for item in individual_members if item.get("delivery") == "pending_inbox"),
                "members": individual_members,
                "response": response_json,
                "decoded": _decoded_dict(decoded),
            }

        for item in parsed.get("members") or []:
            all_results[str(item.get("userId") or "")] = item
        batches.append({
            "batch": batch_no,
            "total": len(member_batch),
            "success": bool(parsed.get("success")),
            "partial": bool(parsed.get("partial")),
            "accepted": int(parsed.get("acceptedCount") or 0),
            "invited": int(parsed.get("invitedCount") or 0),
            "joined": int(parsed.get("joinedCount") or 0),
            "failed": int(parsed.get("failedCount") or 0),
            "inbox": int(parsed.get("inboxCount") or 0),
            "errorCode": parsed.get("errorCode", -1),
            "errorMessage": parsed.get("errorMessage", ""),
            "members": parsed.get("members") or [],
        })

    ordered_results = [all_results[uid] for uid in clean_members if uid in all_results]
    accepted_count = sum(1 for item in ordered_results if item.get("status") in {"invited", "joined"})
    invited_count = sum(1 for item in ordered_results if item.get("status") == "invited")
    joined_count = sum(1 for item in ordered_results if item.get("status") == "joined")
    failed_count = sum(1 for item in ordered_results if item.get("status") == "failed")
    inbox_count = sum(1 for item in ordered_results if item.get("delivery") == "pending_inbox")

    return {
        "success": failed_count == 0,
        "partial": accepted_count > 0 and failed_count > 0,
        "groupId": parse_grid_input(grid),
        "total": len(clean_members),
        "acceptedCount": accepted_count,
        "invitedCount": invited_count,
        "joinedCount": joined_count,
        "failedCount": failed_count,
        "inboxCount": inbox_count,
        "members": ordered_results,
        "batches": batches,
    }
