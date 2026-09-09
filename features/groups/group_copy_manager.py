"""Lưu trữ và quản lý tiến độ tác vụ sao chép thành viên giữa các nhóm Zalo."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta
from typing import Callable, Optional


def _base_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


BASE_DIR = _base_dir()
DATA_DIR = os.path.join(BASE_DIR, "data")
JOBS_FILE = os.path.join(DATA_DIR, "group_copy_jobs.json")
_LOCK = threading.RLock()

_TERMINAL_STATUSES = {"done", "partial", "expired", "failed", "cancelled"}
_MEMBER_STATUSES = {"pending", "invited", "joined", "failed", "skipped"}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _ensure_store() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.isfile(JOBS_FILE):
        _write_jobs_unlocked([])


def _write_jobs_unlocked(jobs: list[dict]) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    temp_path = JOBS_FILE + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump({"jobs": jobs}, f, ensure_ascii=False, indent=2)
    os.replace(temp_path, JOBS_FILE)


def _read_jobs_unlocked() -> list[dict]:
    _ensure_store()
    try:
        with open(JOBS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        jobs = data.get("jobs", data if isinstance(data, list) else [])
        return jobs if isinstance(jobs, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _normalize_friend_state(value):
    """Giữ trạng thái bạn bè để ưu tiên thêm trực tiếp vào nhóm."""
    if isinstance(value, bool):
        return value
    if value is None or value == "":
        return None
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "friend", "friends"}:
        return True
    if text in {"0", "false", "no", "not_friend", "not-friend"}:
        return False
    return None


def _to_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_member(item) -> Optional[dict]:
    if isinstance(item, str):
        uid = item.strip()
        if not uid:
            return None
        item = {"userId": uid, "zaloName": uid}
    if not isinstance(item, dict):
        return None

    uid = str(item.get("userId") or item.get("uid") or item.get("id") or "").strip()
    if not uid:
        return None

    friend_state = _normalize_friend_state(
        item.get("isFriend", item.get("isFr", item.get("friend")))
    )
    status = str(item.get("status") or "pending").strip().lower()

    # Tương thích dữ liệu từ các bản cũ.
    if status == "waiting_friend":
        status = "pending"
        item = dict(item)
        item["error"] = ""
    elif status == "success":
        status = "joined"
    if status not in _MEMBER_STATUSES:
        status = "pending"

    invited_at = int(item.get("invitedAt") or item.get("attemptedAt") or 0)
    joined_at = int(item.get("joinedAt") or 0)
    invite_attempts = max(0, _to_int(item.get("inviteAttempts"), 0))
    if status in {"invited", "joined"} and invite_attempts == 0 and invited_at:
        invite_attempts = 1

    invite_result_code = _to_int(item.get("inviteResultCode", item.get("inviteCode")), -1)
    invite_delivery = str(item.get("inviteDelivery") or "").strip()
    invite_result_message = str(item.get("inviteResultMessage") or "").strip()
    friend_request_attempts = max(0, _to_int(item.get("friendRequestAttempts"), 0))
    friend_request_at = int(item.get("friendRequestAt") or 0)
    friend_request_code = _to_int(item.get("friendRequestCode"), -1)
    friend_request_message = str(item.get("friendRequestMessage") or "").strip()
    friend_request_delivery = str(item.get("friendRequestDelivery") or "").strip()
    friend_request_created_by_campaign = bool(item.get("friendRequestCreatedByCampaign")) or (
        friend_request_attempts > 0
        and friend_request_delivery == "friend_request_with_group_link"
    )
    message_attempts = max(0, _to_int(item.get("messageAttempts"), 0))
    message_at = int(item.get("messageAt") or 0)
    message_code = _to_int(item.get("messageCode"), -1)
    message_message = str(item.get("messageMessage") or "").strip()
    message_delivery = str(item.get("messageDelivery") or "").strip()
    friend_remove_attempts = max(0, _to_int(item.get("friendRemoveAttempts"), 0))
    friend_removed_at = int(item.get("friendRemovedAt") or 0)
    friend_remove_code = _to_int(item.get("friendRemoveCode"), -1)
    friend_remove_message = str(item.get("friendRemoveMessage") or "").strip()
    friend_remove_delivery = str(item.get("friendRemoveDelivery") or "").strip()
    campaign_add_retry_attempts = max(0, _to_int(item.get("campaignAddRetryAttempts"), 0))
    campaign_add_retry_at = int(item.get("campaignAddRetryAt") or 0)
    joined_after_campaign_add_retry = bool(item.get("joinedAfterCampaignAddRetry"))

    # Chuyển dữ liệu cũ từng đánh dấu nhầm 262/263 là lỗi sang trạng thái đã gửi.
    if status == "failed" and invite_result_code in {262, 263, 240, 252}:
        status = "invited"
        item = dict(item)
        invite_delivery = "pending_inbox" if invite_result_code in {262, 263} else "pending_approval"
        invite_result_message = invite_result_message or (
            "Đã gửi lời mời vào tin nhắn chờ của người nhận."
            if invite_result_code in {262, 263}
            else "Đã gửi yêu cầu và đang chờ xử lý."
        )
        item["error"] = invite_result_message
    elif status == "failed" and invite_result_code == 178:
        status = "joined"
        item = dict(item)
        item["error"] = ""

    return {
        "userId": uid,
        "zaloName": str(item.get("zaloName") or item.get("displayName") or item.get("name") or uid).strip(),
        "avatar": str(item.get("avatar") or item.get("avatarUrl") or "").strip(),
        # Định danh gốc ổn định của người dùng (từ URL avatar) — GIỐNG nhau giữa mọi
        # tài khoản. Dùng để mỗi tài khoản phụ tự phân giải uid RIÊNG của nó khi đọc
        # lại nhóm nguồn (userId Zalo mã hóa riêng từng tài khoản).
        "avatarHash": str(item.get("avatarHash") or "").strip().lower(),
        # Tài khoản phụ đã phân giải được uid riêng cho người này chưa.
        "sourceUidResolved": bool(item.get("sourceUidResolved")),
        "isFriend": friend_state,
        "status": status,
        "error": str(item.get("error") or "").strip(),
        "attemptedAt": int(item.get("attemptedAt") or invited_at or 0),
        "invitedAt": invited_at,
        "joinedAt": joined_at,
        "lastVerifiedAt": int(item.get("lastVerifiedAt") or 0),
        "leftDetectedAt": int(item.get("leftDetectedAt") or 0),
        "inviteAttempts": invite_attempts,
        "inviteResultCode": invite_result_code,
        "inviteResultMessage": invite_result_message,
        "inviteDelivery": invite_delivery,
        "friendRequestAttempts": friend_request_attempts,
        "friendRequestAt": friend_request_at,
        "friendRequestCode": friend_request_code,
        "friendRequestMessage": friend_request_message,
        "friendRequestDelivery": friend_request_delivery,
        "friendRequestCreatedByCampaign": friend_request_created_by_campaign,
        "messageAttempts": message_attempts,
        "messageAt": message_at,
        "messageCode": message_code,
        "messageMessage": message_message,
        "messageDelivery": message_delivery,
        "friendRemoveAttempts": friend_remove_attempts,
        "friendRemovedAt": friend_removed_at,
        "friendRemoveCode": friend_remove_code,
        "friendRemoveMessage": friend_remove_message,
        "friendRemoveDelivery": friend_remove_delivery,
        "campaignAddRetryAttempts": campaign_add_retry_attempts,
        "campaignAddRetryAt": campaign_add_retry_at,
        "joinedAfterCampaignAddRetry": joined_after_campaign_add_retry,
    }


def _refresh_progress(job: dict) -> dict:
    members = job.get("members") or []
    if not isinstance(members, list):
        members = []
        job["members"] = members

    counts = {status: 0 for status in _MEMBER_STATUSES}
    friend_count = 0
    invited_sent_count = 0
    accepted_invite_count = 0
    preexisting_count = 0
    inbox_invite_count = 0
    campaign_friend_count = 0
    message_sent_count = 0
    removed_friend_count = 0

    for member in members:
        status = str((member or {}).get("status") or "pending").lower()
        if status not in counts:
            status = "pending"
            member["status"] = status
        counts[status] += 1
        if (member or {}).get("isFriend") is True:
            friend_count += 1
        attempts = max(0, _to_int((member or {}).get("inviteAttempts"), 0))
        friend_request_attempts = max(0, _to_int((member or {}).get("friendRequestAttempts"), 0))
        # Chỉ tính đã gửi khi Zalo thực sự chấp nhận yêu cầu. Thành viên failed
        # không còn bị cộng nhầm vào chỉ số đã gửi lời mời.
        if (attempts > 0 or friend_request_attempts > 0) and status in {"invited", "joined"}:
            invited_sent_count += 1
            if str((member or {}).get("friendRequestDelivery") or "") == "friend_request_with_group_link":
                inbox_invite_count += 1
            elif str((member or {}).get("inviteDelivery") or "") == "pending_inbox":
                inbox_invite_count += 1
            if status == "joined":
                accepted_invite_count += 1
        elif status == "joined":
            preexisting_count += 1
        if bool((member or {}).get("friendRequestCreatedByCampaign")):
            campaign_friend_count += 1
        if str((member or {}).get("messageDelivery") or "") == "sent":
            message_sent_count += 1
        if str((member or {}).get("friendRemoveDelivery") or "") == "removed":
            removed_friend_count += 1

    total = len(members)
    joined_count = counts["joined"]
    awaiting_join_count = counts["invited"]
    pending_invite_count = counts["pending"]
    active_count = pending_invite_count + awaiting_join_count
    conversion_rate = round((joined_count * 100 / total), 1) if total else 0.0
    invite_conversion_rate = (
        round((accepted_invite_count * 100 / invited_sent_count), 1)
        if invited_sent_count else 0.0
    )

    job["totalMembers"] = total
    job["friendCount"] = friend_count
    job["pendingInviteCount"] = pending_invite_count
    job["eligibleCount"] = pending_invite_count  # tương thích UI/API cũ
    job["waitingFriendCount"] = awaiting_join_count
    job["invitedCount"] = invited_sent_count
    job["inboxInviteCount"] = inbox_invite_count
    job["awaitingJoinCount"] = awaiting_join_count
    job["joinedCount"] = joined_count
    job["successCount"] = joined_count  # tương thích UI/API cũ
    job["acceptedInviteCount"] = accepted_invite_count
    job["preExistingCount"] = preexisting_count
    job["campaignFriendCount"] = campaign_friend_count
    job["messageSentCount"] = message_sent_count
    job["removedFriendCount"] = removed_friend_count
    job["failedCount"] = counts["failed"]
    job["skippedCount"] = counts["skipped"]
    job["remainingCount"] = max(0, total - joined_count)
    # pendingCount bao gồm cả người đã gửi lời mời nhưng chưa thực sự vào nhóm,
    # để worker tiếp tục kiểm tra định kỳ.
    job["pendingCount"] = active_count
    job["progressPercent"] = conversion_rate
    job["conversionRate"] = conversion_rate
    job["inviteConversionRate"] = invite_conversion_rate
    return job


def _extract_daily_time(start_at: str) -> str:
    try:
        parsed = datetime.fromisoformat(str(start_at or "").strip())
        return parsed.strftime("%H:%M")
    except (TypeError, ValueError):
        return datetime.now().strftime("%H:%M")


def _earliest_iso(values: list[str]) -> str:
    clean = [str(value or "").strip() for value in values if str(value or "").strip()]
    return min(clean) if clean else ""


def _normalize_job(job: dict) -> dict:
    job = dict(job or {})
    raw_members = job.get("members") or []
    legacy_waiting_found = any(
        isinstance(raw, dict) and str(raw.get("status") or "").strip().lower() == "waiting_friend"
        for raw in raw_members
    )
    normalized_members = []
    seen = set()
    for raw in raw_members:
        member = _normalize_member(raw)
        if not member or member["userId"] in seen:
            continue
        seen.add(member["userId"])
        normalized_members.append(member)
    legacy_invite_retry_found = False
    for member in normalized_members:
        note = str(member.get("error") or "").strip().lower().rstrip(".")
        code = _to_int(member.get("inviteResultCode"), -1)
        if member.get("status") == "failed" and (
            note in {"successful", "success"}
            or code in {262, 263, 240, 252}
        ):
            member["status"] = "pending"
            member["error"] = "Sẽ gửi lại bằng cùng logic Mời vào nhóm."
            legacy_invite_retry_found = True

    job["members"] = normalized_members
    job.setdefault("runs", [])
    job.setdefault("status", "pending")
    job.setdefault("createdAt", _now_ms())
    job.setdefault("updatedAt", job["createdAt"])
    job.setdefault("targetGroupId", "")
    job.setdefault("targetGroupName", "")
    job.setdefault("accountAvatar", "")
    job.setdefault("lastVerifiedAt", "")
    job.setdefault("lastVerificationError", "")
    job.setdefault("targetMemberCount", 0)

    daily_limit = job.get("friendRequestDailyLimit", job.get("dailyLimit", job.get("batchSize", 10)))
    try:
        daily_limit = max(1, min(int(daily_limit or 10), 30))
    except (TypeError, ValueError):
        daily_limit = 10
    job["dailyLimit"] = daily_limit
    job["batchSize"] = daily_limit
    job["friendRequestDailyLimit"] = daily_limit
    job.setdefault("dailyFriendRequestDate", "")
    job.setdefault("dailyFriendRequestCount", 0)
    job.setdefault("groupLink", "")
    job.setdefault("groupLinkExpirationDate", 0)
    job.setdefault("groupLinkEnabled", 0)
    job.setdefault("groupLinkUpdatedAt", "")
    job.setdefault("campaignId", "")
    job.setdefault("resolvedTargetGroupId", "")   # groupId nhóm đích RIÊNG của tài khoản này
    job.setdefault("accountJoinedTarget", False)
    job.setdefault("sourceGroupLink", "")          # link nhóm nguồn để tài khoản phụ tự đọc
    job.setdefault("accountJoinedSource", False)   # tài khoản phụ đã vào nhóm nguồn chưa
    job.setdefault("sourceUidsResolved", False)    # đã phân giải uid nhóm nguồn cho tài khoản này chưa
    job.setdefault("sourceJoinAttempts", 0)        # số lần đã thử vào/đọc nhóm nguồn
    job.setdefault("memberReadErrors", 0)          # số lần lỗi đọc thành viên liên tiếp
    job["removeFriendAfterJoin"] = bool(job.get("removeFriendAfterJoin"))
    job["leaveGroupAfterDone"] = bool(job.get("leaveGroupAfterDone"))
    job.setdefault("sourceLeftAt", "")
    job["scheduleMode"] = "daily"
    job["dailyRunTime"] = str(job.get("dailyRunTime") or _extract_daily_time(job.get("startAt"))).strip()

    try:
        verify_minutes = max(1, min(int(job.get("verifyIntervalMinutes") or 30), 1440))
    except (TypeError, ValueError):
        verify_minutes = 30
    job["verifyIntervalMinutes"] = verify_minutes

    try:
        campaign_days = max(1, min(int(job.get("campaignDurationDays") or 30), 365))
    except (TypeError, ValueError):
        campaign_days = 30
    job["campaignDurationDays"] = campaign_days

    start_at = str(job.get("startAt") or _now_iso()).strip()
    job["startAt"] = start_at
    campaign_end_at = str(job.get("campaignEndAt") or "").strip()
    if not campaign_end_at:
        try:
            campaign_end_at = (datetime.fromisoformat(start_at) + timedelta(days=campaign_days)).isoformat(timespec="seconds")
        except (TypeError, ValueError):
            campaign_end_at = (datetime.now() + timedelta(days=campaign_days)).isoformat(timespec="seconds")
    job["campaignEndAt"] = campaign_end_at
    job.setdefault("campaignExpiredAt", "")
    job.setdefault("nextInviteAt", job.get("nextRunAt") or start_at)

    # Tác vụ từ bản cũ từng giữ người chưa kết bạn ở waiting_friend. Sau khi
    # nâng cấp, đưa toàn bộ những người đó vào đợt mời ngay một lần.
    if legacy_waiting_found and not job.get("inviteAllMigrationApplied") and job.get("status") not in {"cancelled", "running"}:
        migrate_at = _now_iso()
        job["status"] = "pending"
        job["nextInviteAt"] = migrate_at
        job["nextRunAt"] = migrate_at
        if str(job.get("targetGroupId") or "").strip():
            job["nextVerifyAt"] = migrate_at
        job["inviteAllMigrationApplied"] = True
    if legacy_invite_retry_found and not job.get("sharedInviteMigrationApplied") and job.get("status") not in {"cancelled", "running"}:
        migrate_at = _now_iso()
        job["status"] = "pending"
        job["nextInviteAt"] = migrate_at
        job["nextRunAt"] = migrate_at
        if str(job.get("targetGroupId") or "").strip():
            job["nextVerifyAt"] = migrate_at
        job["sharedInviteMigrationApplied"] = True
    if str(job.get("targetGroupId") or "").strip():
        job.setdefault("nextVerifyAt", job.get("nextRunAt") or start_at)
    else:
        job.setdefault("nextVerifyAt", "")

    _refresh_progress(job)

    # Đã đủ thành viên thì kết thúc tác vụ, không tiếp tục theo dõi hay kiểm tra.
    if job.get("totalMembers") and (
        int(job.get("joinedCount") or 0) + int(job.get("skippedCount") or 0)
        >= int(job.get("totalMembers") or 0)
    ):
        job["status"] = "done"
        job["completedAt"] = str(job.get("completedAt") or _now_iso())
        job["nextInviteAt"] = ""
        job["nextVerifyAt"] = ""
        job["nextRunAt"] = ""

    if job.get("status") in {"pending", "monitoring"}:
        try:
            if datetime.now() >= datetime.fromisoformat(campaign_end_at):
                job["status"] = "done" if not job.get("pendingCount") else "expired"
                job["campaignExpiredAt"] = job.get("campaignExpiredAt") or _now_iso()
                job["nextInviteAt"] = ""
                job["nextVerifyAt"] = ""
                job["nextRunAt"] = ""
        except (TypeError, ValueError):
            pass

    if job.get("status") in {"pending", "monitoring"}:
        candidates = []
        if job.get("pendingInviteCount"):
            candidates.append(str(job.get("nextInviteAt") or start_at))
        if str(job.get("targetGroupId") or "").strip() and (
            job.get("awaitingJoinCount") or job.get("pendingInviteCount") or job.get("status") == "monitoring"
        ):
            candidates.append(str(job.get("nextVerifyAt") or start_at))
        job["nextRunAt"] = _earliest_iso(candidates) or str(job.get("nextRunAt") or start_at)
    elif job.get("status") in _TERMINAL_STATUSES:
        job.setdefault("nextRunAt", "")

    return job


def create_job(
    payload: dict,
    members: list[dict],
    source_group: dict,
    account_name: str,
    account_avatar: str = "",
) -> dict:
    clean_members = []
    seen = set()
    for raw in members or []:
        member = _normalize_member(raw)
        if not member or member["userId"] in seen:
            continue
        seen.add(member["userId"])
        # Mọi thành viên đã là bạn bè được ưu tiên thêm trực tiếp trước. Chỉ
        # người chưa thêm được mới nhận lời mời kết bạn theo hạn mức mỗi ngày.
        member["status"] = "pending"
        member["error"] = ""
        member["attemptedAt"] = 0
        member["invitedAt"] = 0
        member["joinedAt"] = 0
        member["lastVerifiedAt"] = 0
        member["inviteAttempts"] = 0
        member["inviteResultCode"] = -1
        member["inviteResultMessage"] = ""
        member["inviteDelivery"] = ""
        member["friendRequestAttempts"] = 0
        member["friendRequestAt"] = 0
        member["friendRequestCode"] = -1
        member["friendRequestMessage"] = ""
        member["friendRequestDelivery"] = ""
        member["friendRequestCreatedByCampaign"] = False
        member["friendRemoveAttempts"] = 0
        member["friendRemovedAt"] = 0
        member["friendRemoveCode"] = -1
        member["friendRemoveMessage"] = ""
        member["friendRemoveDelivery"] = ""
        member["campaignAddRetryAttempts"] = 0
        member["campaignAddRetryAt"] = 0
        member["joinedAfterCampaignAddRetry"] = False
        clean_members.append(member)

    if not clean_members:
        raise ValueError("Nhóm nguồn không có thành viên hợp lệ để lập lịch.")

    start_at = str(payload.get("startAt") or datetime.now().isoformat(timespec="minutes")).strip()
    try:
        daily_limit = max(1, min(int(payload.get("friendRequestDailyLimit") or payload.get("dailyLimit") or payload.get("batchSize") or 10), 30))
    except (TypeError, ValueError):
        daily_limit = 10
    try:
        verify_minutes = max(1, min(int(payload.get("verifyIntervalMinutes") or 30), 1440))
    except (TypeError, ValueError):
        verify_minutes = 30
    try:
        campaign_days = max(1, min(int(payload.get("campaignDurationDays") or 30), 365))
    except (TypeError, ValueError):
        campaign_days = 30

    target_mode = str(payload.get("targetMode") or "existing").strip()
    target_group_id = str(payload.get("targetGroupId") or "").strip()
    target_group_link = str(payload.get("targetGroupLink") or "").strip()
    if target_group_link.startswith("//"):
        target_group_link = "https:" + target_group_link
    elif target_group_link and not target_group_link.lower().startswith(("http://", "https://")):
        token = target_group_link.strip().strip("/")
        target_group_link = ("https://" + token) if token.lower().startswith("zalo.me/g/") else ("https://zalo.me/g/" + token)
    try:
        campaign_end_at = (datetime.fromisoformat(start_at) + timedelta(days=campaign_days)).isoformat(timespec="seconds")
    except (TypeError, ValueError):
        campaign_end_at = (datetime.now() + timedelta(days=campaign_days)).isoformat(timespec="seconds")
    now = _now_ms()
    job = {
        "jobId": "gcopy_" + uuid.uuid4().hex[:12],
        "title": str(payload.get("title") or "Sao chép thành viên nhóm").strip(),
        "accountId": str(payload.get("accountId") or "").strip(),
        "campaignId": str(payload.get("campaignId") or "").strip(),
        "accountName": str(account_name or payload.get("accountName") or "").strip(),
        "accountAvatar": str(account_avatar or payload.get("accountAvatar") or "").strip(),
        "sourceInput": str(payload.get("sourceInput") or "").strip(),
        "sourceGroupId": str(payload.get("sourceGroupId") or (source_group or {}).get("groupId") or (source_group or {}).get("id") or "").strip(),
        # Link mời nhóm NGUỒN — để tài khoản phụ tự phân giải groupId riêng + tự
        # tham gia + đọc lại thành viên nhằm lấy uid hợp lệ cho phiên của chính nó.
        "sourceGroupLink": str(payload.get("sourceGroupLink") or (source_group or {}).get("link") or "").strip(),
        "accountJoinedSource": False,
        "sourceUidsResolved": False,
        "sourceGroup": source_group or {},
        "targetMode": target_mode,
        "targetGroupId": target_group_id,
        # Tài khoản CHÍNH sở hữu nhóm đích — dùng để đọc/mời nhóm đích thay cho
        # tài khoản phụ (tránh lỗi "Tham số không hợp lệ" khi phụ không ở trong nhóm).
        "targetOwnerAccountId": str(payload.get("targetOwnerAccountId") or payload.get("accountId") or "").strip(),
        "targetGroupName": str(payload.get("targetGroupName") or payload.get("newGroupName") or "").strip(),
        "newGroupName": str(payload.get("newGroupName") or "").strip(),
        "dailyLimit": daily_limit,
        "batchSize": daily_limit,
        "friendRequestDailyLimit": daily_limit,
        "dailyFriendRequestDate": "",
        "dailyFriendRequestCount": 0,
        "scheduleMode": "daily",
        "dailyRunTime": _extract_daily_time(start_at),
        "verifyIntervalMinutes": verify_minutes,
        "campaignDurationDays": campaign_days,
        "campaignEndAt": campaign_end_at,
        "campaignExpiredAt": "",
        "removeFriendAfterJoin": bool(payload.get("removeFriendAfterJoin")),
        "leaveGroupAfterDone": bool(payload.get("leaveGroupAfterDone")),
        "sourceLeftAt": "",
        "startAt": start_at,
        "nextInviteAt": start_at,
        "nextVerifyAt": start_at if target_mode == "existing" and target_group_id else "",
        "nextRunAt": start_at,
        "status": "pending",
        "consentConfirmed": bool(payload.get("consentConfirmed")),
        "createdAt": now,
        "updatedAt": now,
        "lastRunAt": "",
        "lastError": "",
        "lastNotice": "",
        "lastVerifiedAt": "",
        "lastVerificationError": "",
        "targetMemberCount": 0,
        "groupLink": target_group_link if target_mode == "existing" else "",
        "groupLinkExpirationDate": 0,
        "groupLinkEnabled": 1 if (target_mode == "existing" and target_group_link) else 0,
        "groupLinkUpdatedAt": datetime.now().isoformat(timespec="seconds") if (target_mode == "existing" and target_group_link) else "",
        "groupLinkSource": "personal_groups" if (target_mode == "existing" and target_group_link) else "",
        "members": clean_members,
        "runs": [],
    }
    _refresh_progress(job)

    with _LOCK:
        jobs = _read_jobs_unlocked()
        jobs.append(job)
        _write_jobs_unlocked(jobs)
    return job


def list_jobs(include_members: bool = False) -> list[dict]:
    with _LOCK:
        jobs = [_normalize_job(item) for item in _read_jobs_unlocked() if isinstance(item, dict)]
    jobs.sort(key=lambda item: int(item.get("createdAt") or 0), reverse=True)
    if include_members:
        return jobs
    summaries = []
    for job in jobs:
        summary = dict(job)
        summary.pop("members", None)
        summaries.append(summary)
    return summaries


def get_job(job_id: str) -> Optional[dict]:
    with _LOCK:
        for item in _read_jobs_unlocked():
            if str(item.get("jobId") or "") == str(job_id):
                return _normalize_job(item)
    return None


def save_job(job: dict) -> dict:
    job_id = str(job.get("jobId") or "").strip()
    if not job_id:
        raise ValueError("Tác vụ không có jobId.")
    job = _normalize_job(job)
    job["updatedAt"] = _now_ms()

    with _LOCK:
        jobs = _read_jobs_unlocked()
        for index, item in enumerate(jobs):
            if str(item.get("jobId") or "") == job_id:
                jobs[index] = job
                _write_jobs_unlocked(jobs)
                return job
    raise ValueError("Không tìm thấy tác vụ sao chép nhóm.")


def mutate_job(job_id: str, mutator: Callable[[dict], None]) -> dict:
    with _LOCK:
        jobs = _read_jobs_unlocked()
        for index, item in enumerate(jobs):
            if str(item.get("jobId") or "") != str(job_id):
                continue
            job = _normalize_job(item)
            mutator(job)
            job["updatedAt"] = _now_ms()
            _refresh_progress(job)
            jobs[index] = job
            _write_jobs_unlocked(jobs)
            return job
    raise ValueError("Không tìm thấy tác vụ sao chép nhóm.")


def _fresh_pending_member(src: dict) -> dict:
    """Tạo bản ghi thành viên MỚI ở trạng thái pending để chuyển sang nick khác.

    Chỉ giữ định danh cần thiết (userId tạm, avatarHash, tên, ảnh, quan hệ). Cờ
    sourceUidResolved=False để nick nhận TỰ phân giải uid RIÊNG của nó theo avatarHash.
    """
    member = _normalize_member(src) or {}
    member["status"] = "pending"
    member["error"] = ""
    member["sourceUidResolved"] = False
    for key in (
        "attemptedAt", "invitedAt", "joinedAt", "lastVerifiedAt", "leftDetectedAt",
        "inviteAttempts", "friendRequestAttempts", "friendRequestAt",
        "friendRequestCreatedByCampaign", "campaignAddRetryAttempts", "campaignAddRetryAt",
        "joinedAfterCampaignAddRetry", "friendRemoveAttempts",
    ):
        if key in member:
            member[key] = 0 if isinstance(member.get(key), int) else (False if isinstance(member.get(key), bool) else member[key])
    member["inviteResultCode"] = -1
    member["inviteResultMessage"] = ""
    member["inviteDelivery"] = ""
    member["friendRequestCode"] = -1
    member["friendRequestMessage"] = ""
    member["friendRequestDelivery"] = ""
    return member


def redistribute_campaign_members(failed_job_id: str, reason: str = "") -> dict:
    """Chia lại phần việc của một nick KHÔNG vào được nhóm nguồn cho các nick đang hoạt động.

    Khi một tài khoản (thường là phụ) không tự tham gia/đọc được nhóm nguồn để lấy uid
    hợp lệ, các thành viên chưa xử lý của nó được PHÂN BỔ LẠI (round-robin) cho những
    nick cùng chiến dịch đang hoạt động (đã vào được nhóm nguồn hoặc là nick chủ). Nick
    nhận sẽ tự phân giải uid RIÊNG theo avatarHash ở lần chạy kế tiếp. Job lỗi bị đánh
    dấu failed. Trả về số người đã chia lại và số nick nhận.
    """
    with _LOCK:
        jobs = _read_jobs_unlocked()
        failed_idx = None
        for index, item in enumerate(jobs):
            if str(item.get("jobId") or "") == str(failed_job_id):
                failed_idx = index
                break
        if failed_idx is None:
            return {"redistributed": 0, "recipients": 0, "reason": "not_found"}

        failed_job = _normalize_job(jobs[failed_idx])
        campaign_id = str(failed_job.get("campaignId") or "").strip()

        # Thành viên có thể chuyển: chưa xử lý xong (pending/skipped).
        movable = [
            m for m in (failed_job.get("members") or [])
            if str(m.get("status") or "") in ("pending", "skipped")
        ]

        def _finalize_failed(note: str) -> None:
            for m in failed_job.get("members") or []:
                if str(m.get("status") or "") in ("pending", "skipped"):
                    m["status"] = "skipped"
                    m["error"] = note
            failed_job["status"] = "failed"
            failed_job["nextRunAt"] = ""
            failed_job["nextInviteAt"] = ""
            failed_job["nextVerifyAt"] = ""
            failed_job["lastError"] = reason or note
            failed_job["lastNotice"] = note
            _refresh_progress(failed_job)
            failed_job["updatedAt"] = _now_ms()
            jobs[failed_idx] = failed_job

        if not campaign_id:
            _finalize_failed("Không vào được nhóm nguồn; tác vụ dừng (không có chiến dịch để chia lại).")
            _write_jobs_unlocked(jobs)
            return {"redistributed": 0, "recipients": 0, "reason": "no_campaign"}

        # Nick nhận: CHỈ những tài khoản ĐÃ CHỨNG MINH đọc được thành viên — tức là
        # nick CHỦ (luôn đọc được nhóm), hoặc nick phụ đã phân giải nguồn thành công
        # (sourceUidsResolved=True) và không đang lỗi đọc. TUYỆT ĐỐI không chia cho các
        # nick phụ chỉ mới "join" nhưng không đọc được (tránh chia lan truyền qua lại
        # giữa các nick lỗi). Nhờ vậy việc sẽ GỘP VỀ tài khoản chính chạy được.
        recipient_indices = []
        for index, item in enumerate(jobs):
            if index == failed_idx:
                continue
            if str(item.get("campaignId") or "").strip() != campaign_id:
                continue
            if str(item.get("status") or "") in ("cancelled", "failed", "expired"):
                continue
            owner_id = str(item.get("targetOwnerAccountId") or "").strip()
            acc_id = str(item.get("accountId") or "").strip()
            is_owner = not owner_id or owner_id == acc_id
            proven_reader = is_owner or (
                item.get("sourceUidsResolved") is True
                and int(item.get("memberReadErrors") or 0) == 0
            )
            if proven_reader:
                recipient_indices.append(index)

        if not movable or not recipient_indices:
            _finalize_failed(
                "Không vào được nhóm nguồn; không có nick đang hoạt động để chia lại phần việc."
                if not recipient_indices else
                "Không vào được nhóm nguồn; không còn thành viên chưa xử lý để chia lại."
            )
            _write_jobs_unlocked(jobs)
            return {"redistributed": 0, "recipients": len(recipient_indices), "reason": "nothing_to_do"}

        # Chuẩn hóa danh sách nick nhận + tập avatarHash đã có (tránh trùng người).
        recipients = [_normalize_job(jobs[i]) for i in recipient_indices]
        recipient_hashes = []
        for rj in recipients:
            hs = set()
            for m in rj.get("members") or []:
                h = str(m.get("avatarHash") or "").strip().lower()
                if h:
                    hs.add(h)
            recipient_hashes.append(hs)

        assigned_global = set()   # tránh giao cùng 1 người cho nhiều nick
        distributed = 0
        rr = 0
        now_iso = datetime.now().isoformat(timespec="seconds")
        for src in movable:
            h = str(src.get("avatarHash") or "").strip().lower()
            key = h or str(src.get("userId") or "").strip()
            if key and key in assigned_global:
                continue
            # Tìm nick nhận chưa có người này (round-robin bắt đầu từ rr).
            placed = False
            for step in range(len(recipients)):
                ri = (rr + step) % len(recipients)
                if h and h in recipient_hashes[ri]:
                    continue
                recipients[ri].setdefault("members", []).append(_fresh_pending_member(src))
                if h:
                    recipient_hashes[ri].add(h)
                if key:
                    assigned_global.add(key)
                rr = ri + 1
                distributed += 1
                placed = True
                break
            if not placed and recipients:
                # Mọi nick đều đã có người này -> bỏ qua (đã được xử lý ở nơi khác).
                if key:
                    assigned_global.add(key)

        # Ghi lại các nick nhận: buộc phân giải lại uid nguồn + đánh thức chạy sớm.
        for offset, ri in enumerate(recipient_indices):
            rj = recipients[offset]
            rj["sourceUidsResolved"] = False   # có người mới -> đọc lại nhóm nguồn để map uid
            if str(rj.get("status") or "") in ("done", "monitoring", "partial"):
                rj["status"] = "pending"
            rj["nextRunAt"] = now_iso
            if not str(rj.get("nextInviteAt") or "").strip():
                rj["nextInviteAt"] = now_iso
            rj["lastNotice"] = (
                f"Đã nhận thêm phần việc từ một nick không vào được nhóm nguồn."
            )
            _refresh_progress(rj)
            rj["updatedAt"] = _now_ms()
            jobs[ri] = rj

        _finalize_failed(
            f"Không vào được nhóm nguồn sau nhiều lần thử; đã chia lại {distributed} người "
            f"cho {len(recipient_indices)} nick đang hoạt động."
        )
        _write_jobs_unlocked(jobs)
        return {"redistributed": distributed, "recipients": len(recipient_indices)}


def claim_due_job(now_iso: str) -> Optional[dict]:
    """Đánh dấu một tác vụ đến hạn là running để worker xử lý độc quyền."""
    with _LOCK:
        jobs = _read_jobs_unlocked()
        due_items = []
        now_ms = _now_ms()
        store_changed = False
        for index, raw in enumerate(jobs):
            job = _normalize_job(raw)
            if job.get("status") == "running" and now_ms - int(job.get("updatedAt") or 0) > 15 * 60 * 1000:
                job["status"] = "pending"
                job["nextRunAt"] = now_iso
                if job.get("pendingInviteCount"):
                    job["nextInviteAt"] = now_iso
                elif job.get("awaitingJoinCount"):
                    job["nextVerifyAt"] = now_iso
                job["lastError"] = "Tác vụ được khôi phục sau khi ứng dụng dừng giữa đợt."
                job["updatedAt"] = now_ms
                jobs[index] = job
                store_changed = True
            if job.get("status") not in {"pending", "monitoring"}:
                continue
            has_work = bool(job.get("pendingCount")) or bool(
                str(job.get("targetGroupId") or "").strip()
                and str(job.get("nextVerifyAt") or "").strip()
            )
            if not has_work:
                continue
            next_run = str(job.get("nextRunAt") or "")
            if next_run and next_run <= now_iso:
                due_items.append((next_run, index, job))
        if not due_items:
            if store_changed:
                _write_jobs_unlocked(jobs)
            return None
        due_items.sort(key=lambda item: item[0])
        _, index, job = due_items[0]
        job["status"] = "running"
        job["lastRunAt"] = now_iso
        job["updatedAt"] = _now_ms()
        jobs[index] = job
        _write_jobs_unlocked(jobs)
        return job


def recover_running_jobs(now_iso: str = "") -> int:
    """Đưa các tác vụ đang chạy dở về hàng đợi khi ứng dụng khởi động lại."""
    now_iso = now_iso or _now_iso()
    recovered = 0
    with _LOCK:
        jobs = _read_jobs_unlocked()
        for index, raw in enumerate(jobs):
            job = _normalize_job(raw)
            if job.get("status") != "running":
                continue
            if not job.get("pendingCount"):
                job["status"] = "monitoring" if str(job.get("targetGroupId") or "").strip() else "done"
                job["nextRunAt"] = job.get("nextVerifyAt") or ""
            else:
                job["status"] = "pending"
                job["nextRunAt"] = now_iso
                if job.get("pendingInviteCount"):
                    job["nextInviteAt"] = now_iso
                if job.get("awaitingJoinCount") and str(job.get("targetGroupId") or "").strip():
                    job["nextVerifyAt"] = now_iso
            job["lastError"] = "Tác vụ được tiếp tục sau khi ứng dụng khởi động lại."
            job["updatedAt"] = _now_ms()
            jobs[index] = job
            recovered += 1
        if recovered:
            _write_jobs_unlocked(jobs)
    return recovered


def cancel_job(job_id: str) -> dict:
    def apply(job: dict) -> None:
        if job.get("status") == "running":
            raise ValueError("Đợt hiện tại đang chạy. Vui lòng chờ đợt này kết thúc rồi hủy.")
        if job.get("status") in _TERMINAL_STATUSES and job.get("status") != "failed":
            raise ValueError("Tác vụ đã kết thúc, không thể hủy.")
        job["status"] = "cancelled"
        job["nextInviteAt"] = ""
        job["nextVerifyAt"] = ""
        job["nextRunAt"] = ""
        job["lastError"] = ""

    return mutate_job(job_id, apply)


def resume_job(job_id: str, next_run_at: str = "") -> dict:
    def apply(job: dict) -> None:
        campaign_end = None
        try:
            campaign_end = datetime.fromisoformat(str(job.get("campaignEndAt") or ""))
        except (TypeError, ValueError):
            pass
        if campaign_end and datetime.now() >= campaign_end:
            raise ValueError("Chiến dịch đã hết thời gian chạy, không thể tiếp tục.")
        if not job.get("pendingCount"):
            raise ValueError("Tác vụ không còn thành viên đang chờ.")
        if job.get("status") == "running":
            raise ValueError("Tác vụ đang chạy.")
        run_at = next_run_at or datetime.now().isoformat(timespec="seconds")
        job["status"] = "pending"
        if job.get("pendingInviteCount"):
            job["nextInviteAt"] = run_at
        if str(job.get("targetGroupId") or "").strip():
            job["nextVerifyAt"] = run_at
        job["nextRunAt"] = run_at
        job["lastError"] = ""

    return mutate_job(job_id, apply)


def request_verification(job_id: str) -> dict:
    """Đưa tác vụ về hàng đợi để kiểm tra ngay danh sách thành viên nhóm đích."""
    def apply(job: dict) -> None:
        campaign_end = None
        try:
            campaign_end = datetime.fromisoformat(str(job.get("campaignEndAt") or ""))
        except (TypeError, ValueError):
            pass
        if campaign_end and datetime.now() >= campaign_end:
            raise ValueError("Chiến dịch đã hết thời gian chạy, không thể kiểm tra thêm.")
        if not str(job.get("targetGroupId") or "").strip():
            raise ValueError("Tác vụ chưa có nhóm đích để kiểm tra.")
        if job.get("status") == "running":
            raise ValueError("Tác vụ đang chạy. Vui lòng chờ đợt hiện tại kết thúc.")
        now_iso = datetime.now().isoformat(timespec="seconds")
        job["status"] = "pending" if job.get("pendingCount") else "monitoring"
        job["nextVerifyAt"] = now_iso
        job["nextRunAt"] = now_iso
        job["lastVerificationError"] = ""

    return mutate_job(job_id, apply)


def delete_job(job_id: str) -> None:
    with _LOCK:
        jobs = _read_jobs_unlocked()
        for index, item in enumerate(jobs):
            if str(item.get("jobId") or "") != str(job_id):
                continue
            if str(item.get("status") or "") == "running":
                raise ValueError("Không thể xóa tác vụ đang chạy.")
            jobs.pop(index)
            _write_jobs_unlocked(jobs)
            return
    raise ValueError("Không tìm thấy tác vụ sao chép nhóm.")
