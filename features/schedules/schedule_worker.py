"""
Background worker để chạy lịch gửi tin nhắn.
Chạy trong thread riêng, mỗi 5 giây check có lịch nào cần chạy không.
"""
import threading
import time
import json
import random
import os
from datetime import datetime
from typing import Optional

from core.zalo.zalo_config import get_zpw_ver
from features.accounts.account_manager import load_accounts
from features.groups.send_sms_group import send_group_msg
from features.messaging.send_sms import send_sms
from features.messaging.send_photo import send_photo
from features.messaging.send_link import extract_zalo_group_link, resolve_group_link_info, send_message_smart
from features.schedules.schedule_manager import DATA_DIR, load_schedules, update_schedule, append_schedule_result


def _prepare_link_info(message: str, zpw_enk: str, cookies: str, zpw_ver: str, imei: str = ""):
    """Nếu nội dung chứa link nhóm Zalo: resolve thông tin nhóm MỘT lần cho cả
    lịch để mọi người nhận dùng chung, tránh gọi parselink/ginfo lặp lại."""
    link = extract_zalo_group_link(message)
    if not link:
        return None
    try:
        info = resolve_group_link_info(link, zpw_enk, cookies, zpw_ver=zpw_ver, imei=imei)
        if info.get("ok"):
            print(f"[schedule_worker] Nội dung chứa link nhóm '{info.get('title')}' -> gửi dạng link card", flush=True)
            return info
        print(f"[schedule_worker] Không lấy được info nhóm từ link: {info.get('message')}", flush=True)
    except Exception as e:
        print(f"[schedule_worker] Lỗi resolve link nhóm: {e}", flush=True)
    return None


def _load_schedule_photo(schedule: dict):
    """Đọc bytes ảnh đính kèm của lịch (nếu có). Trả (bytes|None, tên file)."""
    photo_path = str(schedule.get("photoPath") or "").strip()
    if not photo_path:
        return None, ""
    abs_path = photo_path if os.path.isabs(photo_path) else os.path.join(DATA_DIR, photo_path)
    try:
        with open(abs_path, "rb") as f:
            data = f.read()
        if not data:
            raise ValueError("file rỗng")
        name = schedule.get("photoName") or os.path.basename(abs_path)
        return data, name
    except Exception as e:
        print(f"[schedule_worker] Không đọc được ảnh đính kèm '{abs_path}': {e}")
        return None, ""

