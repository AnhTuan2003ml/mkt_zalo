"""Worker sao chép thành viên nhóm theo hạn mức kết bạn mỗi ngày.

Nguyên tắc:
- Sau khi quét nhóm nguồn, ưu tiên thêm toàn bộ thành viên đã là bạn bè.
- Người thêm trực tiếp thành công không bị tính vào hạn mức hằng ngày.
- Người chưa thêm được sẽ nhận lời mời kết bạn, tối đa X người/ngày.
- Những người đã được chiến dịch gửi kết bạn sẽ được thử add lại theo chu kỳ
  cấu hình, mặc định 30 phút, cho đến khi vào nhóm hoặc hết hạn chiến dịch.
- Không gửi tin nhắn riêng. Link nhóm chỉ nằm trong nội dung lời mời kết bạn.
- Khi thành viên vào nhóm, có thể xóa kết bạn đúng người do chiến dịch vừa
  kết bạn; bạn bè có từ trước không bao giờ bị xóa.
- Tác vụ dừng ngay khi đủ thành viên hoặc khi hết hạn chiến dịch.
"""

from __future__ import annotations

import json
import random
import threading
import time
from datetime import datetime, timedelta
from typing import Optional

from core.zalo.zalo_config import get_zpw_ver
from features.accounts.account_manager import get_account
from features.groups.add_group import create_group
from features.groups.group_copy_manager import claim_due_job, save_job, get_job, recover_running_jobs
from features.groups.group_link import create_group_link, get_group_link_detail
from features.groups.invite_group import invite_members_to_group
from features.groups.group_join_leave import join_group_by_link
from features.members.get_members import get_members_by_group_id
from features.messaging.add_friend import send_friend_request
from features.messaging.remove_friend import remove_friend


def _decoded_dict(value) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {"raw": value}
        except Exception:
            return {"raw": value}
    return {}


def _error_code(response_json: dict, decoded: dict) -> int:
    raw = decoded.get("error_code", response_json.get("error_code", -1))
    try:
        return int(raw or 0)
    except Exception:
        return -1


def _error_message(response_json: dict, decoded: dict) -> str:
    return str(
        decoded.get("error_message")
        or decoded.get("message")
        or response_json.get("error_message")
        or response_json.get("message")
        or ""
    ).strip()


def _extract_group_id(response_json: dict, decoded: dict) -> str:
    candidates = [decoded.get("data"), response_json.get("data"), decoded, response_json]
    for item in candidates:
        if not isinstance(item, dict):
            continue
        gid = item.get("groupId") or item.get("grid") or item.get("gridId") or item.get("id")
        if gid:
            return str(gid).strip()
    return ""


def _extract_error_member_ids(response_json: dict, decoded: dict) -> set[str]:
    result: set[str] = set()
    candidates = []
    for root in (decoded, response_json):
        if not isinstance(root, dict):
            continue
        data = root.get("data") if isinstance(root.get("data"), dict) else {}
        candidates.extend([data.get("errorMembers"), root.get("errorMembers")])

    for raw_items in candidates:
        if not raw_items:
            continue
        if not isinstance(raw_items, list):
            raw_items = [raw_items]
        for item in raw_items:
            if isinstance(item, dict):
                uid = item.get("userId") or item.get("uid") or item.get("id") or item.get("memberId")
            else:
                uid = item
            uid = str(uid or "").strip()
            if uid:
                result.add(uid)
    return result


def _daily_limit(job: dict) -> int:
    try:
        return max(
            1,
            min(
                int(
                    job.get("friendRequestDailyLimit")
                    or job.get("dailyLimit")
                    or job.get("batchSize")
                    or 10
                ),
                30,   # tối đa 30 lời mời kết bạn / tài khoản / ngày
            ),
        )
    except (TypeError, ValueError):
        return 10


def _verify_interval(job: dict) -> int:
    try:
        return max(1, min(int(job.get("verifyIntervalMinutes") or 30), 1440))
    except (TypeError, ValueError):
        return 30


def _daily_run_time(job: dict) -> tuple[int, int]:
    raw = str(job.get("dailyRunTime") or "").strip()
    if raw:
        try:
            hour, minute = raw.split(":", 1)
            return max(0, min(int(hour), 23)), max(0, min(int(minute), 59))
        except (TypeError, ValueError):
            pass
    try:
        start = datetime.fromisoformat(str(job.get("startAt") or ""))
        return start.hour, start.minute
    except (TypeError, ValueError):
        now = datetime.now()
        return now.hour, now.minute


def _next_daily_run(job: dict, after: Optional[datetime] = None) -> str:
    after = after or datetime.now()
    hour, minute = _daily_run_time(job)
    next_day = after.date() + timedelta(days=1)
    candidate = datetime.combine(next_day, datetime.min.time()).replace(hour=hour, minute=minute)
    return candidate.isoformat(timespec="seconds")


def _next_verify_run(job: dict, after: Optional[datetime] = None) -> str:
    after = after or datetime.now()
    return (after + timedelta(minutes=_verify_interval(job))).isoformat(timespec="seconds")


