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
from features.schedules.schedule_manager import load_schedules, update_schedule, append_schedule_result

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

        # Validate
        if not recipients:
            print(f"[schedule_worker] Schedule {sch_id}: No recipients")
            update_schedule(sch_id, {"status": "failed"})
            return

        if not message:
            print(f"[schedule_worker] Schedule {sch_id}: Empty message")
            update_schedule(sch_id, {"status": "failed"})
            return

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

        # Handle personal groups
        if schedule_source == "personal-groups":
            self._run_schedule_groups(sch_id, account, recipients, message, rate_limit, batch_config, update_schedule, append_schedule_result)
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
                    print(f"[schedule_worker] Sending to {uid} ({zalo_name})...")
                    _, decoded = send_sms(cookies, zpw_enk, uid, imei, message, zpw_ver=zpw_ver)

                    error_code = decoded.get("error_code", -1) if isinstance(decoded, dict) else -1

                    if error_code == 0:
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
                            "error": f"Error code: {error_code}",
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
    
    def _run_schedule_groups(self, sch_id, account, recipients, message, rate_limit, batch_config, update_schedule, append_schedule_result):
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
                    print(f"[schedule_worker] Sending to group {group_id}...")
                    response_json, decoded_data = send_group_msg(
                        cookies, zpw_enk, group_id, imei, message,
                        zpw_ver=zpw_ver
                    )

                    error_code = decoded_data.get("error_code", -1) if isinstance(decoded_data, dict) else -1

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