class ScheduleWorker:
    def __init__(self):
        self.running = False
        self.thread = None
        
    def start(self):
        """Start worker thread."""
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._worker_loop, daemon=True)
        self.thread.start()
        print("[schedule_worker] Worker started")
        
    def stop(self):
        """Stop worker thread."""
        self.running = False
        
    def _worker_loop(self):
        """Main worker loop: check every 5 seconds."""
        print("[schedule_worker] Worker loop started")
        while self.running:
            try:
                self._check_and_run_schedules()
            except Exception as e:
                print(f"[schedule_worker] Error in loop: {e}")
            time.sleep(5)
            
    def _check_and_run_schedules(self):
        """Kiểm tra và chạy lịch cần chạy."""
        schedules = load_schedules()
        now = datetime.now().isoformat()
        
        for sch in schedules:
            sch_id = sch.get("scheduleId")
            status = sch.get("status")
            run_at = sch.get("runAt", "")
            
            # Tìm lịch cần chạy: pending + time <= now
            if status == "pending" and run_at and run_at <= now:
                print(f"[schedule_worker] Running schedule: {sch_id}")
                self._run_schedule(sch)
                
    def _run_schedule(self, schedule: dict):
        """Chạy một lịch gửi tin - xử lý theo batch."""
        sch_id = schedule.get("scheduleId")
        account_id = schedule.get("accountId")
        message = schedule.get("message", "")
        recipients = schedule.get("recipients", [])
        rate_limit = schedule.get("rateLimit", {})
        batch_config = schedule.get("batchConfig", {})
        schedule_source = schedule.get("source", "")  # 'group', 'phone', or 'personal-groups'

        # Ảnh đính kèm (tùy chọn): đọc 1 lần cho cả lịch.
        photo_bytes, photo_name = _load_schedule_photo(schedule)

        # Validate
        if not recipients:
            print(f"[schedule_worker] Schedule {sch_id}: No recipients")
            update_schedule(sch_id, {"status": "failed"})
            return

        if not message and photo_bytes is None:
            print(f"[schedule_worker] Schedule {sch_id}: Empty message and no photo")
            update_schedule(sch_id, {"status": "failed"})
            return

        if schedule.get("photoPath") and photo_bytes is None:
            # Lịch có khai báo ảnh nhưng file mất/hỏng: nếu còn text thì gửi
            # text-only kèm cảnh báo, nếu không thì fail rõ ràng.
            if not message:
                print(f"[schedule_worker] Schedule {sch_id}: Photo file missing and no message -> failed")
                update_schedule(sch_id, {"status": "failed"})
                return
            print(f"[schedule_worker] Schedule {sch_id}: Photo file missing, fallback to text-only")

        # Lấy account
        accounts = load_accounts()
        account = next((a for a in accounts if a.get("accountId") == account_id), None)
        if not account:
            print(f"[schedule_worker] Schedule {sch_id}: Account not found")
            update_schedule(sch_id, {"status": "failed"})
            return

        cookies = account.get("cookies", "")
        zpw_enk = account.get("zpwEnk", "")
        imei = account.get("imei", "")
        sender_uid = account.get("uid", "")
        zpw_ver = get_zpw_ver()

        if not all([cookies, zpw_enk, imei]):
            print(f"[schedule_worker] Schedule {sch_id}: Missing account credentials")
            update_schedule(sch_id, {"status": "failed"})
            return

        # Batch config
        batch_size = batch_config.get("batchSize", 10)
        batch_delay_sec = batch_config.get("batchDelaySec", 30)

        # Rate limit config
        min_delay = rate_limit.get("minDelaySec", 3)
        max_delay = rate_limit.get("maxDelaySec", 5)
        max_errors = rate_limit.get("maxConsecutiveErrors", 5)

        # Nội dung chứa link nhóm Zalo -> resolve info nhóm 1 lần cho cả lịch.
        link_info = _prepare_link_info(message, zpw_enk, cookies, zpw_ver, imei=imei)

        # Handle personal groups
        if schedule_source == "personal-groups":
            self._run_schedule_groups(sch_id, account, recipients, message, rate_limit, batch_config, update_schedule, append_schedule_result,
                                      photo_bytes=photo_bytes, photo_name=photo_name, link_info=link_info)
            return

        # Handle individual users (group or phone source)
        # ⚠️ LỌC BỎ NGƯỜI GỬI TỪ DANH SÁCH NHẬN
        filtered_recipients = [r for r in recipients if r.get("userId") != sender_uid]

        if not filtered_recipients:
            print(f"[schedule_worker] Schedule {sch_id}: No valid recipients after filtering sender")
            update_schedule(sch_id, {"status": "failed"})
            return

        if len(filtered_recipients) < len(recipients):
            print(f"[schedule_worker] Schedule {sch_id}: Filtered out {len(recipients) - len(filtered_recipients)} sender account(s)")

        # Đổi status => running
        update_schedule(sch_id, {"status": "running"})

        success_count = 0
        failed_count = 0
        consecutive_errors = 0

        # Xử lý theo batch
        for batch_start in range(0, len(filtered_recipients), batch_size):
            batch = filtered_recipients[batch_start:batch_start + batch_size]
            batch_num = batch_start // batch_size + 1
            total_batches = (len(filtered_recipients) + batch_size - 1) // batch_size

            print(f"[schedule_worker] Schedule {sch_id}: Batch {batch_num}/{total_batches} - gửi {len(batch)} tin nhắn")

            for i, recipient in enumerate(batch):
                if consecutive_errors >= max_errors:
                    print(f"[schedule_worker] Schedule {sch_id}: Max consecutive errors reached")
                    break

                uid = recipient.get("userId")
                zalo_name = recipient.get("zaloName", "")
                avatar = recipient.get("avatar", "")

                try:
                    if photo_bytes is not None:
                        # Gửi ảnh TRƯỚC (không caption), rồi gửi text thành tin riêng sau.
                        print(f"[schedule_worker] Sending photo to {uid} ({zalo_name})...")
                        photo_result = send_photo(
                            photo_bytes, photo_name or "image.jpg", uid,
                            zpw_enk, cookies, imei,
                            desc="", is_group=False, zpw_ver=zpw_ver,
                        )
                        sent_ok = bool(photo_result.get("ok"))
                        send_error = photo_result.get("message", "Gửi ảnh thất bại")
                        if sent_ok and message:
                            text_result = send_message_smart(
                                uid, message, zpw_enk, cookies, imei,
                                is_group=False, zpw_ver=zpw_ver, link_info=link_info,
                            )
                            if not text_result.get("ok"):
                                sent_ok = False
                                send_error = f"Ảnh đã gửi nhưng text lỗi: {text_result.get('error')}"
                    else:
                        print(f"[schedule_worker] Sending to {uid} ({zalo_name})...")
                        text_result = send_message_smart(
                            uid, message, zpw_enk, cookies, imei,
                            is_group=False, zpw_ver=zpw_ver, link_info=link_info,
                        )
                        sent_ok = bool(text_result.get("ok"))
                        send_error = text_result.get("error", "")

                    if sent_ok:
                        success_count += 1
                        consecutive_errors = 0
                        result = {
                            "userId": uid,
                            "zaloName": zalo_name,
                            "avatar": avatar,
                            "status": "success",
                            "sentAt": int(time.time() * 1000)
                        }
                    else:
                        failed_count += 1
                        consecutive_errors += 1
                        result = {
                            "userId": uid,
                            "zaloName": zalo_name,
                            "avatar": avatar,
                            "status": "failed",
                            "error": send_error,
                            "sentAt": int(time.time() * 1000)
                        }

                    append_schedule_result(sch_id, result)

                except Exception as e:
                    failed_count += 1
                    consecutive_errors += 1
                    result = {
                        "userId": uid,
                        "zaloName": zalo_name,
                        "avatar": avatar,
                        "status": "failed",
                        "error": str(e),
                        "sentAt": int(time.time() * 1000)
                    }
                    append_schedule_result(sch_id, result)
                    print(f"[schedule_worker] Error sending to {uid}: {e}")

                # Delay giữa các tin nhắn trong cùng batch
                if i < len(batch) - 1:
                    delay = random.uniform(min_delay, max_delay)
                    time.sleep(delay)

            # Delay giữa các batch (trừ batch cuối cùng)
            if batch_start + batch_size < len(filtered_recipients) and consecutive_errors < max_errors:
                print(f"[schedule_worker] Schedule {sch_id}: Batch {batch_num} done, nghỉ {batch_delay_sec}s trước batch tiếp theo...")
                time.sleep(batch_delay_sec)

        # Xác định status cuối cùng
        if success_count == len(filtered_recipients):
            final_status = "done"
        elif success_count > 0:
            final_status = "partial"
        else:
            final_status = "failed"

        print(f"[schedule_worker] Schedule {sch_id} completed: {success_count} success, {failed_count} failed -> {final_status}")
        update_schedule(sch_id, {"status": final_status})
    
    def _run_schedule_groups(self, sch_id, account, recipients, message, rate_limit, batch_config, update_schedule, append_schedule_result,
                             photo_bytes=None, photo_name="", link_info=None):
        """Run schedule for personal groups - xử lý theo batch."""
        cookies = account.get("cookies", "")
        zpw_enk = account.get("zpwEnk", "")
        imei = account.get("imei", "")
        zpw_ver = get_zpw_ver()

        if not all([cookies, zpw_enk, imei]):
            print(f"[schedule_worker] Schedule {sch_id}: Missing account credentials for groups")
            update_schedule(sch_id, {"status": "failed"})
            return

        # Batch config
        batch_size = batch_config.get("batchSize", 10)
        batch_delay_sec = batch_config.get("batchDelaySec", 30)

        # Rate limit config
        min_delay = rate_limit.get("minDelaySec", 3)
        max_delay = rate_limit.get("maxDelaySec", 5)
        max_errors = rate_limit.get("maxConsecutiveErrors", 5)

        # Đổi status => running
        update_schedule(sch_id, {"status": "running"})

        success_count = 0
        failed_count = 0
        consecutive_errors = 0

        # Xử lý theo batch
        for batch_start in range(0, len(recipients), batch_size):
            batch = recipients[batch_start:batch_start + batch_size]
            batch_num = batch_start // batch_size + 1
            total_batches = (len(recipients) + batch_size - 1) // batch_size

            print(f"[schedule_worker] Schedule {sch_id}: Batch {batch_num}/{total_batches} - gửi {len(batch)} nhóm")

            for i, recipient in enumerate(batch):
                if consecutive_errors >= max_errors:
                    print(f"[schedule_worker] Schedule {sch_id}: Max consecutive errors reached")
                    break

                group_id = recipient.get("groupId")
                if not group_id:
                    print(f"[schedule_worker] Schedule {sch_id}: Missing groupId in recipient")
                    continue

                try:
                    if photo_bytes is not None:
                        # Gửi ảnh vào nhóm TRƯỚC (không caption), rồi gửi text thành tin riêng.
                        print(f"[schedule_worker] Sending photo to group {group_id}...")
                        photo_result = send_photo(
                            photo_bytes, photo_name or "image.jpg", group_id,
                            zpw_enk, cookies, imei,
                            desc="", is_group=True, zpw_ver=zpw_ver,
                        )
                        error_code = 0 if photo_result.get("ok") else -1
                        if error_code != 0:
                            print(f"[schedule_worker] Photo to group {group_id} failed: {photo_result.get('message')}")
                        elif message:
                            text_result = send_message_smart(
                                group_id, message, zpw_enk, cookies, imei,
                                is_group=True, zpw_ver=zpw_ver, link_info=link_info,
                            )
                            error_code = 0 if text_result.get("ok") else -1
                            if error_code != 0:
                                print(f"[schedule_worker] Text after photo to group {group_id} failed: {text_result.get('error')}")
                    else:
                        print(f"[schedule_worker] Sending to group {group_id}...")
                        text_result = send_message_smart(
                            group_id, message, zpw_enk, cookies, imei,
                            is_group=True, zpw_ver=zpw_ver, link_info=link_info,
                        )
                        error_code = 0 if text_result.get("ok") else -1

                    if error_code == 0:
                        success_count += 1
                        consecutive_errors = 0
                        result = {
                            "groupId": group_id,
                            "status": "success",
                            "sentAt": int(time.time() * 1000)
                        }
                    else:
                        failed_count += 1
                        consecutive_errors += 1
                        result = {
                            "groupId": group_id,
                            "status": "failed",
                            "error": f"Error code: {error_code}",
                            "sentAt": int(time.time() * 1000)
                        }

                    append_schedule_result(sch_id, result)

                except Exception as e:
                    failed_count += 1
                    consecutive_errors += 1
                    result = {
                        "groupId": group_id,
                        "status": "failed",
                        "error": str(e),
                        "sentAt": int(time.time() * 1000)
                    }
                    append_schedule_result(sch_id, result)
                    print(f"[schedule_worker] Error sending to group {group_id}: {e}")

                # Delay giữa các tin nhắn trong cùng batch
                if i < len(batch) - 1:
                    delay = random.uniform(min_delay, max_delay)
                    time.sleep(delay)

            # Delay giữa các batch (trừ batch cuối cùng)
            if batch_start + batch_size < len(recipients) and consecutive_errors < max_errors:
                print(f"[schedule_worker] Schedule {sch_id}: Batch {batch_num} done, nghỉ {batch_delay_sec}s trước batch tiếp theo...")
                time.sleep(batch_delay_sec)

        # Xác định status cuối cùng
        if success_count == len(recipients):
            final_status = "done"
        elif success_count > 0:
            final_status = "partial"
        else:
            final_status = "failed"

        print(f"[schedule_worker] Schedule {sch_id} completed: {success_count} success, {failed_count} failed -> {final_status}")
        update_schedule(sch_id, {"status": final_status})

# Global worker instance
_worker: Optional[ScheduleWorker] = None

def start_schedule_worker():
    """Khởi động worker."""
    global _worker
    if _worker is None:
        _worker = ScheduleWorker()
    _worker.start()

def stop_schedule_worker():
    """Dừng worker."""
    global _worker
    if _worker:
        _worker.stop()