def _send_gap_minutes(job: dict) -> int:
    """Khoảng nghỉ NGẪU NHIÊN (phút) giữa 2 lần gửi kết bạn/mời của MỖI tài khoản.

    Mặc định random 5–20 phút để tránh Zalo chặn do gửi dồn dập. Có thể chỉnh
    qua job: sendMinMinutes / sendMaxMinutes.
    """
    try:
        lo = int(job.get("sendMinMinutes") or 5)
    except (TypeError, ValueError):
        lo = 5
    try:
        hi = int(job.get("sendMaxMinutes") or 20)
    except (TypeError, ValueError):
        hi = 20
    lo = max(1, min(lo, 1440))
    hi = max(lo, min(hi, 1440))
    return random.randint(lo, hi)


def _next_send_run(job: dict, after: Optional[datetime] = None) -> str:
    after = after or datetime.now()
    return (after + timedelta(minutes=_send_gap_minutes(job))).isoformat(timespec="seconds")


def _parse_iso(value: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(value or "").strip())
    except (TypeError, ValueError):
        return None


def _is_due(value: str, now: Optional[datetime] = None) -> bool:
    parsed = _parse_iso(value)
    return parsed is None or parsed <= (now or datetime.now())


def _cookie_dict(cookies: str) -> dict:
    result = {}
    for item in str(cookies or "").split(";"):
        item = item.strip()
        if "=" in item:
            key, value = item.split("=", 1)
            result[key.strip()] = value.strip()
    return result


def _reset_daily_friend_request_counter(job: dict, now: Optional[datetime] = None) -> None:
    now = now or datetime.now()
    current_date = now.date().isoformat()
    if str(job.get("dailyFriendRequestDate") or "") != current_date:
        job["dailyFriendRequestDate"] = current_date
        job["dailyFriendRequestCount"] = 0


def _remaining_friend_requests_today(job: dict, now: Optional[datetime] = None) -> int:
    _reset_daily_friend_request_counter(job, now)
    used = max(0, int(job.get("dailyFriendRequestCount") or 0))
    return max(0, _daily_limit(job) - used)


def _consume_friend_request_slot(job: dict, now: Optional[datetime] = None) -> None:
    _reset_daily_friend_request_counter(job, now)
    job["dailyFriendRequestCount"] = int(job.get("dailyFriendRequestCount") or 0) + 1


def _friend_request_text(job: dict, group_link: str) -> str:
    group_name = str(job.get("targetGroupName") or job.get("newGroupName") or "nhóm").strip()[:60]
    return f"Mời bạn tham gia nhóm {group_name}: {group_link}".strip()


def _ensure_group_link(
    job: dict,
    zpw_enk: str,
    cookies: str,
    imei: str,
    *,
    newly_created: bool = False,
    force: bool = False,
) -> str:
    """Lấy link nhóm và lưu vào tác vụ để tái sử dụng đến khi hết hạn."""
    now_ms = int(time.time() * 1000)
    cached_link = str(job.get("groupLink") or "").strip()
    try:
        expiration = int(job.get("groupLinkExpirationDate") or 0)
    except (TypeError, ValueError):
        expiration = 0
    enabled = int(job.get("groupLinkEnabled") or 0)
    cache_valid = bool(cached_link) and enabled == 1 and (expiration == 0 or expiration > now_ms + 60_000)
    if cache_valid and not force:
        return cached_link

    group_id = str(job.get("targetGroupId") or "").strip()
    if not group_id:
        raise ValueError("Tác vụ chưa có Group ID để lấy link tham gia.")

    if newly_created:
        result = create_group_link(
            group_id,
            imei,
            zpw_enk,
            cookies,
            zpw_ver=get_zpw_ver(),
        )
        job["groupLinkSource"] = "new"
    else:
        result = get_group_link_detail(
            group_id,
            imei,
            zpw_enk,
            cookies,
            zpw_ver=get_zpw_ver(),
        )
        job["groupLinkSource"] = "detail"

    job["groupLink"] = str(result.get("link") or "").strip()
    job["groupLinkExpirationDate"] = int(result.get("expirationDate") or 0)
    job["groupLinkEnabled"] = int(result.get("enabled") or 0)
    job["groupLinkUpdatedAt"] = datetime.now().isoformat(timespec="seconds")
    return job["groupLink"]


def _friend_request_result(response_json: dict, decoded_raw) -> tuple[bool, int, str]:
    response_json = response_json if isinstance(response_json, dict) else {}
    decoded = _decoded_dict(decoded_raw)
    code = _error_code(response_json, decoded)
    message = _error_message(response_json, decoded)
    return code == 0, code, message


def _api_result(response_json: dict, decoded_raw) -> tuple[bool, int, str]:
    response_json = response_json if isinstance(response_json, dict) else {}
    decoded = _decoded_dict(decoded_raw)
    code = _error_code(response_json, decoded)
    message = _error_message(response_json, decoded)
    return code == 0, code, message


def _campaign_end(job: dict) -> Optional[datetime]:
    return _parse_iso(str(job.get("campaignEndAt") or ""))


def _campaign_expired(job: dict, now: Optional[datetime] = None) -> bool:
    end_at = _campaign_end(job)
    return bool(end_at and (now or datetime.now()) >= end_at)



def _member_map(job: dict) -> dict[str, dict]:
    return {
        str(member.get("userId") or "").strip(): member
        for member in (job.get("members") or [])
        if str(member.get("userId") or "").strip()
    }


def _pending_members(job: dict) -> list[dict]:
    return [
        member for member in (job.get("members") or [])
        if str(member.get("status") or "pending") == "pending"
    ]


def _awaiting_members(job: dict) -> list[dict]:
    return [
        member for member in (job.get("members") or [])
        if str(member.get("status") or "") == "invited"
    ]


