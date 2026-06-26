"""
Task Manager - Quản lý background tasks/jobs để cho phép các tác vụ nặng chạy song song.
- Các heavy operations (fetch members, lookup phones, etc.) chạy ở background threads
- Frontend nhận task_id ngay tức thì, không phải chờ hoàn thành
- Kết quả được stream qua SSE hoặc fetch qua polling endpoint
"""
import threading
import queue
import json
import uuid
import time
from datetime import datetime
from typing import Callable, Optional, Dict, Any

# Global task registry
_TASKS = {}
_TASKS_LOCK = threading.RLock()

# SSE broadcast function (sẽ được set từ app.py)
_sse_broadcast_func = None


class TaskStatus:
    """Enum for task status"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Task:
    """Represents a background task"""
    
    def __init__(self, task_id: str, name: str, description: str = ""):
        self.task_id = task_id
        self.name = name
        self.description = description
        self.status = TaskStatus.PENDING
        self.progress = 0  # 0-100
        self.result = None
        self.error = None
        self.created_at = datetime.now().isoformat()
        self.started_at = None
        self.completed_at = None
        self._log_messages = []
        self._thread = None
    
    def to_dict(self) -> dict:
        """Convert task to dictionary"""
        return {
            "taskId": self.task_id,
            "name": self.name,
            "description": self.description,
            "status": self.status,
            "progress": self.progress,
            "result": self.result,
            "error": self.error,
            "createdAt": self.created_at,
            "startedAt": self.started_at,
            "completedAt": self.completed_at,
            "logs": self._log_messages[-50:],  # Last 50 logs
        }
    
    def log(self, message: str, level: str = "info"):
        """Add log message"""
        timestamp = datetime.now().isoformat()
        log_entry = {
            "timestamp": timestamp,
            "message": message,
            "level": level
        }
        self._log_messages.append(log_entry)
        
        # Broadcast via SSE if available
        if _sse_broadcast_func:
            _sse_broadcast_func(message, f"task:{level}")
    
    def set_progress(self, progress: int):
        """Update progress (0-100)"""
        self.progress = max(0, min(100, progress))
    
    def set_completed(self, result: Any = None):
        """Mark task as completed"""
        self.status = TaskStatus.COMPLETED
        self.progress = 100
        self.result = result
        self.completed_at = datetime.now().isoformat()
        self.log("✅ Hoàn thành!", "success")
    
    def set_failed(self, error: str):
        """Mark task as failed"""
        self.status = TaskStatus.FAILED
        self.error = error
        self.completed_at = datetime.now().isoformat()
        self.log(f"❌ Lỗi: {error}", "error")
    
    def set_cancelled(self):
        """Mark task as cancelled"""
        self.status = TaskStatus.CANCELLED
        self.completed_at = datetime.now().isoformat()
        self.log("⏸️  Bị hủy!", "warning")


def set_sse_broadcast_func(func: Callable):
    """Set the SSE broadcast function (called from app.py)"""
    global _sse_broadcast_func
    _sse_broadcast_func = func


def create_task(name: str, description: str = "") -> Task:
    """Create a new task"""
    task_id = str(uuid.uuid4())[:8]
    task = Task(task_id, name, description)
    
    with _TASKS_LOCK:
        _TASKS[task_id] = task
    
    return task


def get_task(task_id: str) -> Optional[Task]:
    """Get task by ID"""
    with _TASKS_LOCK:
        return _TASKS.get(task_id)


def list_tasks(status: Optional[str] = None, limit: int = 50) -> list:
    """List all tasks, optionally filtered by status"""
    with _TASKS_LOCK:
        tasks = list(_TASKS.values())
    
    if status:
        tasks = [t for t in tasks if t.status == status]
    
    # Return most recent first
    tasks.sort(key=lambda t: t.created_at, reverse=True)
    return tasks[:limit]


def run_task_in_background(
    func: Callable,
    task_name: str,
    task_description: str = "",
    *args,
    **kwargs
) -> Task:
    """
    Run a function in background thread and return task immediately.
    
    Args:
        func: Function to run
        task_name: Display name for task
        task_description: Longer description
        *args, **kwargs: Arguments to pass to func
    
    Returns:
        Task object (user can check status via get_task)
    """
    task = create_task(task_name, task_description)
    
    def worker():
        try:
            task.status = TaskStatus.RUNNING
            task.started_at = datetime.now().isoformat()
            task.log(f"Bắt đầu: {task_name}")
            
            # Call the function with task as first argument (for progress/logging)
            result = func(task, *args, **kwargs)
            task.set_completed(result)
            
        except Exception as e:
            import traceback
            error_msg = str(e)
            task.set_failed(error_msg)
            print(f"[TaskManager] Task {task.task_id} failed: {error_msg}")
            print(traceback.format_exc())
    
    # Start thread
    thread = threading.Thread(target=worker, daemon=True, name=f"task-{task.task_id}")
    thread.start()
    task._thread = thread
    
    return task


def cleanup_old_tasks(max_age_seconds: int = 3600):
    """Clean up completed tasks older than max_age (default 1 hour)"""
    now = time.time()
    cutoff_time = now - max_age_seconds
    
    with _TASKS_LOCK:
        to_delete = []
        for task_id, task in _TASKS.items():
            # Only delete completed/failed tasks
            if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
                created_timestamp = datetime.fromisoformat(task.created_at).timestamp()
                if created_timestamp < cutoff_time:
                    to_delete.append(task_id)
        
        for task_id in to_delete:
            del _TASKS[task_id]
    
    print(f"[TaskManager] Cleaned up {len(to_delete)} old tasks")


# Periodic cleanup (every 30 min)
def _start_cleanup_worker():
    def cleanup_loop():
        while True:
            time.sleep(1800)  # 30 minutes
            cleanup_old_tasks()
    
    thread = threading.Thread(target=cleanup_loop, daemon=True, name="task-cleanup")
    thread.start()


_start_cleanup_worker()
