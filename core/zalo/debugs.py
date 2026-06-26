import json
import os

# Thư mục debug nằm tại app root/debug
APP_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEBUG_DIR = os.path.join(APP_ROOT, "debug")


def save(filename: str, data, as_json: bool = True):
    os.makedirs(DEBUG_DIR, exist_ok=True)
    filepath = os.path.join(DEBUG_DIR, filename)

    if as_json or isinstance(data, (dict, list)):
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    else:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(str(data))

    print(f"[DEBUG] Đã lưu: {filepath}")