def _earliest_iso(values: list[str]) -> str:
    clean = [str(value or "").strip() for value in values if str(value or "").strip()]
    return min(clean) if clean else ""


def _sync_job_schedule(job: dict, now: Optional[datetime] = None) -> None:
    """Đồng bộ trạng thái tác vụ và lần chạy kế tiếp."""
    now = now or datetime.now()
    members = job.get("members") or []
    pending = [m for m in members if m.get("status") == "pending"]
    awaiting = [m for m in members if m.get("status") == "invited"]
    joined = [m for m in members if m.get("status") == "joined"]
    failed = [m for m in members if m.get("status") == "failed"]
    skipped = [m for m in members if m.get("status") == "skipped"]

    if _campaign_expired(job, now):
        job["status"] = "done" if members and len(joined) + len(skipped) == len(members) else "expired"
        job["campaignExpiredAt"] = str(job.get("campaignExpiredAt") or now.isoformat(timespec="seconds"))
        job["nextInviteAt"] = ""
        job["nextVerifyAt"] = ""
        job["nextRunAt"] = ""
        return

    if members and len(joined) + len(skipped) == len(members):
        # Đã đủ thành viên: hoàn thành chiến dịch và dừng toàn bộ lịch kiểm tra.
        job["status"] = "done"
        job["completedAt"] = str(job.get("completedAt") or now.isoformat(timespec="seconds"))
        job["nextInviteAt"] = ""
        job["nextVerifyAt"] = ""
        job["nextRunAt"] = ""
        return

    if pending or awaiting:
        job["status"] = "pending"
        candidates = []
        if pending:
            if not str(job.get("nextInviteAt") or "").strip():
                job["nextInviteAt"] = _next_daily_run(job, now)
            candidates.append(str(job.get("nextInviteAt") or ""))
        else:
            job["nextInviteAt"] = ""

        if str(job.get("targetGroupId") or "").strip():
            if not str(job.get("nextVerifyAt") or "").strip():
                job["nextVerifyAt"] = _next_verify_run(job, now)
            candidates.append(str(job.get("nextVerifyAt") or ""))
        else:
            job["nextVerifyAt"] = ""

        job["nextRunAt"] = _earliest_iso(candidates) or now.isoformat(timespec="seconds")
        return

    job["status"] = "partial" if failed else "done"
    job["nextInviteAt"] = ""
    job["nextVerifyAt"] = ""
    job["nextRunAt"] = ""


def _ensure_joined_target_group(job: dict, zpw_enk: str, cookies: str, imei: str) -> bool:
    """Bảo đảm TÀI KHOẢN ĐANG CHẠY đã ở trong nhóm đích (vào bằng link mời).

    Mỗi tài khoản (chính lẫn phụ) chạy logic y hệt: tự add/đọc/mời trên nhóm đích
    bằng credentials của chính nó. Muốn add/đọc được thì phải là thành viên nhóm,
    nên nếu chưa vào thì tự tham gia qua link một lần. Chủ nhóm (đã tạo/đã có
    nhóm) sẽ được đánh dấu là đã vào ngay. Best-effort: lỗi không làm hỏng run.
    """
    if job.get("accountJoinedTarget") is True:
        return True
    group_id = str(job.get("targetGroupId") or "").strip()
    if not group_id:
        return False
    # Chủ nhóm đích: đã ở trong nhóm sẵn.
    owner_account_id = str(job.get("targetOwnerAccountId") or "").strip()
    current_account_id = str(job.get("accountId") or "").strip()
    if not owner_account_id or owner_account_id == current_account_id:
        job["accountJoinedTarget"] = True
        return True

    link = str(job.get("groupLink") or job.get("targetGroupLink") or "").strip()
    if not link:
        return False
    try:
        result = join_group_by_link(link, zpw_enk, cookies, zpw_ver=get_zpw_ver()) or {}
        msg = str(result.get("message") or "").lower()
        # Coi là thành công nếu ok, hoặc Zalo báo đã là thành viên rồi.
        if result.get("ok") or "đã" in msg or "already" in msg or "member" in msg:
            job["accountJoinedTarget"] = True
            return True
    except Exception as exc:
        print(f"[group_copy_worker] Tài khoản phụ vào nhóm đích thất bại: {exc}", flush=True)
    return False


