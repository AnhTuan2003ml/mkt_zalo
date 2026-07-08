import json
import os
import sys


DEFAULT_ZPW_VER = "687"
SETTINGS_FILE_NAME = "app_settings.json"


def get_app_root():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def get_settings_path():
    return os.path.join(get_app_root(), "data", SETTINGS_FILE_NAME)


def normalize_zpw_ver(value, default=DEFAULT_ZPW_VER):
    value = str(value or "").strip()
    return value if value.isdigit() else default


def load_app_settings():
    path = get_settings_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {"zpw_ver": DEFAULT_ZPW_VER}
    except Exception:
        return {"zpw_ver": DEFAULT_ZPW_VER}

    if not isinstance(data, dict):
        data = {}

    data["zpw_ver"] = normalize_zpw_ver(data.get("zpw_ver"))
    return data


def save_app_settings(settings):
    data = load_app_settings()
    if isinstance(settings, dict):
        data.update(settings)

    data["zpw_ver"] = normalize_zpw_ver(data.get("zpw_ver"))

    path = get_settings_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    return data


def get_zpw_ver(value=None):
    if value is not None:
        return normalize_zpw_ver(value)
    return load_app_settings().get("zpw_ver", DEFAULT_ZPW_VER)