def _verify_target_members(
    job: dict,
    zpw_enk: str,
    cookies: str,
    imei: str,
    *,
    force: bool = False,
) -> dict:
    """Đọc lại nhóm đích và cập nhật trạng thái thực tế của từng thành viên nguồn."""
    group_id = str(job.get("targetGroupId") or "").strip()
    if not group_id:
        return {"checked": False, "newlyJoined": 0, "leftCount": 0, "targetCount": 0}

    now = datetime.now()
    if not force and not _is_due(str(job.get("nextVerifyAt") or ""), now):
        return {"checked": False, "newlyJoined": 0, "leftCount": 0, "targetCount": int(job.get("targetMemberCount") or 0)}

    # Mỗi tài khoản đọc nhóm đích bằng chính credentials của nó (đã tự vào nhóm).
    try:
        result = get_members_by_group_id(
            group_id=group_id,
            cookie_dict=_cookie_dict(cookies),
            zpw_enk=zpw_enk,
            imei=imei,
            zpw_ver=get_zpw_ver(),
            mcount=500,
            timeout=30,
            max_pages=200,
        ) or {}
        if not result.get("ok"):
            raise RuntimeError(result.get("message") or "Không đọc được danh sách thành viên nhóm đích.")

        target_uids = {
            str(uid or "").strip()
            for uid in (result.get("uids") or [])
            if str(uid or "").strip()
        }
        now_ms = int(time.time() * 1000)
        newly_joined = 0
        left_count = 0

        for member in job.get("members") or []:
            uid = str(member.get("userId") or "").strip()
            if not uid:
                continue
            member["lastVerifiedAt"] = now_ms
            if uid in target_uids:
                if member.get("status") != "joined":
                    newly_joined += 1
                member["status"] = "joined"
                member["joinedAt"] = member.get("joinedAt") or now_ms
                if member.get("friendRequestCreatedByCampaign"):
                    # Chỉ đánh dấu người do chiến dịch vừa kết bạn; họ có thể
                    # vào nhóm qua lần add lại hoặc tự tham gia bằng link.
                    member["joinedAfterCampaignAddRetry"] = True
                member["error"] = ""
                continue

            # Nếu trước đó đã có mặt nhưng hiện không còn trong nhóm, đưa lại về
            # danh sách cần mời để hệ thống bổ sung ở đợt hằng ngày tiếp theo.
            if member.get("status") == "joined":
                left_count += 1
                member["leftDetectedAt"] = now_ms
                member["status"] = "pending"
                member["error"] = "Không còn trong nhóm đích; sẽ được đưa vào đợt mời tiếp theo."
            elif member.get("status") == "invited":
                member["error"] = "Đã gửi lời mời; đang chờ người dùng tham gia nhóm."

        job["targetMemberCount"] = int(result.get("total") or len(target_uids))
        job["lastVerifiedAt"] = now.isoformat(timespec="seconds")
        job["lastVerificationError"] = ""
        job["nextVerifyAt"] = _next_verify_run(job, now)
        return {
            "checked": True,
            "newlyJoined": newly_joined,
            "leftCount": left_count,
            "targetCount": job["targetMemberCount"],
        }
    except Exception as exc:
        job["lastVerificationError"] = str(exc)
        job["nextVerifyAt"] = _next_verify_run(job, now)
        print(f"[group_copy_worker] Verify target group failed: {exc}", flush=True)
        return {
            "checked": False,
            "newlyJoined": 0,
            "leftCount": 0,
            "targetCount": int(job.get("targetMemberCount") or 0),
            "error": str(exc),
        }


def _retry_awaiting_group_adds(
    job: dict,
    zpw_enk: str,
    cookies: str,
    imei: str,
) -> dict:
    """Thử add lại toàn bộ người đã được chiến dịch gửi kết bạn.

    Hàm này không gửi tin nhắn và không tiêu thụ hạn mức kết bạn hằng ngày.
    Trạng thái ``joined`` chỉ được xác nhận sau khi đọc lại danh sách nhóm đích.
    """
    candidates = [
        member for member in _awaiting_members(job)
        if str(member.get("userId") or "").strip()
    ]
    if not candidates:
        return {"attempted": 0}

    member_ids = [str(member.get("userId") or "").strip() for member in candidates]
    # Mỗi tài khoản tự add lại vào nhóm đích bằng credentials của chính nó.
    result = invite_members_to_group(
        str(job.get("targetGroupId") or ""),
        member_ids,
        imei,
        zpw_enk,
        cookies,
        zpw_ver=get_zpw_ver(),
        batch_size=50,
    )
    result_by_uid = {
        str(item.get("userId") or "").strip(): item
        for item in (result.get("members") or [])
        if str(item.get("userId") or "").strip()
    }
    now_ms = int(time.time() * 1000)

    for member in candidates:
        uid = str(member.get("userId") or "").strip()
        item = result_by_uid.get(uid) or {
            "status": "failed",
            "code": -1,
            "delivery": "failed",
            "message": "Không đọc được kết quả add lại vào nhóm.",
        }
        member["campaignAddRetryAttempts"] = int(member.get("campaignAddRetryAttempts") or 0) + 1
        member["campaignAddRetryAt"] = now_ms
        member["attemptedAt"] = now_ms
        member["inviteAttempts"] = int(member.get("inviteAttempts") or 0) + 1
        member["inviteResultCode"] = item.get("code", -1)
        member["inviteResultMessage"] = str(item.get("message") or "").strip()
        member["inviteDelivery"] = str(item.get("delivery") or "")
        member["status"] = "invited"
        member["error"] = str(
            item.get("message")
            or "Đã thử add lại; đang đọc nhóm đích để xác nhận kết quả."
        ).strip()

    return {"attempted": len(candidates)}

def _remove_joined_campaign_friends(
    job: dict,
    zpw_enk: str,
    cookies: str,
    imei: str,
) -> dict:
    """Xóa đúng người do chiến dịch kết bạn và đã add thành công vào nhóm."""
    if not bool(job.get("removeFriendAfterJoin")):
        return {"attempted": 0, "removed": 0, "failed": 0}

    candidates = [
        member for member in (job.get("members") or [])
        if member.get("status") == "joined"
        and bool(member.get("friendRequestCreatedByCampaign"))
        and bool(member.get("joinedAfterCampaignAddRetry"))
        and str(member.get("friendRemoveDelivery") or "") != "removed"
    ]
    removed = 0
    failed = 0
    for member in candidates:
        uid = str(member.get("userId") or "").strip()
        if not uid:
            continue
        member["friendRemoveAttempts"] = int(member.get("friendRemoveAttempts") or 0) + 1
        try:
            response_json, decoded_raw = remove_friend(
                uid,
                imei,
                zpw_enk,
                cookies,
                zpw_ver=get_zpw_ver(),
            )
            success, code, message = _api_result(response_json, decoded_raw)
        except Exception as exc:
            success, code, message = False, -1, str(exc)

        member["friendRemoveCode"] = code
        member["friendRemoveMessage"] = message
        if success:
            member["friendRemoveDelivery"] = "removed"
            member["friendRemovedAt"] = int(time.time() * 1000)
            member["isFriend"] = False
            removed += 1
        else:
            member["friendRemoveDelivery"] = "failed"
            failed += 1
    return {"attempted": len(candidates), "removed": removed, "failed": failed}


class GroupCopyWorker:
    def __init__(self) -> None:
        self.running = False
        self.thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._worker_loop, daemon=True, name="group-copy-worker")
        self.thread.start()
        print("[group_copy_worker] Worker started", flush=True)

    def stop(self) -> None:
        self.running = False

    def _worker_loop(self) -> None:
        while self.running:
            try:
                job = claim_due_job(datetime.now().isoformat(timespec="seconds"))
                if job:
                    self._run_cycle(job)
                    continue
            except Exception as exc:
                print(f"[group_copy_worker] Loop error: {exc}", flush=True)
            time.sleep(5)

    def _run_cycle(self, job: dict) -> None:
        job_id = str(job.get("jobId") or "")
        started_at = datetime.now()
        run_record = {
            "runNo": len(job.get("runs") or []) + 1,
            "startedAt": started_at.isoformat(timespec="seconds"),
            "completedAt": "",
            "status": "running",
            "action": "",
            "memberIds": [],
            "dailyLimit": _daily_limit(job),
            "sentCount": 0,
            "joinedCount": 0,
            "failedCount": 0,
            "newlyJoinedCount": 0,
            "conversionRate": float(job.get("conversionRate") or 0),
            "error": "",
        }
        job.setdefault("runs", []).append(run_record)

        try:
            account = get_account(str(job.get("accountId") or ""))
            if not account:
                raise ValueError("Không tìm thấy tài khoản thực hiện.")
            cookies = str(account.get("cookies") or "").strip()
            zpw_enk = str(account.get("zpwEnk") or "").strip()
            imei = str(account.get("imei") or "").strip()
            if not all([cookies, zpw_enk, imei]):
                raise ValueError("Tài khoản chưa đủ cookies, zpwEnk hoặc IMEI.")

            # Tài khoản phụ trong nhóm chung: chờ tài khoản chủ tạo xong nhóm đích
            # rồi mượn Group ID + link, không tạo nhóm riêng.
            shared_from = str(job.get("sharedTargetFromJobId") or "").strip()
            if shared_from and not str(job.get("targetGroupId") or "").strip():
                master = get_job(shared_from)
                master_gid = str((master or {}).get("targetGroupId") or "").strip()
                if master_gid:
                    job["targetGroupId"] = master_gid
                    job["targetGroupName"] = str((master or {}).get("targetGroupName") or master_gid)
                    job["groupLink"] = str((master or {}).get("groupLink") or "")
                    job["groupLinkEnabled"] = 1 if job["groupLink"] else 0
                    job["targetMode"] = "existing"
                    job["lastNotice"] = "Đã nhận nhóm đích chung từ tài khoản chính."
                else:
                    # Chủ chưa tạo nhóm xong: hoãn ~2 phút rồi thử lại.
                    run_record["action"] = "wait_shared_target"
                    run_record["status"] = "done"
                    job["status"] = "pending"
                    job["nextRunAt"] = (started_at + timedelta(minutes=2)).isoformat(timespec="seconds")
                    job["lastNotice"] = "Đang chờ tài khoản chính tạo nhóm đích chung..."
                    run_record["completedAt"] = datetime.now().isoformat(timespec="seconds")
                    save_job(job)
                    return

            if _campaign_expired(job, started_at):
                run_record["action"] = "campaign_expired"
                run_record["status"] = "done"
                job["lastNotice"] = "Chiến dịch đã hết thời gian chạy; hệ thống dừng gửi kết bạn và kiểm tra lại nhóm."
                _sync_job_schedule(job, started_at)
                run_record["completedAt"] = datetime.now().isoformat(timespec="seconds")
                save_job(job)
                return

            # Bảo đảm tài khoản đang chạy đã ở trong nhóm đích (vào bằng link) để
            # tự add/đọc/mời bằng chính credentials của nó — logic y hệt tài khoản chính.
            if str(job.get("targetGroupId") or "").strip():
                _ensure_joined_target_group(job, zpw_enk, cookies, imei)

            # Chỉ coi đợt mời là đến hạn dựa trên danh sách pending trước lúc
            # kiểm tra. Nếu lần kiểm tra phát hiện ai vừa rời nhóm, người đó được
            # đưa vào đợt hằng ngày kế tiếp, không bị mời lại ngay trong cùng chu kỳ.
            pending_before_verify = _pending_members(job)
            invite_due = bool(pending_before_verify) and _is_due(
                str(job.get("nextInviteAt") or ""), started_at
            )

            # Mỗi chu kỳ cấu hình, thử add lại toàn bộ người đã được chiến dịch
            # gửi kết bạn, sau đó đọc nhóm đích để xác nhận trạng thái thực tế.
            pre_verify = {"checked": False, "newlyJoined": 0, "leftCount": 0}
            if str(job.get("targetGroupId") or "").strip() and (
                _is_due(str(job.get("nextVerifyAt") or ""), started_at)
                or invite_due
            ):
                retry_result = _retry_awaiting_group_adds(
                    job,
                    zpw_enk,
                    cookies,
                    imei,
                )
                pre_verify = _verify_target_members(job, zpw_enk, cookies, imei, force=True)
                remove_result = _remove_joined_campaign_friends(job, zpw_enk, cookies, imei)
                run_record["retryAddCount"] = int(retry_result.get("attempted") or 0)
                run_record["friendRemovedCount"] = int(remove_result.get("removed") or 0)
                run_record["friendRemoveFailedCount"] = int(remove_result.get("failed") or 0)

            if invite_due:
                self._send_daily_batch(job, run_record, zpw_enk, cookies, imei, started_at)
            else:
                run_record["action"] = "retry_add_verify_and_cleanup"
                verify_result = pre_verify
                if not verify_result.get("checked") and str(job.get("targetGroupId") or "").strip():
                    verify_result = _verify_target_members(job, zpw_enk, cookies, imei, force=True)
                run_record["newlyJoinedCount"] = int(verify_result.get("newlyJoined") or 0)
                run_record["status"] = "done" if not verify_result.get("error") else "partial"
                run_record["error"] = str(verify_result.get("error") or "")
                prefix = (
                    f"Đã thử add lại {int(run_record.get('retryAddCount') or 0)} người đang chờ; "
                    f"xóa kết bạn {int(run_record.get('friendRemovedCount') or 0)} người theo thiết lập."
                )
                job["lastNotice"] = self._progress_notice(job, prefix=prefix)

            _sync_job_schedule(job, datetime.now())
            run_record["completedAt"] = datetime.now().isoformat(timespec="seconds")
            # save_job sẽ tính lại joinedCount/conversionRate từ danh sách thành viên.
            saved = save_job(job)
            run_record["joinedCount"] = int(saved.get("joinedCount") or 0)
            run_record["conversionRate"] = float(saved.get("conversionRate") or 0)
            # Ghi lại số liệu cuối cùng của run_record sau lần save đầu.
            save_job(saved)
            print(
                f"[group_copy_worker] {job_id}: action={run_record.get('action')} "
                f"sent={run_record.get('sentCount')} joined={saved.get('joinedCount')} "
                f"conversion={saved.get('conversionRate')}% status={saved.get('status')}",
                flush=True,
            )
        except Exception as exc:
            run_record["completedAt"] = datetime.now().isoformat(timespec="seconds")
            run_record["status"] = "failed"
            run_record["error"] = str(exc)
            job["status"] = "failed"
            job["lastError"] = str(exc)
            job["lastNotice"] = ""
            save_job(job)
            print(f"[group_copy_worker] {job_id} failed: {exc}", flush=True)

    def _send_daily_batch(
        self,
        job: dict,
        run_record: dict,
        zpw_enk: str,
        cookies: str,
        imei: str,
        started_at: datetime,
    ) -> None:
        pending = _pending_members(job)
        if not pending:
            run_record["action"] = "verify_target"
            run_record["status"] = "done"
            return

        target_mode = str(job.get("targetMode") or "existing")
        creating_new_group = target_mode == "new" and not str(job.get("targetGroupId") or "").strip()
        run_record["memberIds"] = [str(member.get("userId") or "").strip() for member in pending]
        run_record["friendRequestDailyLimit"] = _daily_limit(job)
        run_record["directInviteCount"] = 0
        run_record["friendRequestCount"] = 0
        run_record["friendRequestFailedCount"] = 0

        if creating_new_group:
            group_name = str(job.get("newGroupName") or job.get("targetGroupName") or "Nhóm mới").strip()
            # Ưu tiên thành viên đã xác định là bạn bè để tăng khả năng tạo nhóm
            # và thêm trực tiếp ngay ở request đầu tiên.
            prioritized = sorted(
                [member for member in pending if member.get("isFriend") is not False],
                key=lambda member: 0 if member.get("isFriend") is True else 1,
            )
            seed_ids = [
                str(member.get("userId") or "").strip()
                for member in prioritized[:50]
                if str(member.get("userId") or "").strip()
            ]
            response_json, decoded_raw = create_group(
                group_name,
                seed_ids,
                zpw_enk,
                cookies,
                imei,
                zpw_ver=get_zpw_ver(),
            )
            response_json = response_json if isinstance(response_json, dict) else {}
            decoded = _decoded_dict(decoded_raw)
            group_id = _extract_group_id(response_json, decoded)
            error_code = _error_code(response_json, decoded)
            error_message = _error_message(response_json, decoded)
            if not group_id:
                raise RuntimeError(
                    error_message
                    or (
                        f"Zalo trả về mã lỗi {error_code} khi tạo nhóm mới."
                        if error_code != 0
                        else "Tạo nhóm thành công nhưng không đọc được Group ID."
                    )
                )

            job["targetGroupId"] = group_id
            job["targetGroupName"] = str(job.get("newGroupName") or job.get("targetGroupName") or group_id)
            job["groupCreatedAt"] = datetime.now().isoformat(timespec="seconds")
            run_record["action"] = "create_group_get_link_and_copy"
            group_link = _ensure_group_link(
                job,
                zpw_enk,
                cookies,
                imei,
                newly_created=True,
                force=True,
            )
            # Xác nhận ngay những người đã được thêm trong request tạo nhóm.
            _verify_target_members(job, zpw_enk, cookies, imei, force=True)
        else:
            target_group_id = str(job.get("targetGroupId") or "").strip()
            if not target_group_id:
                raise ValueError("Tác vụ chưa có nhóm đích.")
            run_record["action"] = "get_link_invite_friends_and_send_friend_requests"
            group_link = _ensure_group_link(
                job,
                zpw_enk,
                cookies,
                imei,
                newly_created=False,
            )

        run_record["targetGroupId"] = str(job.get("targetGroupId") or "")
        run_record["groupLink"] = group_link

        # Sau bước tạo nhóm/kiểm tra ban đầu, chỉ xử lý những người chưa có mặt.
        # Giãn nhịp: mỗi run chỉ mời TRỰC TIẾP 1 người là bạn bè (và chưa thử mời
        # quá 2 lần) để tránh add dồn dập; người còn lại xử lý ở các run kế tiếp.
        direct_candidates = [
            member for member in _pending_members(job)
            if member.get("isFriend") is True and int(member.get("inviteAttempts") or 0) < 2
        ]
        direct_ids = [
            str(member.get("userId") or "").strip()
            for member in direct_candidates[:1]
            if str(member.get("userId") or "").strip()
        ]
        direct_result = {
            "members": [],
            "acceptedCount": 0,
            "failedCount": 0,
        }
        if direct_ids:
            # Mỗi tài khoản tự mời trực tiếp vào nhóm bằng credentials của chính nó
            # (đã tự vào nhóm đích trước đó).
            direct_result = invite_members_to_group(
                str(job.get("targetGroupId") or ""),
                direct_ids,
                imei,
                zpw_enk,
                cookies,
                zpw_ver=get_zpw_ver(),
                batch_size=50,
            )

        attempted_at = int(time.time() * 1000)
        members_by_uid = _member_map(job)
        direct_by_uid = {
            str(item.get("userId") or ""): item
            for item in (direct_result.get("members") or [])
            if str(item.get("userId") or "").strip()
        }
        for uid in direct_ids:
            member = members_by_uid.get(uid)
            if not member:
                continue
            result = direct_by_uid.get(uid) or {
                "status": "failed",
                "code": -1,
                "delivery": "failed",
                "message": "Không đọc được kết quả thêm trực tiếp vào nhóm.",
            }
            member["attemptedAt"] = attempted_at
            member["inviteAttempts"] = int(member.get("inviteAttempts") or 0) + 1
            member["inviteResultCode"] = result.get("code", -1)
            member["inviteResultMessage"] = str(result.get("message") or "").strip()
            member["inviteDelivery"] = str(result.get("delivery") or "")
            # Chưa đánh dấu invited ở đây. Chỉ khi kiểm tra thấy UID có trong nhóm
            # mới coi là thêm trực tiếp thành công; nếu chưa có sẽ gửi kết bạn
            # theo hạn mức hằng ngày và thử add lại theo chu kỳ.
            if str(result.get("status") or "") == "joined":
                member["status"] = "joined"
                member["joinedAt"] = member.get("joinedAt") or attempted_at
                member["error"] = ""
            else:
                member["status"] = "pending"
                member["error"] = str(result.get("message") or "Chưa thêm trực tiếp được vào nhóm.").strip()

        run_record["directInviteCount"] = len(direct_ids)
        verify_after_direct = _verify_target_members(job, zpw_enk, cookies, imei, force=True)
        run_record["newlyJoinedCount"] = int(verify_after_direct.get("newlyJoined") or 0)

        remaining_quota = _remaining_friend_requests_today(job, started_at)
        friend_request_sent = 0
        friend_request_failed = 0
        quota_deferred = 0
        friend_text = _friend_request_text(job, group_link)

        # Giãn nhịp chống spam: chỉ khi run này CHƯA mời trực tiếp ai mới gửi 1 lời
        # mời kết bạn kèm link nhóm cho MỘT người chưa vào nhóm. Người còn lại chờ
        # run kế tiếp (mỗi tài khoản cách nhau ngẫu nhiên 5–20 phút).
        if run_record["directInviteCount"] == 0 and remaining_quota > 0:
            target = next(
                (m for m in _pending_members(job) if str(m.get("userId") or "").strip()),
                None,
            )
            if target is not None:
                uid = str(target.get("userId") or "").strip()
                _consume_friend_request_slot(job, started_at)
                remaining_quota -= 1
                target["friendRequestAttempts"] = int(target.get("friendRequestAttempts") or 0) + 1
                target["friendRequestAt"] = int(time.time() * 1000)
                try:
                    response_json, decoded_raw = send_friend_request(
                        toid=uid,
                        msg=friend_text,
                        imei=imei,
                        zpw_enk=zpw_enk,
                        cookies=cookies,
                        zpw_ver=get_zpw_ver(),
                    )
                    success, code, message = _friend_request_result(response_json, decoded_raw)
                except Exception as exc:
                    success, code, message = False, -1, str(exc)

                target["friendRequestCode"] = code
                target["friendRequestMessage"] = message
                if success:
                    target["status"] = "invited"
                    target["invitedAt"] = target.get("invitedAt") or target["friendRequestAt"]
                    target["friendRequestDelivery"] = "friend_request_with_group_link"
                    target["friendRequestCreatedByCampaign"] = True
                    target["friendRemoveAttempts"] = 0
                    target["friendRemovedAt"] = 0
                    target["friendRemoveCode"] = -1
                    target["friendRemoveMessage"] = ""
                    target["friendRemoveDelivery"] = ""
                    target["joinedAfterCampaignAddRetry"] = False
                    target["error"] = (
                        "Đã gửi lời mời kết bạn. Hệ thống sẽ thử add lại vào nhóm "
                        f"mỗi {_verify_interval(job)} phút."
                    )
                    friend_request_sent += 1
                else:
                    target["status"] = "pending"
                    target["friendRequestDelivery"] = "failed"
                    target["error"] = message or "Zalo từ chối gửi lời mời kết bạn."
                    friend_request_failed += 1

        run_record["friendRequestCount"] = friend_request_sent
        run_record["friendRequestFailedCount"] = friend_request_failed
        run_record["dailyFriendRequestCount"] = int(job.get("dailyFriendRequestCount") or 0)
        run_record["sentCount"] = friend_request_sent
        run_record["failedCount"] = friend_request_failed
        run_record["deferredCount"] = quota_deferred
        run_record["status"] = "done" if friend_request_failed == 0 else "partial"
        run_record["error"] = (
            "" if friend_request_failed == 0
            else f"Có {friend_request_failed} lời mời kết bạn chưa gửi được; hệ thống sẽ thử lại vào đợt sau."
        )

        # Lịch gửi kế tiếp: còn người chưa vào nhóm thì hẹn cách ngẫu nhiên 5–20
        # phút (giãn nhịp/tài khoản). Nếu đã hết hạn mức kết bạn hôm nay và không
        # còn bạn bè để mời trực tiếp thì lùi sang giờ chạy ngày kế tiếp.
        pending_left = _pending_members(job)
        if pending_left:
            now_after = datetime.now()
            remaining_quota_now = _remaining_friend_requests_today(job, now_after)
            friends_left = any(
                m.get("isFriend") is True and int(m.get("inviteAttempts") or 0) < 2
                for m in pending_left
            )
            if friends_left or remaining_quota_now > 0:
                job["nextInviteAt"] = _next_send_run(job, now_after)
            else:
                job["nextInviteAt"] = _next_daily_run(job, now_after)
        else:
            job["nextInviteAt"] = ""

        # Kiểm tra lần cuối để cập nhật trường hợp thành viên vừa vào nhóm sau khi
        # nhận lời mời trực tiếp hoặc tự mở link trong lời mời kết bạn.
        final_verify = _verify_target_members(job, zpw_enk, cookies, imei, force=True)
        run_record["newlyJoinedCount"] += int(final_verify.get("newlyJoined") or 0)
        remove_result = _remove_joined_campaign_friends(job, zpw_enk, cookies, imei)
        run_record["friendRemovedCount"] = int(run_record.get("friendRemovedCount") or 0) + int(remove_result.get("removed") or 0)
        run_record["friendRemoveFailedCount"] = int(run_record.get("friendRemoveFailedCount") or 0) + int(remove_result.get("failed") or 0)
        job["lastError"] = ""
        prefix = (
            f"Đã tạo nhóm, kích hoạt link và thử thêm trực tiếp {len(direct_ids)} người"
            if creating_new_group
            else f"Đã lấy link nhóm và thử thêm trực tiếp {len(direct_ids)} người"
        )
        prefix += f"; đã gửi {friend_request_sent}/{_daily_limit(job)} lời mời kết bạn hôm nay"
        if quota_deferred:
            prefix += f", còn {quota_deferred} người chờ hạn mức ngày kế tiếp"
        prefix += "."
        job["lastNotice"] = self._progress_notice(job, prefix=prefix)

    @staticmethod
    def _progress_notice(job: dict, prefix: str = "") -> str:
        total = len(job.get("members") or [])
        joined = sum(1 for m in (job.get("members") or []) if m.get("status") == "joined")
        invited = sum(1 for m in (job.get("members") or []) if m.get("status") == "invited")
        pending = sum(1 for m in (job.get("members") or []) if m.get("status") == "pending")
        rate = round((joined * 100 / total), 1) if total else 0.0
        parts = [prefix.strip()] if prefix.strip() else []
        parts.append(
            f"Thực tế đã vào nhóm {joined}/{total} người ({rate}%). "
            f"Đang chờ chấp nhận {invited}, chưa gửi {pending}."
        )
        if job.get("lastVerificationError"):
            parts.append(f"Lần kiểm tra gần nhất chưa thành công: {job.get('lastVerificationError')}")
        else:
            parts.append(f"Hệ thống sẽ kiểm tra lại mỗi {_verify_interval(job)} phút.")
        return " ".join(parts)


_WORKER: Optional[GroupCopyWorker] = None


def start_group_copy_worker() -> None:
    global _WORKER
    recovered = recover_running_jobs()
    if recovered:
        print(f"[group-copy-worker] Đã khôi phục {recovered} tác vụ đang chạy dở.", flush=True)
    if _WORKER is None:
        _WORKER = GroupCopyWorker()
    _WORKER.start()


def stop_group_copy_worker() -> None:
    if _WORKER:
        _WORKER.stop()
