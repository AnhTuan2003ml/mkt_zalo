#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
ACTIVATION CODE GENERATOR v3.0 - Per-Device, Time-Limited, Non-Sharable Keys

Core rules (as requested):
- Every device gets a **unique key** (tied to its fingerprint).
- Key is only valid for the number of days defined in ACTIVATION_DAYS_VALID (.env).
- When a key expires, the **exact same device** will automatically receive a **completely different key** the next time the app runs (new nonce + new expiry window).
- Keys from other devices are rejected (device fingerprint mismatch).
- Signature must be valid (tamper-proof).
- Wrong/expired key → activation blocked. User must use the freshly generated key for that period.

No key sharing possible across machines.
"""

import socket
import os
import uuid
import platform
import requests
import json
import smtplib
import hashlib
import secrets
import stat
import warnings
from datetime import datetime, timedelta
from getpass import getuser
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from cryptography.fernet import Fernet
import base64

# Suppress RequestsDependencyWarning
warnings.filterwarnings('ignore', category=DeprecationWarning)
warnings.filterwarnings('ignore', message='.*urllib3.*')

# Load environment variables (with PyInstaller onefile support)
import sys
import os
from pathlib import Path
import hmac  # for constant-time signature comparison


def get_app_root() -> Path:
    """Root app khi chạy source hoặc exe."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def get_bundle_resource_path(name: str) -> Path:
    """Lấy file được nhúng bởi PyInstaller --add-data."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / name
    return get_app_root() / name


def get_env_path() -> Path:
    """Ưu tiên .env ngoài cạnh exe/source; nếu không có thì lấy .env nhúng trong exe."""
    external_env = get_app_root() / ".env"
    if external_env.exists():
        return external_env

    bundled_env = get_bundle_resource_path(".env")
    if bundled_env.exists():
        return bundled_env

    return external_env


def _load_dotenv_safe():
    """Load .env safely, supporting PyInstaller onefile bundle.
    Uses python-dotenv when available, otherwise falls back to a small manual parser.
    """
    env_path = get_env_path()
    if not env_path.exists():
        print("[AUTH] .env loaded: no")
        return

    try:
        from dotenv import load_dotenv
        load_dotenv(env_path, override=True)
    except Exception as e:
        print(f"[AUTH] python-dotenv unavailable, using manual .env parser: {e}")
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for raw_line in f:
                    line = raw_line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    if key:
                        os.environ[key] = value
        except Exception as manual_e:
            print(f"[AUTH] .env manual load failed: {manual_e}")
            return

    source = "bundled" if getattr(sys, "frozen", False) and not (get_app_root() / ".env").exists() else "external"
    print(f"[AUTH] .env path: {source}")
    print("[AUTH] .env loaded: yes")


_load_dotenv_safe()

# Email config - from .env
SENDMAIL_USER = (os.getenv("SENDMAIL_USER", "") or "").strip()
# Gmail App Password thường được hiển thị theo nhóm có dấu cách.
# SMTP login cần chuỗi liền 16 ký tự, nên loại bỏ toàn bộ khoảng trắng để tránh lỗi 535 BadCredentials.
SENDMAIL_PASS = ''.join((os.getenv("SENDMAIL_PASS", "") or "").split())
RECEIVER_EMAIL_1 = (os.getenv("RECEIVER_EMAIL_1", "") or "").strip()
RECEIVER_EMAIL_2 = (os.getenv("RECEIVER_EMAIL_2", "") or "").strip()
RECEIVER_EMAIL_3 = (os.getenv("RECEIVER_EMAIL_3", "") or "").strip()
ACTIVATION_DAYS_VALID = int(os.getenv("ACTIVATION_DAYS_VALID", "30"))
SECRET_KEY = os.getenv("SECRET_KEY", "default_secret_key_change_this")

def _is_secret_key_valid(sk=None):
    """Check if SECRET_KEY is properly configured without printing sensitive/noisy logs."""
    if sk is None:
        sk = SECRET_KEY
    if not sk or not str(sk).strip() or sk == "default_secret_key_change_this":
        print("[AUTH] SECRET_KEY missing or default")
        return False
    return True

# Print status once on import - disabled to avoid noisy build/runtime logs
# _is_secret_key_valid()

# Backend config
BACKEND_URL = os.getenv("DEVICE_BACKEND_URL", "http://localhost:5000/api/device/register")
DEVICE_LOG_ENDPOINT = os.getenv("DEVICE_LOG_ENDPOINT", "http://localhost:5000/api/device/log")

# Persistent paths
def _get_persistent_paths():
    """Get persistent storage paths in AppData"""
    appdata = os.path.expandvars(r"%APPDATA%\ZaloMemberTool")
    os.makedirs(appdata, exist_ok=True)
    return {
        "marker": os.path.join(appdata, ".activation_sent"),
        "code": os.path.join(appdata, ".activation_code"),
        "fingerprint": os.path.join(appdata, ".device_fingerprint"),
        "latest": os.path.join(appdata, ".latest_keys.json"),
        "pending": os.path.join(appdata, ".pending_activation_code")
    }

PATHS = _get_persistent_paths()


# ====================== SHORT ACTIVATION CODE HELPERS ======================

SHORT_CODE_LENGTH = 12
SHORT_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # bỏ I/O/0/1 để tránh nhầm


def _normalize_short_code(code_text):
    """Chuẩn hóa mã người dùng nhập: bỏ khoảng trắng/gạch ngang, viết hoa."""
    return ''.join(ch for ch in str(code_text or '').upper() if ch.isalnum())


def _generate_short_activation_code(length=SHORT_CODE_LENGTH):
    """Sinh mã kích hoạt ngắn tối đa 12 ký tự."""
    return ''.join(secrets.choice(SHORT_CODE_ALPHABET) for _ in range(length))


def _pending_key_file():
    return os.path.join(os.path.dirname(PATHS["code"]), ".activation_pending_key")


def _save_pending_activation(short_code, code_data):
    """Lưu tạm mã ngắn -> dữ liệu kích hoạt đầy đủ trên chính máy này."""
    try:
        pending_file = PATHS["pending"]
        key_file = _pending_key_file()

        if os.path.exists(pending_file):
            try:
                os.chmod(pending_file, stat.S_IWRITE | stat.S_IREAD)
            except Exception:
                pass
        if os.path.exists(key_file):
            try:
                os.chmod(key_file, stat.S_IWRITE | stat.S_IREAD)
            except Exception:
                pass
            with open(key_file, 'rb') as f:
                encryption_key = f.read()
        else:
            encryption_key = generate_encryption_key()

        payload = dict(code_data)
        payload["short_code"] = short_code
        payload["short_code_length"] = SHORT_CODE_LENGTH
        payload["created"] = datetime.now().isoformat()

        encrypted = encrypt_data(json.dumps(payload), encryption_key)
        if not encrypted:
            return False

        with open(pending_file, 'wb') as f:
            f.write(encrypted)
        with open(key_file, 'wb') as f:
            f.write(encryption_key)

        os.chmod(pending_file, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        os.chmod(key_file, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        return True
    except Exception as e:
        print(f"[AUTH] Error saving pending activation code: {e}")
        return False


def _load_pending_activation():
    """Đọc dữ liệu kích hoạt đầy đủ tương ứng với mã ngắn đã gửi email."""
    try:
        pending_file = PATHS["pending"]
        key_file = _pending_key_file()
        if not os.path.exists(pending_file) or not os.path.exists(key_file):
            return None
        with open(key_file, 'rb') as f:
            encryption_key = f.read()
        with open(pending_file, 'rb') as f:
            encrypted = f.read()
        data = decrypt_data(encrypted, encryption_key)
        if not data:
            return None
        return json.loads(data)
    except Exception as e:
        print(f"[AUTH] Error loading pending activation code: {e}")
        return None


def _clear_pending_activation():
    """Xóa mã chờ sau khi kích hoạt thành công."""
    for path in [PATHS.get("pending"), _pending_key_file()]:
        if not path:
            continue
        try:
            if os.path.exists(path):
                os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
                os.remove(path)
        except Exception:
            pass


# ====================== LATEST KEY TRACKING (per device) ======================

def _get_latest_nonce(fingerprint_hash):
    """Return the nonce of the most recently issued key for this fingerprint, or None."""
    latest_file = PATHS["latest"]
    if not os.path.exists(latest_file):
        return None
    try:
        # Relax in case it was set read-only
        try:
            os.chmod(latest_file, stat.S_IWRITE | stat.S_IREAD)
        except:
            pass
        with open(latest_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data.get(fingerprint_hash)
    except Exception:
        return None


def _set_latest_nonce(fingerprint_hash, nonce):
    """Record that this nonce is now the latest issued key for the device."""
    latest_file = PATHS["latest"]
    data = {}
    if os.path.exists(latest_file):
        try:
            # Relax permissions so we can overwrite
            os.chmod(latest_file, stat.S_IWRITE | stat.S_IREAD)
            with open(latest_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            data = {}
    data[fingerprint_hash] = nonce
    try:
        with open(latest_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
        # Re-set read-only
        os.chmod(latest_file, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    except Exception as e:
        print(f"[AUTH] Warning: could not update latest key record: {e}")


# ====================== ENCRYPTION FUNCTIONS ======================

def generate_encryption_key():
    """Generate a new random encryption key (Fernet)"""
    return Fernet.generate_key()


def get_or_create_encryption_key():
    """Get existing encryption key or create a new one"""
    key_file = PATHS.get("encryption_key") or os.path.join(
        os.path.dirname(PATHS["code"]), ".activation_key"
    )
    
    try:
        if os.path.exists(key_file):
            with open(key_file, 'rb') as f:
                return f.read()
        else:
            # Generate new key
            key = generate_encryption_key()
            
            # Save key with read-only permissions
            with open(key_file, 'wb') as f:
                f.write(key)
            
            os.chmod(key_file, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
            return key
    except Exception as e:
        print(f"❌ Error managing encryption key: {e}")
        return None


def encrypt_data(data, key):
    """Encrypt data using Fernet cipher"""
    try:
        cipher = Fernet(key)
        encrypted = cipher.encrypt(data.encode() if isinstance(data, str) else data)
        return encrypted
    except Exception as e:
        print(f"❌ Error encrypting data: {e}")
        return None


def decrypt_data(encrypted_data, key):
    """Decrypt data using Fernet cipher"""
    try:
        cipher = Fernet(key)
        decrypted = cipher.decrypt(encrypted_data)
        return decrypted.decode() if isinstance(decrypted, bytes) else decrypted
    except Exception as e:
        print(f"❌ Error decrypting data: {e}")
        return None


# ====================== ACTIVATION CODE GENERATION ======================

def generate_device_fingerprint():
    """
    Generate unique device fingerprint (immutable)
    Based on: MAC address, OS, hostname
    """
    try:
        mac = uuid.getnode()
        device_name = socket.gethostname()
        os_info = f"{platform.system()}_{platform.release()}"
        username = getuser()
        
        # Create unique string
        fingerprint_str = f"{mac}:{device_name}:{os_info}:{username}"
        
        # Hash it
        fingerprint_hash = hashlib.sha256(fingerprint_str.encode()).hexdigest()
        
        return {
            "hash": fingerprint_hash,
            "mac": str(mac),
            "device_name": device_name,
            "os": os_info,
            "user": username,
            "created": datetime.now().isoformat()
        }
    except Exception as e:
        print(f"❌ Error generating fingerprint: {e}")
        return None


def create_activation_code(fingerprint_hash):
    """
    Create activation data with expiry, but send only a 12-character code to the user.

    Email/user-facing format:
      12 ký tự, ví dụ: A7K9P2XQ4MZ8

    Internal signed payload (stored only on this device):
      fingerprint_hash|expiry_iso|nonce|signature
    """
    try:
        # Generate expiry date
        expiry_date = (datetime.now() + timedelta(days=ACTIVATION_DAYS_VALID)).isoformat()

        # Generate random nonce
        nonce = secrets.token_hex(16)

        # Internal signed payload
        payload = f"{fingerprint_hash}|{expiry_date}|{nonce}"
        signature = hashlib.sha256(f"{payload}:{SECRET_KEY}".encode()).hexdigest()
        full_activation_code = f"{fingerprint_hash}|{expiry_date}|{nonce}|{signature}"

        # User-facing short code: always max 12 characters
        short_code = _generate_short_activation_code(SHORT_CODE_LENGTH)

        # Mark this as the latest issued key for the device.
        _set_latest_nonce(fingerprint_hash, nonce)

        # Store the full signed data locally so the 12-character code can be checked later.
        pending_data = {
            "code": full_activation_code,
            "fingerprint": fingerprint_hash,
            "expiry": expiry_date,
            "nonce": nonce,
            "signature": signature,
        }
        if not _save_pending_activation(short_code, pending_data):
            print("[AUTH] Warning: could not save pending short activation code")

        # Encrypt for storage (kept for compatibility with old code paths)
        key = Fernet.generate_key()
        cipher = Fernet(key)
        encrypted_code = cipher.encrypt(full_activation_code.encode()).decode()

        return {
            "code": short_code,                  # what will be emailed and typed by user
            "short_code": short_code,
            "full_code": full_activation_code,  # internal only
            "encrypted": encrypted_code,
            "key": key.decode(),
            "expiry": expiry_date,
            "nonce": nonce,
            "signature": signature,
            "created": datetime.now().isoformat(),
            "max_length": SHORT_CODE_LENGTH,
        }
    except Exception as e:
        print(f"❌ Error creating activation code: {e}")
        return None

def save_activation_code_securely(code_data):
    """
    Save activation code encrypted with random key
    Code cannot be read or modified manually
    """
    try:
        # Generate new random encryption key
        encryption_key = generate_encryption_key()
        
        # Convert code data to JSON
        code_json = json.dumps(code_data)
        
        # Encrypt the code
        encrypted_code = encrypt_data(code_json, encryption_key)
        if not encrypted_code:
            return False
        
        # Save encrypted code to file (binary mode)
        code_file = PATHS["code"]
        with open(code_file, 'wb') as f:
            f.write(encrypted_code)
        
        # Save encryption key to separate file with read-only permissions
        key_file = os.path.join(os.path.dirname(code_file), ".activation_key")
        with open(key_file, 'wb') as f:
            f.write(encryption_key)
        
        # Set read-only permissions for both files
        os.chmod(code_file, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        os.chmod(key_file, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        
        return True
        
    except Exception as e:
        print(f"❌ Error saving activation code securely: {e}")
        return False


def validate_activation_code():
    """
    Validate activation code expiry and integrity
    Returns: (is_valid, days_remaining, message)
    """
    try:
        if not _is_secret_key_valid():
            print('[AUTH] SECRET_KEY missing/mismatch - signature re-verify disabled (rely on local encrypted store for expiry/device check)')
        
        code_file = PATHS["code"]
        
        # Check if code exists
        if not os.path.exists(code_file):
            return False, 0, "❌ Activation code not found"
        
        # Get encryption key
        key_file = os.path.join(os.path.dirname(code_file), ".activation_key")
        if not os.path.exists(key_file):
            return False, 0, "❌ Encryption key not found"
        
        # Read and decrypt code
        with open(key_file, 'rb') as f:
            encryption_key = f.read()
        
        with open(code_file, 'rb') as f:
            encrypted_code = f.read()
        
        # Decrypt
        code_json = decrypt_data(encrypted_code, encryption_key)
        if not code_json:
            return False, 0, "❌ Failed to decrypt activation code"
        
        code_data = json.loads(code_json)
        
        stored_code_str = code_data.get("code", "")
        
        # Extract nonce from whatever format the stored code is in
        stored_nonce = None
        stored_fp = None
        if '|' in stored_code_str:
            p = stored_code_str.split('|')
            if len(p) >= 3:
                stored_fp = p[0]
                stored_nonce = p[2]
        else:
            p = stored_code_str.split(':')
            if len(p) >= 3:
                stored_fp = p[0]
                # For legacy we don't have nonce in the pasted string, so we can't do latest check reliably.
                # But we still do device check below.
        
        # Check against latest issued key (if we have a nonce)
        if stored_nonce:
            latest_nonce = _get_latest_nonce(stored_fp or code_data.get("fingerprint", ""))
            if latest_nonce is not None and stored_nonce != latest_nonce:
                return False, 0, "❌ This activation key is no longer valid. A newer key has been issued for this device."
        
        # Check expiry
        expiry = datetime.fromisoformat(code_data["expiry"])
        now = datetime.now()
        
        if now > expiry:
            return False, 0, f"❌ Activation code expired on {expiry.strftime('%Y-%m-%d')}"
        
        # Calculate days remaining
        days_remaining = (expiry - now).days
        
        # Lỗi 4 + improved sig/device check in validate (for stored code)
        stored_code_str = code_data.get("code", "")
        try:
            if '|' in stored_code_str:
                stored_fp = stored_code_str.split('|')[0]
            else:
                stored_fp = stored_code_str.split(':')[0]
            curr_fp = generate_device_fingerprint()
            if curr_fp and curr_fp.get("hash"):
                curr_full = curr_fp["hash"]
                curr_short = curr_full[:16]
                if stored_fp != curr_full and stored_fp != curr_short:
                    return False, 0, "❌ Activation key is for another device"
        except Exception as de:
            print(f"[AUTH] Device check in validate warning: {de}")
        
        # Optional re-verify sig for stored (if sk available and new format)
        sk = SECRET_KEY
        if _is_secret_key_valid(sk) and "signature" in code_data and stored_code_str:
            try:
                if '|' in stored_code_str and len(stored_code_str.split('|')) == 4:
                    p = stored_code_str.split('|')
                    pay = f"{p[0]}|{p[1]}|{p[2]}"
                    exp_sig = hashlib.sha256(f"{pay}:{sk}".encode()).hexdigest()
                    if not hmac.compare_digest(exp_sig, code_data.get("signature", "")):
                        return False, 0, "❌ Code signature verification FAILED - code is invalid or tampered"
            except Exception as ve:
                print(f"[AUTH] Sig re-verify in validate warning: {ve}")
        
        message = f"✅ License valid for {days_remaining} more days"
        
        return True, days_remaining, message
        
    except Exception as e:
        print(f"❌ Error validating code: {e}")
        return False, 0, f"❌ Validation error: {e}"


# ====================== EMAIL FUNCTIONS ======================

def send_activation_code_by_email(device_info, activation_code):
    """
    Send activation code to 2 recipients
    Recipients: RECEIVER_EMAIL_1, RECEIVER_EMAIL_2, RECEIVER_EMAIL_3
    """
    if not device_info or not activation_code:
        return False
    
    recipients = [r for r in [RECEIVER_EMAIL_1, RECEIVER_EMAIL_2, RECEIVER_EMAIL_3] if r]
    if not SENDMAIL_USER or not SENDMAIL_PASS or not recipients:
        print("[AUTH] Missing email config. Set SENDMAIL_USER, SENDMAIL_PASS, RECEIVER_EMAIL_1/2/3 in .env")
        return False
    
    try:
        for recipient in recipients:
            # Prepare email content
            subject = f"🔐 Activation Code - {device_info['device_name']}"
            
            expiry_date = activation_code["expiry"]
            code = activation_code.get("short_code") or activation_code["code"]
            
            body = f"""
            <html>
            <head>
                <style>
                    body {{ font-family: Arial, sans-serif; background: #f5f5f5; }}
                    .container {{ max-width: 600px; margin: 20px auto; background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }}
                    h1 {{ color: #333; border-bottom: 2px solid #4CAF50; padding-bottom: 10px; }}
                    .info-block {{ background: #f9f9f9; padding: 15px; margin: 10px 0; border-radius: 5px; border-left: 3px solid #4CAF50; }}
                    .code-block {{ background: #f0f0f0; padding: 18px; margin: 15px 0; border-radius: 5px; border: 2px solid #4CAF50; font-family: monospace; font-size: 28px; font-weight: bold; letter-spacing: 4px; text-align: center; }}
                    .label {{ font-weight: bold; color: #333; }}
                    .value {{ color: #555; font-family: monospace; }}
                    .warning {{ background: #fff3cd; padding: 12px; border-radius: 5px; border-left: 3px solid #ffc107; margin: 15px 0; }}
                    .timestamp {{ color: #999; font-size: 12px; margin-top: 20px; text-align: center; }}
                </style>
            </head>
            <body>
                <div class="container">
                    <h1>🔐 Nexus - Mã kích hoạt</h1>
                    
                    <div class="info-block">
                        <div><span class="label">🖥️ Device Name:</span> <span class="value">{device_info['device_name']}</span></div>
                        <div><span class="label">👤 User:</span> <span class="value">{device_info['username']}</span></div>
                        <div><span class="label">📱 MAC Address:</span> <span class="value">{device_info['mac_address']}</span></div>
                        <div><span class="label">🗺️ Location:</span> <span class="value">{device_info['location']}</span></div>
                    </div>
                    
                    <h2 style="color: #4CAF50;">Mã kích hoạt của thiết bị (12 ký tự):</h2>
                    <div class="code-block">{code}</div>
                    
                    <div class="info-block">
                        <div><span class="label">⏰ Valid Until:</span> <span class="value">{expiry_date}</span></div>
                        <div><span class="label">📅 Duration:</span> <span class="value">{ACTIVATION_DAYS_VALID} days</span></div>
                    </div>
                    
                    <div class="warning">
                        <strong>⚠️ Important:</strong> This activation code is:
                        <ul>
                            <li>✅ Device-specific (tied to this machine)</li>
                            <li>✅ Time-limited ({ACTIVATION_DAYS_VALID} days expiry)</li>
                            <li>✅ Tối đa 12 ký tự, dễ nhập</li>
                            <li>✅ Immutable (cannot be modified)</li>
                            <li>✅ Encrypted and secured internally</li>
                        </ul>
                        Do not share this code with others.
                    </div>
                    
                    <p class="timestamp">Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
                </div>
            </body>
            </html>
            """
            
            # Create email
            msg = MIMEMultipart('alternative')
            msg['From'] = SENDMAIL_USER
            msg['To'] = recipient
            msg['Subject'] = subject
            msg.attach(MIMEText(body, 'html'))
            
            # Send email
            server = smtplib.SMTP('smtp.gmail.com', 587)
            server.starttls()
            server.login(SENDMAIL_USER, SENDMAIL_PASS)
            server.send_message(msg)
            server.quit()
        
        return True
        
    except smtplib.SMTPAuthenticationError as e:
        print("[AUTH] Gmail SMTP authentication failed. Kiểm tra SENDMAIL_USER và SENDMAIL_PASS trong .env.")
        print("[AUTH] Nếu dùng Gmail, SENDMAIL_PASS phải là App Password 16 ký tự; không dùng mật khẩu Gmail thường. Khoảng trắng trong App Password đã được tự loại bỏ.")
        print(f"[AUTH] SMTP error: {e}")
        return False
    except Exception as e:
        print(f"[AUTH] Error sending activation email: {e}")
        return False


# ====================== DEVICE INFO FUNCTIONS ======================

def get_device_info():
    """Get device information for activation"""
    try:
        device_name = socket.gethostname()
        username = getuser()
        mac_int = uuid.getnode()
        device_id = str(mac_int)
        mac_address = format_mac_address(mac_int)
        location = get_location()
        os_info = f"{platform.system()} {platform.release()}"
        ip_address = get_local_ip()
        
        device_info = {
            "device_name": device_name,
            "username": username,
            "device_id": device_id,
            "location": location,
            "os": os_info,
            "ip_address": ip_address,
            "mac_address": mac_address,
            "timestamp": datetime.now().isoformat()
        }
        
        return device_info
    except Exception as e:
        print(f"❌ Error getting device info: {str(e)}")
        return None


def get_local_ip():
    """Get local IP address"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "127.0.0.1"


def get_location():
    """Get location from IP geolocation"""
    try:
        response = requests.get("https://ipapi.co/json/", timeout=3)
        if response.status_code == 200:
            data = response.json()
            location = f"{data.get('city', 'Unknown')}, {data.get('country_name', 'Unknown')}"
            return location
    except:
        pass
    
    return "Unknown"


def format_mac_address(device_id_int):
    """Format MAC address"""
    try:
        mac_bytes = device_id_int.to_bytes(6, byteorder='big')
        return ':'.join('{:02x}'.format(b) for b in mac_bytes)
    except:
        return str(device_id_int)


# ====================== MAIN PROCESS ======================

def register_device_with_activation(verbose=False):
    """
    Main process: Generate and send activation code for this device.
    
    Key behavior (per requirements):
    - Each device gets its own unique key (tied to fingerprint).
    - Key is only valid for ACTIVATION_DAYS_VALID days (from .env).
    - When the current key expires, the **same device** will automatically 
      trigger generation of a **brand new different key** (new nonce, new expiry, new signature)
      the next time the app starts.
    - Wrong key (wrong device, bad signature, expired) will not activate.
    - Keys are never shared across devices.
    
    Decision to send new key:
    - If there is currently a valid (non-expired + matching this device's fingerprint) 
      local activation code → skip (no new email).
    - Otherwise (no code, expired, or for different device) → generate fresh code 
      and email it.
    """
    if verbose:
        print("\n" + "="*60)
        print("🔐 Nexus - ACTIVATION CODE GENERATOR")
        print("="*60 + "\n")
    
    # SECRET_KEY is required because the same key verifies the pasted activation code.
    if not _is_secret_key_valid():
        if verbose:
            print("❌ SECRET_KEY missing. Set SECRET_KEY in .env before generating activation codes.")
        return False

    # Generate fingerprint early (used for both check and new code)
    fingerprint = generate_device_fingerprint()
    if not fingerprint:
        if verbose:
            print("❌ Failed to generate fingerprint")
        return False
    
    # === Core logic: only send if no valid activation for THIS device right now ===
    is_valid, days_remaining, msg = validate_activation_code()
    
    if is_valid:
        if verbose:
            print("ℹ️  This device already has a valid activation code for the current period.")
            print(msg)
            print("   (No new key will be generated until the current one expires.)")
        return False
    
    # No valid code for this device (missing, expired, or device mismatch) 
    # → Generate and send a **new unique key** with fresh expiry.
    if verbose:
        print("ℹ️  No valid activation for this device (or expired). Generating new key...")
    
    # Step 2: Get device info (for email)
    device_info = get_device_info()
    if not device_info:
        if verbose:
            print("❌ Failed to get device information")
        return False
    
    if verbose:
        print("📱 Device Information:")
        print(f"   🖥️  Device: {device_info['device_name']}")
        print(f"   👤 User: {device_info['username']}")
        print(f"   📱 MAC: {device_info['mac_address']}")
        print(f"   🌐 IP: {device_info['ip_address']}")
        print(f"   🗺️  Location: {device_info['location']}")
        print(f"   💻 OS: {device_info['os']}\n")
    
    if verbose:
        print(f"✅ Device fingerprint: {fingerprint['hash']}\n")
    
    # Step 4: Create a brand new activation code (will have new nonce + new future expiry)
    activation_code = create_activation_code(fingerprint['hash'])
    if not activation_code:
        if verbose:
            print("❌ Failed to create activation code")
        return False
    
    if verbose:
        print(f"✅ New activation code generated for this device")
        print(f"   📅 Valid until: {activation_code['expiry']}")
        print(f"   🔐 Signature: {activation_code['signature'][:16]}...\n")
    
    # Step 5: Send the new code via email
    if verbose:
        print(f"📧 Sending new activation code to recipients...\n")
    if not send_activation_code_by_email(device_info, activation_code):
        if verbose:
            print("❌ Failed to send activation code")
        return False
    
    # Update marker (timestamp of last successful send attempt)
    # Note: we no longer use this marker to permanently skip; validity is decided by the code's expiry + device match.
    try:
        with open(PATHS["marker"], 'w') as f:
            f.write(datetime.now().isoformat())
        if verbose:
            print("\n✅ New activation code sent!")
            print(f"📧 Code sent to: {RECEIVER_EMAIL_1}, {RECEIVER_EMAIL_2}, {RECEIVER_EMAIL_3}")
            print("   User must enter the latest 12-character key when prompted (old key is now invalid).")
        return True
    except Exception as e:
        if verbose:
            print(f"❌ Error marking registration: {e}")
        return False


def save_user_activation_code(code_text):
    """
    Save activation code pasted by user from email.

    New user-facing format:
      12 ký tự, ví dụ: A7K9P2XQ4MZ8

    The full signed activation payload is stored locally when the code is emailed.
    Returns: (success: bool, message: str)
    """
    try:
        raw_input_code = (code_text or "").strip()
        if not raw_input_code:
            return False, "❌ Vui lòng nhập mã kích hoạt"

        normalized_short_code = _normalize_short_code(raw_input_code)
        full_code_text = None
        display_code = normalized_short_code

        # New short-code process: user enters max 12 characters.
        if '|' not in raw_input_code and ':' not in raw_input_code:
            if len(normalized_short_code) != SHORT_CODE_LENGTH:
                return False, f"❌ Mã kích hoạt phải đúng {SHORT_CODE_LENGTH} ký tự"

            pending = _load_pending_activation()
            if not pending:
                return False, "❌ Không tìm thấy mã kích hoạt đang chờ trên máy này. Hãy bấm gửi lại mã qua email."

            expected_short = _normalize_short_code(pending.get("short_code", ""))
            if not hmac.compare_digest(normalized_short_code, expected_short):
                return False, "❌ Mã kích hoạt không đúng. Vui lòng nhập đúng mã 12 ký tự mới nhất trong email."

            full_code_text = pending.get("code") or pending.get("full_code")
            if not full_code_text:
                return False, "❌ Dữ liệu kích hoạt nội bộ không hợp lệ. Hãy bấm gửi lại mã qua email."
            code_text = full_code_text
        else:
            # Legacy compatibility for already-issued long codes.
            code_text = raw_input_code
            display_code = "LEGACY"

        # === SAFE PARSE supporting legacy : (expiry has :) and new | (4 parts with nonce) ===
        if '|' in code_text:
            parts = code_text.split('|')
            if len(parts) != 4:
                return False, "❌ Dữ liệu kích hoạt nội bộ không đúng định dạng"
            fingerprint_hash = parts[0]
            expiry_str = parts[1]
            nonce = parts[2]
            signature = parts[3]
            payload_for_sig = f"{fingerprint_hash}|{expiry_str}|{nonce}"
        else:
            # legacy compat: hash:expiry(with:):sig --> last part = sig, join [1:-1]
            parts = code_text.split(':')
            if len(parts) < 3:
                return False, "❌ Invalid code format. Expected: hash:expiry:signature"
            fingerprint_hash = parts[0]
            signature = parts[-1]
            expiry_str = ':'.join(parts[1:-1])
            nonce = None
            payload_for_sig = f"{fingerprint_hash}:{expiry_str}"
        
        # Validate hash
        if len(fingerprint_hash) < 8:
            return False, "❌ Invalid fingerprint hash"
        
        # Validate expiry
        try:
            expiry = datetime.fromisoformat(expiry_str)
            now = datetime.now()
            if now > expiry:
                return False, f"❌ Code already expired on {expiry.strftime('%Y-%m-%d')}"
            days_remaining = (expiry - now).days
        except Exception:
            return False, "❌ Invalid expiry date format"
        
        # === Lỗi 4: check device using same generate_device_fingerprint ===
        try:
            current_fp = generate_device_fingerprint()
            if current_fp and current_fp.get("hash"):
                curr_full = current_fp["hash"]
                curr_short = curr_full[:16]
                if fingerprint_hash != curr_full and fingerprint_hash != curr_short:
                    return False, "❌ Activation key is for another device"
        except Exception as fp_e:
            print(f"[AUTH] Device fingerprint check warning (non-fatal): {fp_e}")
        
        # === NEW: Only the latest issued key for this device is accepted ===
        # This prevents old keys from working even if they are not yet expired.
        latest_nonce = _get_latest_nonce(fingerprint_hash)
        if latest_nonce is not None and nonce != latest_nonce:
            return False, "❌ This is an old activation key. A newer key has been issued for this device. Please use the latest key from your email."
        
        # === Lỗi 3: signature verify (must match generator's payload + SECRET) ===
        if not _is_secret_key_valid():
            return False, "❌ SECRET_KEY missing. Cannot verify activation code."
        
        sk = SECRET_KEY
        if nonce is not None:
            expected_sig = hashlib.sha256(f"{payload_for_sig}:{sk}".encode()).hexdigest()
            if not hmac.compare_digest(expected_sig, signature):
                return False, "❌ Code signature verification FAILED - code is invalid or tampered"
        else:
            # legacy: old sigs included hidden nonce not sent to user; accept based on device+expiry+format
            try:
                expected_sig = hashlib.sha256(f"{payload_for_sig}:{sk}".encode()).hexdigest()
                if not hmac.compare_digest(expected_sig, signature):
                    print("[AUTH] Legacy code sig mismatch (expected for keys without nonce in emailed string). Proceeding with other checks.")
            except:
                pass
        
        # store original pasted (keeps | or : )
        code_data = {
            "code": code_text,  # full signed payload for internal validation
            "display_code": display_code,
            "expiry": expiry_str,
            "signature": signature,
            "nonce": nonce,
            "created": datetime.now().isoformat()
        }
        
        # Check if encryption key already exists (reuse if code still valid)
        key_file = os.path.join(os.path.dirname(PATHS["code"]), ".activation_key")
        code_file = PATHS["code"]
        
        if os.path.exists(key_file):
            # Reuse existing key (code still valid)
            with open(key_file, 'rb') as f:
                encryption_key = f.read()
        else:
            # Generate NEW random key (first time or after code expired)
            encryption_key = generate_encryption_key()
        
        # Convert code data to JSON and encrypt
        code_json = json.dumps(code_data)
        encrypted_code = encrypt_data(code_json, encryption_key)
        
        if not encrypted_code:
            return False, "❌ Failed to encrypt activation code"
        
        # Remove read-only flag if file exists
        if os.path.exists(code_file):
            try:
                os.chmod(code_file, stat.S_IWRITE | stat.S_IREAD)
            except:
                pass
        
        # Write encrypted code (binary mode)
        with open(code_file, 'wb') as f:
            f.write(encrypted_code)
        
        # Save/reuse encryption key
        # Remove read-only flag from key file if it exists
        if os.path.exists(key_file):
            try:
                os.chmod(key_file, stat.S_IWRITE | stat.S_IREAD)
            except:
                pass
        
        # Write encryption key (binary mode)
        with open(key_file, 'wb') as f:
            f.write(encryption_key)
        
        # Set read-only permissions for both files
        os.chmod(code_file, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        os.chmod(key_file, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        
        _clear_pending_activation()

        message = f"✅ Kích hoạt thành công! License còn {days_remaining} ngày"
        return True, message
        
    except Exception as e:
        return False, f"❌ Error: {str(e)}"


def check_activation_on_startup():
    """Check if activation code is valid on app startup"""
    is_valid, days_remaining, message = validate_activation_code()
    print(f"\n{message}")
    
    if not is_valid:
        print("⚠️  Please request a new activation code")
        return False
    
    return True


# ====================== MEMBERS_ZALO COMPATIBILITY HELPERS ======================

def send_device_info_to_backend(device_info):
    """Send device info to local Flask backend registry (non-fatal)."""
    if not device_info:
        return False
    try:
        response = requests.post(BACKEND_URL, json=device_info, timeout=5)
        if response.status_code in (200, 201):
            print(f"✅ Đã ghi nhận thiết bị vào backend: {device_info.get('device_name')}")
            return True
        print(f"⚠️ Backend ghi nhận thiết bị trả HTTP {response.status_code}")
        return False
    except Exception as e:
        print(f"⚠️ Không gửi được thông tin thiết bị về backend: {e}")
        return False


def send_device_log(action, details=None):
    """Write device activity log to local Flask backend (non-fatal)."""
    try:
        device_info = get_device_info()
        if not device_info:
            return False
        log_data = {
            "device_name": device_info.get("device_name", "Unknown"),
            "username": device_info.get("username", "Unknown"),
            "action": action,
            "details": details or "",
            "timestamp": datetime.now().isoformat(),
            "ip_address": device_info.get("ip_address", ""),
        }
        response = requests.post(DEVICE_LOG_ENDPOINT, json=log_data, timeout=5)
        return response.status_code in (200, 201)
    except Exception as e:
        print(f"⚠️ Lỗi ghi log thiết bị: {e}")
        return False


def register_device_on_startup(verbose=True):
    """
    Compatibility function used by members_zalo app.py.
    - If no valid license exists, generate a fresh device-bound activation key and email it.
    - Always tries to record device info to the local backend registry.
    """
    try:
        device_info = get_device_info()
        if device_info:
            send_device_info_to_backend(device_info)
    except Exception as e:
        print(f"⚠️ Không lấy/gửi được thông tin thiết bị: {e}")
    return register_device_with_activation(verbose=verbose)


if __name__ == "__main__":
    # First run: Generate and send activation code
    register_device_with_activation()
    
    # Subsequent runs: Check activation
    # check_activation_on_startup()

# ====================== OVERRIDES: ACTIVATION UI DURATION SUPPORT ======================

ACTIVATION_DURATION_OPTIONS = {
    "3d": {"key": "3d", "label": "3 Ngày", "days": 3, "icon": "📅", "is_permanent": False},
    "7d": {"key": "7d", "label": "1 Tuần", "days": 7, "icon": "📈", "is_permanent": False},
    "10d": {"key": "10d", "label": "10 Ngày", "days": 10, "icon": "📈", "is_permanent": False},
    "1m": {"key": "1m", "label": "1 Tháng", "days": 30, "icon": "📊", "is_permanent": False},
    "2m": {"key": "2m", "label": "2 Tháng", "days": 60, "icon": "📊", "is_permanent": False},
    "3m": {"key": "3m", "label": "3 Tháng", "days": 90, "icon": "📊", "is_permanent": False},
    "6m": {"key": "6m", "label": "6 Tháng", "days": 180, "icon": "📊", "is_permanent": False},
    "lifetime": {"key": "lifetime", "label": "Vĩnh viễn", "days": None, "icon": "💎", "is_permanent": True},
}
DEFAULT_ACTIVATION_OPTION_KEY = os.getenv("DEFAULT_ACTIVATION_OPTION_KEY", "1m")


def get_activation_duration_options():
    return [ACTIVATION_DURATION_OPTIONS[k] for k in ["3d", "7d", "10d", "1m", "2m", "3m", "6m", "lifetime"]]


def normalize_requested_duration(duration_key=None):
    key = str(duration_key or "").strip().lower()
    if key in ACTIVATION_DURATION_OPTIONS:
        return dict(ACTIVATION_DURATION_OPTIONS[key])
    if DEFAULT_ACTIVATION_OPTION_KEY in ACTIVATION_DURATION_OPTIONS:
        return dict(ACTIVATION_DURATION_OPTIONS[DEFAULT_ACTIVATION_OPTION_KEY])
    return dict(ACTIVATION_DURATION_OPTIONS["1m"])


def _safe_iso_expiry(option):
    if option.get("is_permanent"):
        return "2099-12-31T23:59:59"
    days = int(option.get("days") or ACTIVATION_DAYS_VALID)
    return (datetime.now() + timedelta(days=days)).isoformat()


def create_activation_code(fingerprint_hash, duration_key=None):
    """Create a 12-char activation code with selectable duration."""
    try:
        option = normalize_requested_duration(duration_key)
        expiry_date = _safe_iso_expiry(option)
        nonce = secrets.token_hex(16)
        payload = f"{fingerprint_hash}|{expiry_date}|{nonce}"
        signature = hashlib.sha256(f"{payload}:{SECRET_KEY}".encode()).hexdigest()
        full_activation_code = f"{fingerprint_hash}|{expiry_date}|{nonce}|{signature}"
        short_code = _generate_short_activation_code(SHORT_CODE_LENGTH)

        _set_latest_nonce(fingerprint_hash, nonce)

        pending_data = {
            "code": full_activation_code,
            "full_code": full_activation_code,
            "fingerprint": fingerprint_hash,
            "expiry": expiry_date,
            "nonce": nonce,
            "signature": signature,
            "plan_key": option["key"],
            "plan_label": option["label"],
            "duration_days": option["days"],
            "is_permanent": option["is_permanent"],
        }
        if not _save_pending_activation(short_code, pending_data):
            print("[AUTH] Warning: could not save pending short activation code")

        key = Fernet.generate_key()
        cipher = Fernet(key)
        encrypted_code = cipher.encrypt(full_activation_code.encode()).decode()

        return {
            "code": short_code,
            "short_code": short_code,
            "full_code": full_activation_code,
            "encrypted": encrypted_code,
            "key": key.decode(),
            "expiry": expiry_date,
            "nonce": nonce,
            "signature": signature,
            "created": datetime.now().isoformat(),
            "max_length": SHORT_CODE_LENGTH,
            "plan_key": option["key"],
            "plan_label": option["label"],
            "duration_days": option["days"],
            "is_permanent": option["is_permanent"],
        }
    except Exception as e:
        print(f"❌ Error creating activation code: {e}")
        return None


def send_activation_code_by_email(device_info, activation_code):
    if not device_info or not activation_code:
        return False

    recipients = [r for r in [RECEIVER_EMAIL_1, RECEIVER_EMAIL_2, RECEIVER_EMAIL_3] if r]
    if not SENDMAIL_USER or not SENDMAIL_PASS or not recipients:
        print("[AUTH] Missing email config. Set SENDMAIL_USER, SENDMAIL_PASS, RECEIVER_EMAIL_1/2/3 in .env")
        return False

    plan_label = activation_code.get("plan_label") or "Không rõ"
    duration_days = activation_code.get("duration_days")
    duration_text = "Vĩnh viễn" if activation_code.get("is_permanent") else f"{duration_days} ngày"
    expiry_text = "Không giới hạn" if activation_code.get("is_permanent") else activation_code.get("expiry")
    code = activation_code.get("short_code") or activation_code.get("code")

    try:
        for recipient in recipients:
            subject = f"🔐 Nexus - Mã kích hoạt {plan_label} - {device_info['device_name']}"
            body = f"""
            <html>
            <head>
                <style>
                    body {{ font-family: Arial, sans-serif; background: #f5f5f5; }}
                    .container {{ max-width: 640px; margin: 20px auto; background: white; padding: 24px; border-radius: 10px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }}
                    h1 {{ color: #333; border-bottom: 2px solid #4CAF50; padding-bottom: 10px; }}
                    .info-block {{ background: #f9f9f9; padding: 15px; margin: 10px 0; border-radius: 5px; border-left: 3px solid #4CAF50; }}
                    .code-block {{ background: #f0f0f0; padding: 18px; margin: 15px 0; border-radius: 5px; border: 2px solid #4CAF50; font-family: monospace; font-size: 28px; font-weight: bold; letter-spacing: 4px; text-align: center; }}
                    .label {{ font-weight: bold; color: #333; }}
                    .value {{ color: #555; font-family: monospace; }}
                    .warning {{ background: #fff3cd; padding: 12px; border-radius: 5px; border-left: 3px solid #ffc107; margin: 15px 0; }}
                    .timestamp {{ color: #999; font-size: 12px; margin-top: 20px; text-align: center; }}
                </style>
            </head>
            <body>
                <div class="container">
                    <h1>🔐 Nexus - Mã kích hoạt</h1>
                    <div class="info-block">
                        <div><span class="label">🖥️ Device Name:</span> <span class="value">{device_info['device_name']}</span></div>
                        <div><span class="label">👤 User:</span> <span class="value">{device_info['username']}</span></div>
                        <div><span class="label">📱 MAC Address:</span> <span class="value">{device_info['mac_address']}</span></div>
                        <div><span class="label">🗺️ Location:</span> <span class="value">{device_info['location']}</span></div>
                    </div>
                    <h2 style="color: #4CAF50;">Mã kích hoạt của thiết bị (12 ký tự):</h2>
                    <div class="code-block">{code}</div>
                    <div class="info-block">
                        <div><span class="label">🎯 Gói kích hoạt:</span> <span class="value">{plan_label}</span></div>
                        <div><span class="label">📅 Thời hạn:</span> <span class="value">{duration_text}</span></div>
                        <div><span class="label">⏰ Hết hạn:</span> <span class="value">{expiry_text}</span></div>
                    </div>
                    <div class="warning">
                        <strong>⚠️ Lưu ý:</strong>
                        <ul>
                            <li>✅ Mã chỉ dùng cho đúng thiết bị này</li>
                            <li>✅ Mã chỉ dài tối đa 12 ký tự</li>
                            <li>✅ Mã mới nhất sẽ làm mã cũ hết hiệu lực</li>
                            <li>✅ Dữ liệu kích hoạt nội bộ vẫn được ký số và mã hóa</li>
                        </ul>
                    </div>
                    <p class="timestamp">Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
                </div>
            </body>
            </html>
            """
            msg = MIMEMultipart('alternative')
            msg['From'] = SENDMAIL_USER
            msg['To'] = recipient
            msg['Subject'] = subject
            msg.attach(MIMEText(body, 'html'))
            server = smtplib.SMTP('smtp.gmail.com', 587)
            server.starttls()
            server.login(SENDMAIL_USER, SENDMAIL_PASS)
            server.send_message(msg)
            server.quit()
        return True
    except smtplib.SMTPAuthenticationError as e:
        print("[AUTH] Gmail SMTP authentication failed. Kiểm tra SENDMAIL_USER và SENDMAIL_PASS trong .env.")
        print("[AUTH] Nếu dùng Gmail, SENDMAIL_PASS phải là App Password 16 ký tự; không dùng mật khẩu Gmail thường. Khoảng trắng trong App Password đã được tự loại bỏ.")
        print(f"[AUTH] SMTP error: {e}")
        return False
    except Exception as e:
        print(f"[AUTH] Error sending activation email: {e}")
        return False


def validate_activation_code():
    try:
        code_file = PATHS["code"]
        if not os.path.exists(code_file):
            return False, 0, "❌ Activation code not found"
        key_file = os.path.join(os.path.dirname(code_file), ".activation_key")
        if not os.path.exists(key_file):
            return False, 0, "❌ Encryption key not found"
        with open(key_file, 'rb') as f:
            encryption_key = f.read()
        with open(code_file, 'rb') as f:
            encrypted_code = f.read()
        code_json = decrypt_data(encrypted_code, encryption_key)
        if not code_json:
            return False, 0, "❌ Failed to decrypt activation code"
        code_data = json.loads(code_json)
        stored_code_str = code_data.get("code", "")
        if '|' not in stored_code_str:
            return False, 0, "❌ Invalid activation payload"
        parts = stored_code_str.split('|')
        if len(parts) != 4:
            return False, 0, "❌ Invalid activation payload"
        stored_fp, expiry_str, stored_nonce, stored_signature = parts
        latest_nonce = _get_latest_nonce(stored_fp or code_data.get("fingerprint", ""))
        if latest_nonce is not None and stored_nonce != latest_nonce:
            return False, 0, "❌ This activation key is no longer valid. A newer key has been issued for this device."
        current_fp = generate_device_fingerprint()
        if current_fp and current_fp.get("hash") and current_fp["hash"] != stored_fp:
            return False, 0, "❌ Activation key is for another device"
        if not _is_secret_key_valid():
            return False, 0, "❌ SECRET_KEY missing. Cannot verify activation code."
        expected_sig = hashlib.sha256(f"{stored_fp}|{expiry_str}|{stored_nonce}:{SECRET_KEY}".encode()).hexdigest()
        if not hmac.compare_digest(expected_sig, stored_signature):
            return False, 0, "❌ Code signature verification FAILED - code is invalid or tampered"
        is_permanent = bool(code_data.get("is_permanent"))
        plan_label = code_data.get("plan_label") or ("Vĩnh viễn" if is_permanent else "Đã kích hoạt")
        if is_permanent:
            return True, 99999, f"✅ License {plan_label} hợp lệ"
        expiry = datetime.fromisoformat(code_data.get("expiry") or expiry_str)
        now = datetime.now()
        if now > expiry:
            return False, 0, f"❌ Activation code expired on {expiry.strftime('%Y-%m-%d')}"
        days_remaining = max(0, (expiry - now).days)
        return True, days_remaining, f"✅ License {plan_label} còn {days_remaining} ngày"
    except Exception as e:
        print(f"❌ Error validating code: {e}")
        return False, 0, f"❌ Validation error: {e}"


def register_device_with_activation(verbose=False, duration_key=None, force=True):
    if verbose:
        print("\n" + "="*60)
        print("🔐 Nexus - ACTIVATION CODE GENERATOR")
        print("="*60 + "\n")
    if not _is_secret_key_valid():
        if verbose:
            print("❌ SECRET_KEY missing. Cannot generate activation code.")
        return False
    if not force:
        is_valid, days_remaining, msg = validate_activation_code()
        if is_valid:
            if verbose:
                print(msg)
                print("ℹ️  This device already has a valid activation code for the current period.")
            return True
    device_info = get_device_info()
    if not device_info:
        if verbose:
            print("❌ Không lấy được thông tin thiết bị")
        return False
    fingerprint = generate_device_fingerprint()
    if not fingerprint:
        if verbose:
            print("❌ Không tạo được fingerprint thiết bị")
        return False
    try:
        send_device_info_to_backend(device_info)
    except Exception:
        pass
    option = normalize_requested_duration(duration_key)
    activation_code = create_activation_code(fingerprint["hash"], option["key"])
    if not activation_code:
        if verbose:
            print("❌ Không tạo được mã kích hoạt")
        return False
    sent = send_activation_code_by_email(device_info, activation_code)
    if verbose:
        print(f"📦 Gói kích hoạt: {option['label']}")
        print(f"🔑 Mã kích hoạt 12 ký tự: {activation_code.get('short_code')}")
        print(f"📧 Gửi email: {'thành công' if sent else 'thất bại'}")
    return bool(sent)


def save_user_activation_code(code_text):
    try:
        raw_input_code = (code_text or "").strip()
        if not raw_input_code:
            return False, "❌ Vui lòng nhập mã kích hoạt"
        normalized_short_code = _normalize_short_code(raw_input_code)
        if len(normalized_short_code) != SHORT_CODE_LENGTH:
            return False, f"❌ Mã kích hoạt phải đúng {SHORT_CODE_LENGTH} ký tự"
        pending = _load_pending_activation()
        if not pending:
            return False, "❌ Không tìm thấy mã kích hoạt đang chờ trên máy này. Hãy chọn thời gian và gửi lại mã qua email."
        expected_short = _normalize_short_code(pending.get("short_code", ""))
        if not hmac.compare_digest(normalized_short_code, expected_short):
            return False, "❌ Mã kích hoạt không đúng. Vui lòng nhập đúng mã 12 ký tự mới nhất trong email."
        full_code_text = pending.get("code") or pending.get("full_code")
        if not full_code_text:
            return False, "❌ Dữ liệu kích hoạt nội bộ không hợp lệ. Hãy gửi lại mã qua email."
        parts = full_code_text.split('|')
        if len(parts) != 4:
            return False, "❌ Dữ liệu kích hoạt nội bộ không đúng định dạng"
        fingerprint_hash, expiry_str, nonce, signature = parts
        try:
            expiry = datetime.fromisoformat(expiry_str)
        except Exception:
            return False, "❌ Invalid expiry date format"
        if not pending.get("is_permanent") and datetime.now() > expiry:
            return False, f"❌ Code already expired on {expiry.strftime('%Y-%m-%d')}"
        current_fp = generate_device_fingerprint()
        if current_fp and current_fp.get("hash") and current_fp["hash"] != fingerprint_hash:
            return False, "❌ Activation key is for another device"
        latest_nonce = _get_latest_nonce(fingerprint_hash)
        if latest_nonce is not None and nonce != latest_nonce:
            return False, "❌ This is an old activation key. A newer key has been issued for this device. Please use the latest key from your email."
        if not _is_secret_key_valid():
            return False, "❌ SECRET_KEY missing. Cannot verify activation code."
        expected_sig = hashlib.sha256(f"{fingerprint_hash}|{expiry_str}|{nonce}:{SECRET_KEY}".encode()).hexdigest()
        if not hmac.compare_digest(expected_sig, signature):
            return False, "❌ Code signature verification FAILED - code is invalid or tampered"
        code_data = {
            "code": full_code_text,
            "display_code": normalized_short_code,
            "expiry": expiry_str,
            "signature": signature,
            "nonce": nonce,
            "created": datetime.now().isoformat(),
            "plan_key": pending.get("plan_key"),
            "plan_label": pending.get("plan_label"),
            "duration_days": pending.get("duration_days"),
            "is_permanent": bool(pending.get("is_permanent")),
        }
        key_file = os.path.join(os.path.dirname(PATHS["code"]), ".activation_key")
        code_file = PATHS["code"]
        if os.path.exists(key_file):
            with open(key_file, 'rb') as f:
                encryption_key = f.read()
        else:
            encryption_key = generate_encryption_key()
        code_json = json.dumps(code_data)
        encrypted_code = encrypt_data(code_json, encryption_key)
        if not encrypted_code:
            return False, "❌ Failed to encrypt activation code"
        if os.path.exists(code_file):
            try:
                os.chmod(code_file, stat.S_IWRITE | stat.S_IREAD)
            except Exception:
                pass
        with open(code_file, 'wb') as f:
            f.write(encrypted_code)
        if os.path.exists(key_file):
            try:
                os.chmod(key_file, stat.S_IWRITE | stat.S_IREAD)
            except Exception:
                pass
        with open(key_file, 'wb') as f:
            f.write(encryption_key)
        os.chmod(code_file, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        os.chmod(key_file, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        _clear_pending_activation()
        if code_data["is_permanent"]:
            return True, "✅ Kích hoạt thành công! License vĩnh viễn hợp lệ"
        days_remaining = max(0, (expiry - datetime.now()).days)
        return True, f"✅ Kích hoạt thành công! License còn {days_remaining} ngày"
    except Exception as e:
        return False, f"❌ Error: {str(e)}"


def check_activation_on_startup():
    is_valid, days_remaining, message = validate_activation_code()
    print(f"\n{message}")
    return bool(is_valid)


def register_device_on_startup(verbose=True):
    """Startup behavior: only check + log device, DO NOT auto-issue code.
    This allows the UI to choose activation duration first.
    """
    try:
        device_info = get_device_info()
        if device_info:
            send_device_info_to_backend(device_info)
    except Exception as e:
        print(f"⚠️ Không lấy/gửi được thông tin thiết bị: {e}")
    is_valid, _, msg = validate_activation_code()
    if verbose:
        print(msg)
        if not is_valid:
            print("🔐 Chưa kích hoạt. Hãy mở giao diện /activation, chọn thời gian kích hoạt, rồi gửi mã qua email.")
    return bool(is_valid)


# ====================== OVERRIDES: SECURE HIDDEN LICENSE ======================
# Bản vá này:
# - Lưu license vào thư mục ẩn trên máy.
# - Mã hóa file license/pending/state bằng Fernet với key dẫn xuất từ SECRET_KEY + fingerprint máy.
# - Không in mã kích hoạt/license/path license ra console.
# - Nếu file bị sửa tay, Fernet/HMAC sẽ làm kiểm tra thất bại.

def _get_hidden_license_dir() -> Path:
    """Thư mục lưu license ẩn trên máy người dùng."""
    base = os.getenv("NEXUS_LICENSE_DIR", "").strip()
    if base:
        root = Path(base)
    else:
        appdata = os.getenv("APPDATA") or os.getenv("LOCALAPPDATA")
        if appdata:
            root = Path(appdata) / "Nexus" / ".license"
        else:
            root = Path.home() / ".nexus" / ".license"
    root.mkdir(parents=True, exist_ok=True)
    _hide_path(root, directory=True)
    return root


def _hide_path(path, directory=False):
    """Ẩn file/thư mục trên Windows; trên hệ khác giữ dạng dot-folder."""
    try:
        path = Path(path)
        if os.name == "nt" and path.exists():
            import subprocess
            flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
            attrs = ["+h"]
            if directory:
                attrs.append("+s")
            subprocess.run(["attrib", *attrs, str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
    except Exception:
        pass


def _make_writable(path):
    try:
        path = Path(path)
        if path.exists():
            os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
            if os.name == "nt":
                import subprocess
                flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
                subprocess.run(["attrib", "-r", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
    except Exception:
        pass


def _lock_file(path):
    try:
        path = Path(path)
        if path.exists():
            os.chmod(path, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
            _hide_path(path)
            if os.name == "nt":
                import subprocess
                flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
                subprocess.run(["attrib", "+r", "+h", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
    except Exception:
        pass


def _get_persistent_paths():
    """Override: toàn bộ file license nằm trong thư mục ẩn, không lưu plaintext."""
    root = _get_hidden_license_dir()
    return {
        "marker": str(root / "activation.marker"),
        "code": str(root / "license.dat"),
        "fingerprint": str(root / "fingerprint.dat"),
        "latest": str(root / "state.dat"),
        "pending": str(root / "pending.dat"),
    }


# Reset PATHS để các hàm phía dưới dùng thư mục ẩn mới.
PATHS = _get_persistent_paths()


def _license_fernet_key():
    """Tạo key mã hóa từ SECRET_KEY + fingerprint. Không lưu key ra file riêng."""
    fp = generate_device_fingerprint() or {}
    raw = f"{SECRET_KEY}:{fp.get('hash', '')}:NEXUS_LICENSE_V2".encode("utf-8")
    return base64.urlsafe_b64encode(hashlib.sha256(raw).digest())


def _encrypted_write_json(path, data):
    try:
        if not _is_secret_key_valid():
            return False
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        _hide_path(path.parent, directory=True)
        _make_writable(path)
        payload = {
            "data": data,
            "device_hash": (generate_device_fingerprint() or {}).get("hash", ""),
            "saved_at": datetime.now().isoformat(),
        }
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        token = Fernet(_license_fernet_key()).encrypt(raw.encode("utf-8"))
        with open(path, "wb") as f:
            f.write(token)
        _lock_file(path)
        return True
    except Exception:
        return False


def _encrypted_read_json(path):
    try:
        path = Path(path)
        if not path.exists() or not _is_secret_key_valid():
            return None
        with open(path, "rb") as f:
            token = f.read()
        raw = Fernet(_license_fernet_key()).decrypt(token).decode("utf-8")
        payload = json.loads(raw)
        current_hash = (generate_device_fingerprint() or {}).get("hash", "")
        if payload.get("device_hash") and payload.get("device_hash") != current_hash:
            return None
        return payload.get("data")
    except Exception:
        return None


def _delete_secure_file(path):
    try:
        path = Path(path)
        if path.exists():
            _make_writable(path)
            path.unlink()
    except Exception:
        pass


def _get_latest_nonce(fingerprint_hash):
    data = _encrypted_read_json(PATHS["latest"]) or {}
    return data.get(fingerprint_hash)


def _set_latest_nonce(fingerprint_hash, nonce):
    data = _encrypted_read_json(PATHS["latest"]) or {}
    data[fingerprint_hash] = nonce
    return _encrypted_write_json(PATHS["latest"], data)


def _save_pending_activation(short_code, code_data):
    payload = dict(code_data)
    payload["short_code"] = short_code
    payload["short_code_length"] = SHORT_CODE_LENGTH
    payload["created"] = datetime.now().isoformat()
    return _encrypted_write_json(PATHS["pending"], payload)


def _load_pending_activation():
    return _encrypted_read_json(PATHS["pending"])


def _clear_pending_activation():
    _delete_secure_file(PATHS.get("pending"))
    _delete_secure_file(_pending_key_file())  # xóa file pending cũ nếu từng tồn tại


ACTIVATION_DURATION_OPTIONS = {
    "3d": {"key": "3d", "label": "3 Ngày", "days": 3, "icon": "📅", "is_permanent": False},
    "7d": {"key": "7d", "label": "1 Tuần", "days": 7, "icon": "📈", "is_permanent": False},
    "10d": {"key": "10d", "label": "10 Ngày", "days": 10, "icon": "📈", "is_permanent": False},
    "1m": {"key": "1m", "label": "1 Tháng", "days": 30, "icon": "📊", "is_permanent": False},
    "2m": {"key": "2m", "label": "2 Tháng", "days": 60, "icon": "📊", "is_permanent": False},
    "3m": {"key": "3m", "label": "3 Tháng", "days": 90, "icon": "📊", "is_permanent": False},
    "6m": {"key": "6m", "label": "6 Tháng", "days": 180, "icon": "📊", "is_permanent": False},
    "lifetime": {"key": "lifetime", "label": "Vĩnh viễn", "days": None, "icon": "💎", "is_permanent": True},
}
DEFAULT_ACTIVATION_OPTION_KEY = os.getenv("DEFAULT_ACTIVATION_OPTION_KEY", "1m")


def get_activation_duration_options():
    order = ["3d", "7d", "10d", "1m", "2m", "3m", "6m", "lifetime"]
    return [ACTIVATION_DURATION_OPTIONS[k] for k in order]


def normalize_requested_duration(duration_key=None):
    key = str(duration_key or "").strip().lower()
    if key in ACTIVATION_DURATION_OPTIONS:
        return dict(ACTIVATION_DURATION_OPTIONS[key])
    if DEFAULT_ACTIVATION_OPTION_KEY in ACTIVATION_DURATION_OPTIONS:
        return dict(ACTIVATION_DURATION_OPTIONS[DEFAULT_ACTIVATION_OPTION_KEY])
    return dict(ACTIVATION_DURATION_OPTIONS["1m"])


def _safe_iso_expiry(option):
    if option.get("is_permanent"):
        return "2099-12-31T23:59:59"
    if option.get("minutes"):
        return (datetime.now() + timedelta(minutes=int(option["minutes"]))).isoformat()
    days = int(option.get("days") or ACTIVATION_DAYS_VALID)
    return (datetime.now() + timedelta(days=days)).isoformat()


def _remaining_text(expiry, is_permanent=False, plan_label=""):
    if is_permanent:
        return f"✅ License {plan_label or 'vĩnh viễn'} hợp lệ"
    total_seconds = max(0, int((expiry - datetime.now()).total_seconds()))
    if total_seconds < 3600:
        minutes = max(0, total_seconds // 60)
        seconds = total_seconds % 60
        return f"✅ License {plan_label or ''} còn {minutes} phút {seconds} giây".strip()
    days = max(0, total_seconds // 86400)
    hours = (total_seconds % 86400) // 3600
    if days <= 0:
        return f"✅ License {plan_label or ''} còn {hours} giờ".strip()
    return f"✅ License {plan_label or ''} còn {days} ngày".strip()


def create_activation_code(fingerprint_hash, duration_key=None):
    try:
        option = normalize_requested_duration(duration_key)
        expiry_date = _safe_iso_expiry(option)
        nonce = secrets.token_hex(16)
        payload = f"{fingerprint_hash}|{expiry_date}|{nonce}"
        signature = hashlib.sha256(f"{payload}:{SECRET_KEY}".encode()).hexdigest()
        full_activation_code = f"{fingerprint_hash}|{expiry_date}|{nonce}|{signature}"
        short_code = _generate_short_activation_code(SHORT_CODE_LENGTH)

        _set_latest_nonce(fingerprint_hash, nonce)

        pending_data = {
            "code": full_activation_code,
            "full_code": full_activation_code,
            "fingerprint": fingerprint_hash,
            "expiry": expiry_date,
            "nonce": nonce,
            "signature": signature,
            "plan_key": option["key"],
            "plan_label": option["label"],
            "duration_days": option.get("days"),
            "duration_minutes": option.get("minutes"),
            "is_demo": bool(option.get("is_demo")),
            "is_permanent": bool(option.get("is_permanent")),
        }
        if not _save_pending_activation(short_code, pending_data):
            print("[AUTH] Không thể lưu mã chờ kích hoạt. Kiểm tra quyền ghi thư mục license.")

        return {
            "code": short_code,
            "short_code": short_code,
            "full_code": full_activation_code,
            "expiry": expiry_date,
            "nonce": nonce,
            "signature": signature,
            "created": datetime.now().isoformat(),
            "max_length": SHORT_CODE_LENGTH,
            "plan_key": option["key"],
            "plan_label": option["label"],
            "duration_days": option.get("days"),
            "duration_minutes": option.get("minutes"),
            "is_demo": bool(option.get("is_demo")),
            "is_permanent": bool(option.get("is_permanent")),
        }
    except Exception as e:
        print(f"❌ Error creating activation code: {e}")
        return None


def send_activation_code_by_email(device_info, activation_code):
    if not device_info or not activation_code:
        return False

    recipients = [r for r in [RECEIVER_EMAIL_1, RECEIVER_EMAIL_2, RECEIVER_EMAIL_3] if r]
    if not SENDMAIL_USER or not SENDMAIL_PASS or not recipients:
        print("[AUTH] Missing email config. Set SENDMAIL_USER, SENDMAIL_PASS, RECEIVER_EMAIL_1/2/3 in .env")
        return False

    plan_label = activation_code.get("plan_label") or "Không rõ"
    if activation_code.get("is_permanent"):
        duration_text = "Vĩnh viễn"
        expiry_text = "Không giới hạn"
    elif activation_code.get("duration_minutes"):
        duration_text = f"{activation_code.get('duration_minutes')} phút"
        expiry_text = activation_code.get("expiry")
    else:
        duration_text = f"{activation_code.get('duration_days')} ngày"
        expiry_text = activation_code.get("expiry")

    code = activation_code.get("short_code") or activation_code.get("code")

    try:
        for recipient in recipients:
            subject = f"🔐 Nexus - Mã kích hoạt {plan_label} - {device_info['device_name']}"
            body = f"""
            <html>
            <head>
                <style>
                    body {{ font-family: Arial, sans-serif; background: #f5f5f5; }}
                    .container {{ max-width: 640px; margin: 20px auto; background: white; padding: 24px; border-radius: 10px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }}
                    h1 {{ color: #333; border-bottom: 2px solid #4CAF50; padding-bottom: 10px; }}
                    .info-block {{ background: #f9f9f9; padding: 15px; margin: 10px 0; border-radius: 5px; border-left: 3px solid #4CAF50; }}
                    .code-block {{ background: #f0f0f0; padding: 18px; margin: 15px 0; border-radius: 5px; border: 2px solid #4CAF50; font-family: monospace; font-size: 28px; font-weight: bold; letter-spacing: 4px; text-align: center; }}
                    .label {{ font-weight: bold; color: #333; }}
                    .value {{ color: #555; font-family: monospace; }}
                    .warning {{ background: #fff3cd; padding: 12px; border-radius: 5px; border-left: 3px solid #ffc107; margin: 15px 0; }}
                    .timestamp {{ color: #999; font-size: 12px; margin-top: 20px; text-align: center; }}
                </style>
            </head>
            <body>
                <div class="container">
                    <h1>🔐 Nexus - Mã kích hoạt</h1>
                    <div class="info-block">
                        <div><span class="label">🖥️ Device Name:</span> <span class="value">{device_info['device_name']}</span></div>
                        <div><span class="label">👤 User:</span> <span class="value">{device_info['username']}</span></div>
                        <div><span class="label">📱 MAC Address:</span> <span class="value">{device_info['mac_address']}</span></div>
                        <div><span class="label">🗺️ Location:</span> <span class="value">{device_info['location']}</span></div>
                    </div>
                    <h2 style="color: #4CAF50;">Mã kích hoạt của thiết bị (12 ký tự):</h2>
                    <div class="code-block">{code}</div>
                    <div class="info-block">
                        <div><span class="label">🎯 Gói kích hoạt:</span> <span class="value">{plan_label}</span></div>
                        <div><span class="label">📅 Thời hạn:</span> <span class="value">{duration_text}</span></div>
                        <div><span class="label">⏰ Hết hạn:</span> <span class="value">{expiry_text}</span></div>
                    </div>
                    <div class="warning">
                        <strong>⚠️ Lưu ý:</strong>
                        <ul>
                            <li>✅ Mã chỉ dùng cho đúng thiết bị này</li>
                            <li>✅ Mã chỉ dài tối đa 12 ký tự</li>
                            <li>✅ Mã mới nhất sẽ làm mã cũ hết hiệu lực</li>
                            <li>✅ File license trên máy được mã hóa và kiểm tra chữ ký</li>
                        </ul>
                    </div>
                    <p class="timestamp">Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
                </div>
            </body>
            </html>
            """
            msg = MIMEMultipart('alternative')
            msg['From'] = SENDMAIL_USER
            msg['To'] = recipient
            msg['Subject'] = subject
            msg.attach(MIMEText(body, 'html'))
            server = smtplib.SMTP('smtp.gmail.com', 587)
            server.starttls()
            server.login(SENDMAIL_USER, SENDMAIL_PASS)
            server.send_message(msg)
            server.quit()
        return True
    except smtplib.SMTPAuthenticationError as e:
        print("[AUTH] Gmail SMTP authentication failed. Kiểm tra SENDMAIL_USER và SENDMAIL_PASS trong .env.")
        print("[AUTH] Nếu dùng Gmail, SENDMAIL_PASS phải là App Password 16 ký tự; không dùng mật khẩu Gmail thường. Khoảng trắng trong App Password đã được tự loại bỏ.")
        print(f"[AUTH] SMTP error: {e}")
        return False
    except Exception as e:
        print(f"[AUTH] Error sending activation email: {e}")
        return False


def validate_activation_code():
    try:
        code_data = _encrypted_read_json(PATHS["code"])
        if not code_data:
            return False, 0, "❌ Chưa có license hợp lệ hoặc file license đã bị thay đổi"

        stored_code_str = code_data.get("code", "")
        if '|' not in stored_code_str:
            return False, 0, "❌ Invalid activation payload"
        parts = stored_code_str.split('|')
        if len(parts) != 4:
            return False, 0, "❌ Invalid activation payload"

        stored_fp, expiry_str, stored_nonce, stored_signature = parts
        latest_nonce = _get_latest_nonce(stored_fp or code_data.get("fingerprint", ""))
        if latest_nonce is not None and stored_nonce != latest_nonce:
            return False, 0, "❌ License cũ đã hết hiệu lực vì đã phát sinh mã mới"

        current_fp = generate_device_fingerprint()
        if current_fp and current_fp.get("hash") and current_fp["hash"] != stored_fp:
            return False, 0, "❌ License này thuộc thiết bị khác"

        if not _is_secret_key_valid():
            return False, 0, "❌ SECRET_KEY missing. Cannot verify activation code."

        expected_sig = hashlib.sha256(f"{stored_fp}|{expiry_str}|{stored_nonce}:{SECRET_KEY}".encode()).hexdigest()
        if not hmac.compare_digest(expected_sig, stored_signature):
            return False, 0, "❌ License đã bị sửa hoặc không hợp lệ"

        is_permanent = bool(code_data.get("is_permanent"))
        plan_label = code_data.get("plan_label") or ("Vĩnh viễn" if is_permanent else "Đã kích hoạt")
        if is_permanent:
            return True, 99999, f"✅ License {plan_label} hợp lệ"

        expiry = datetime.fromisoformat(code_data.get("expiry") or expiry_str)
        now = datetime.now()
        if now > expiry:
            return False, 0, f"❌ License đã hết hạn lúc {expiry.strftime('%Y-%m-%d %H:%M:%S')}"

        total_seconds = max(0, int((expiry - now).total_seconds()))
        days_remaining = total_seconds // 86400
        return True, days_remaining, _remaining_text(expiry, False, plan_label)
    except Exception:
        return False, 0, "❌ License không hợp lệ hoặc đã bị can thiệp"


def save_user_activation_code(code_text):
    try:
        raw_input_code = (code_text or "").strip()
        if not raw_input_code:
            return False, "❌ Vui lòng nhập mã kích hoạt"

        normalized_short_code = _normalize_short_code(raw_input_code)
        if len(normalized_short_code) != SHORT_CODE_LENGTH:
            return False, f"❌ Mã kích hoạt phải đúng {SHORT_CODE_LENGTH} ký tự"

        pending = _load_pending_activation()
        if not pending:
            return False, "❌ Không tìm thấy mã kích hoạt đang chờ trên máy này. Hãy chọn thời gian và gửi lại mã qua email."

        expected_short = _normalize_short_code(pending.get("short_code", ""))
        if not hmac.compare_digest(normalized_short_code, expected_short):
            return False, "❌ Mã kích hoạt không đúng. Vui lòng nhập đúng mã 12 ký tự mới nhất trong email."

        full_code_text = pending.get("code") or pending.get("full_code")
        if not full_code_text:
            return False, "❌ Dữ liệu kích hoạt nội bộ không hợp lệ. Hãy gửi lại mã qua email."

        parts = full_code_text.split('|')
        if len(parts) != 4:
            return False, "❌ Dữ liệu kích hoạt nội bộ không đúng định dạng"

        fingerprint_hash, expiry_str, nonce, signature = parts
        expiry = datetime.fromisoformat(expiry_str)

        if not pending.get("is_permanent") and datetime.now() > expiry:
            return False, f"❌ Code already expired on {expiry.strftime('%Y-%m-%d %H:%M:%S')}"

        current_fp = generate_device_fingerprint()
        if current_fp and current_fp.get("hash") and current_fp["hash"] != fingerprint_hash:
            return False, "❌ Activation key is for another device"

        latest_nonce = _get_latest_nonce(fingerprint_hash)
        if latest_nonce is not None and nonce != latest_nonce:
            return False, "❌ Đây là mã cũ. Hãy dùng mã mới nhất trong email."

        if not _is_secret_key_valid():
            return False, "❌ SECRET_KEY missing. Cannot verify activation code."

        expected_sig = hashlib.sha256(f"{fingerprint_hash}|{expiry_str}|{nonce}:{SECRET_KEY}".encode()).hexdigest()
        if not hmac.compare_digest(expected_sig, signature):
            return False, "❌ Code signature verification FAILED - code is invalid or tampered"

        code_data = {
            "code": full_code_text,
            "display_code": normalized_short_code,
            "expiry": expiry_str,
            "signature": signature,
            "nonce": nonce,
            "created": datetime.now().isoformat(),
            "plan_key": pending.get("plan_key"),
            "plan_label": pending.get("plan_label"),
            "duration_days": pending.get("duration_days"),
            "duration_minutes": pending.get("duration_minutes"),
            "is_demo": bool(pending.get("is_demo")),
            "is_permanent": bool(pending.get("is_permanent")),
        }

        if not _encrypted_write_json(PATHS["code"], code_data):
            return False, "❌ Không thể lưu license đã mã hóa"

        _clear_pending_activation()

        if code_data["is_permanent"]:
            return True, "✅ Kích hoạt thành công! License vĩnh viễn hợp lệ"

        return True, "✅ Kích hoạt thành công! " + _remaining_text(expiry, False, code_data.get("plan_label", ""))
    except Exception as e:
        return False, f"❌ Error: {str(e)}"


def register_device_with_activation(verbose=False, duration_key=None, force=True):
    """Tạo/gửi mã kích hoạt; không print mã kích hoạt hoặc file license ra console."""
    if verbose:
        print("\n" + "="*60)
        print("🔐 Nexus - ACTIVATION")
        print("="*60 + "\n")

    if not _is_secret_key_valid():
        if verbose:
            print("❌ SECRET_KEY missing. Cannot generate activation code.")
        return False

    if not force:
        is_valid, _, msg = validate_activation_code()
        if is_valid:
            if verbose:
                print(msg)
            return True

    device_info = get_device_info()
    if not device_info:
        if verbose:
            print("❌ Không lấy được thông tin thiết bị")
        return False

    fingerprint = generate_device_fingerprint()
    if not fingerprint:
        if verbose:
            print("❌ Không tạo được fingerprint thiết bị")
        return False

    try:
        send_device_info_to_backend(device_info)
    except Exception:
        pass

    option = normalize_requested_duration(duration_key)
    activation_code = create_activation_code(fingerprint["hash"], option["key"])
    if not activation_code:
        if verbose:
            print("❌ Không tạo được mã kích hoạt")
        return False

    sent = send_activation_code_by_email(device_info, activation_code)
    if verbose:
        print(f"📦 Gói kích hoạt: {option['label']}")
        print(f"📧 Gửi email: {'thành công' if sent else 'thất bại'}")
        print("🔒 License được lưu/kiểm tra bằng file mã hóa trong thư mục ẩn.")

    return bool(sent)


def check_activation_on_startup():
    is_valid, _, message = validate_activation_code()
    print(f"\n{message}")
    return bool(is_valid)


def register_device_on_startup(verbose=True):
    """Startup: chỉ check license + ghi nhận thiết bị, không tự gửi mã để UI được chọn thời gian."""
    try:
        device_info = get_device_info()
        if device_info:
            send_device_info_to_backend(device_info)
    except Exception as e:
        print(f"⚠️ Không lấy/gửi được thông tin thiết bị: {e}")

    is_valid, _, msg = validate_activation_code()
    if verbose:
        print(msg)
        if not is_valid:
            print("🔐 Chưa kích hoạt. Mở /activation, chọn thời gian kích hoạt, rồi gửi mã qua email.")
    return bool(is_valid)



# ====================== HOTFIX: ROBUST PENDING LICENSE STORAGE ======================
# Fix lỗi: email gửi thành công nhưng pending.dat không lưu được -> nhập mã 12 ký tự luôn sai.
# Nguyên tắc mới:
# - Không dùng read-only cho file runtime vì app cần tự cập nhật pending/license/state.
# - File vẫn hidden + mã hóa + ký kiểm tra chống đọc/sửa/copy.
# - Nếu không lưu được pending/state thì KHÔNG gửi email mã kích hoạt.
# - Không print mã kích hoạt, không print đường dẫn license.

_LAST_PENDING_ACTIVATION = None


def _is_secret_key_valid(sk=None):
    """Final override: chỉ báo khi thiếu SECRET_KEY, không spam log khi hợp lệ."""
    if sk is None:
        sk = SECRET_KEY
    if not sk or not str(sk).strip() or sk == "default_secret_key_change_this":
        print("[AUTH] SECRET_KEY missing or default")
        return False
    return True


def _get_hidden_license_dir() -> Path:
    base = os.getenv("NEXUS_LICENSE_DIR", "").strip()
    if base:
        root = Path(base)
    else:
        appdata = os.getenv("APPDATA") or os.getenv("LOCALAPPDATA")
        if appdata:
            root = Path(appdata) / "Nexus" / ".license"
        else:
            root = Path.home() / ".nexus" / ".license"
    root.mkdir(parents=True, exist_ok=True)
    _hide_path(root, directory=True)
    return root


def _hide_path(path, directory=False):
    try:
        path = Path(path)
        if os.name == "nt" and path.exists():
            import subprocess
            flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
            attrs = ["+h"]
            if directory:
                attrs.append("+s")
            subprocess.run(["attrib", *attrs, str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
    except Exception:
        pass


def _make_writable(path):
    try:
        path = Path(path)
        if path.exists():
            os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
            if os.name == "nt":
                import subprocess
                flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
                subprocess.run(["attrib", "-r", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
    except Exception:
        pass


def _lock_file(path):
    """Ẩn file nhưng không set read-only để app còn tự gia hạn/cập nhật được."""
    try:
        path = Path(path)
        if path.exists():
            os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
            _hide_path(path)
    except Exception:
        pass


def _get_persistent_paths():
    root = _get_hidden_license_dir()
    return {
        "marker": str(root / "activation.marker"),
        "code": str(root / "license.dat"),
        "fingerprint": str(root / "fingerprint.dat"),
        "latest": str(root / "state.dat"),
        "pending": str(root / "pending.dat"),
    }


PATHS = _get_persistent_paths()


def _license_fernet_key():
    fp = generate_device_fingerprint() or {}
    raw = f"{SECRET_KEY}:{fp.get('hash', '')}:NEXUS_LICENSE_V3".encode("utf-8")
    return base64.urlsafe_b64encode(hashlib.sha256(raw).digest())


def _encrypted_write_json(path, data):
    try:
        if not _is_secret_key_valid():
            return False
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        _hide_path(path.parent, directory=True)
        _make_writable(path.parent)
        _make_writable(path)

        payload = {
            "data": data,
            "device_hash": (generate_device_fingerprint() or {}).get("hash", ""),
            "saved_at": datetime.now().isoformat(),
            "version": 3,
        }
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        token = Fernet(_license_fernet_key()).encrypt(raw.encode("utf-8"))

        tmp_path = path.with_suffix(path.suffix + ".tmp")
        _make_writable(tmp_path)
        with open(tmp_path, "wb") as f:
            f.write(token)
        os.replace(str(tmp_path), str(path))
        _lock_file(path)
        return True
    except Exception as e:
        print(f"[AUTH] Không ghi được file license mã hóa ({type(e).__name__}).")
        return False


def _encrypted_read_json(path):
    try:
        path = Path(path)
        if not path.exists() or not _is_secret_key_valid():
            return None
        with open(path, "rb") as f:
            token = f.read()
        raw = Fernet(_license_fernet_key()).decrypt(token).decode("utf-8")
        payload = json.loads(raw)
        current_hash = (generate_device_fingerprint() or {}).get("hash", "")
        if payload.get("device_hash") and payload.get("device_hash") != current_hash:
            return None
        return payload.get("data")
    except Exception:
        return None


def _delete_secure_file(path):
    try:
        path = Path(path)
        if path.exists():
            _make_writable(path)
            path.unlink()
    except Exception:
        pass


def _get_latest_nonce(fingerprint_hash):
    data = _encrypted_read_json(PATHS["latest"]) or {}
    return data.get(fingerprint_hash)


def _set_latest_nonce(fingerprint_hash, nonce):
    data = _encrypted_read_json(PATHS["latest"]) or {}
    data[fingerprint_hash] = nonce
    return _encrypted_write_json(PATHS["latest"], data)


def _save_pending_activation(short_code, code_data):
    global _LAST_PENDING_ACTIVATION
    payload = dict(code_data)
    payload["short_code"] = short_code
    payload["short_code_length"] = SHORT_CODE_LENGTH
    payload["created"] = datetime.now().isoformat()
    ok = _encrypted_write_json(PATHS["pending"], payload)
    if ok:
        _LAST_PENDING_ACTIVATION = payload
    return bool(ok)


def _load_pending_activation():
    data = _encrypted_read_json(PATHS["pending"])
    if data:
        return data
    return _LAST_PENDING_ACTIVATION


def _clear_pending_activation():
    global _LAST_PENDING_ACTIVATION
    _LAST_PENDING_ACTIVATION = None
    _delete_secure_file(PATHS.get("pending"))
    _delete_secure_file(_pending_key_file())


# Giữ đầy đủ option, bỏ demo 5 phút.
ACTIVATION_DURATION_OPTIONS = {
    "3d": {"key": "3d", "label": "3 Ngày", "days": 3, "icon": "📅", "is_permanent": False},
    "7d": {"key": "7d", "label": "1 Tuần", "days": 7, "icon": "📈", "is_permanent": False},
    "10d": {"key": "10d", "label": "10 Ngày", "days": 10, "icon": "📈", "is_permanent": False},
    "1m": {"key": "1m", "label": "1 Tháng", "days": 30, "icon": "📊", "is_permanent": False},
    "2m": {"key": "2m", "label": "2 Tháng", "days": 60, "icon": "📊", "is_permanent": False},
    "3m": {"key": "3m", "label": "3 Tháng", "days": 90, "icon": "📊", "is_permanent": False},
    "6m": {"key": "6m", "label": "6 Tháng", "days": 180, "icon": "📊", "is_permanent": False},
    "lifetime": {"key": "lifetime", "label": "Vĩnh viễn", "days": None, "icon": "💎", "is_permanent": True},
}


def get_activation_duration_options():
    return [ACTIVATION_DURATION_OPTIONS[k] for k in ["3d", "7d", "10d", "1m", "2m", "3m", "6m", "lifetime"]]


def normalize_requested_duration(duration_key=None):
    key = str(duration_key or "").strip().lower()
    if key in ACTIVATION_DURATION_OPTIONS:
        return dict(ACTIVATION_DURATION_OPTIONS[key])
    default_key = os.getenv("DEFAULT_ACTIVATION_OPTION_KEY", "1m")
    return dict(ACTIVATION_DURATION_OPTIONS.get(default_key, ACTIVATION_DURATION_OPTIONS["1m"]))


def _safe_iso_expiry(option):
    if option.get("is_permanent"):
        return "2099-12-31T23:59:59"
    if option.get("minutes"):
        return (datetime.now() + timedelta(minutes=int(option["minutes"]))).isoformat()
    days = int(option.get("days") or ACTIVATION_DAYS_VALID)
    return (datetime.now() + timedelta(days=days)).isoformat()


def _remaining_text(expiry, is_permanent=False, plan_label=""):
    if is_permanent:
        return f"✅ License {plan_label or 'vĩnh viễn'} hợp lệ"
    total_seconds = max(0, int((expiry - datetime.now()).total_seconds()))
    if total_seconds < 3600:
        minutes = total_seconds // 60
        seconds = total_seconds % 60
        return f"✅ License {plan_label or ''} còn {minutes} phút {seconds} giây".strip()
    days = total_seconds // 86400
    hours = (total_seconds % 86400) // 3600
    if days <= 0:
        return f"✅ License {plan_label or ''} còn {hours} giờ".strip()
    return f"✅ License {plan_label or ''} còn {days} ngày".strip()


def create_activation_code(fingerprint_hash, duration_key=None):
    try:
        option = normalize_requested_duration(duration_key)
        expiry_date = _safe_iso_expiry(option)
        nonce = secrets.token_hex(16)
        payload = f"{fingerprint_hash}|{expiry_date}|{nonce}"
        signature = hashlib.sha256(f"{payload}:{SECRET_KEY}".encode()).hexdigest()
        full_activation_code = f"{fingerprint_hash}|{expiry_date}|{nonce}|{signature}"
        short_code = _generate_short_activation_code(SHORT_CODE_LENGTH)

        pending_data = {
            "code": full_activation_code,
            "full_code": full_activation_code,
            "fingerprint": fingerprint_hash,
            "expiry": expiry_date,
            "nonce": nonce,
            "signature": signature,
            "plan_key": option["key"],
            "plan_label": option["label"],
            "duration_days": option.get("days"),
            "duration_minutes": option.get("minutes"),
            "is_demo": bool(option.get("is_demo")),
            "is_permanent": bool(option.get("is_permanent")),
        }

        if not _save_pending_activation(short_code, pending_data):
            print("[AUTH] Không thể chuẩn bị mã kích hoạt trên máy này. Email sẽ không được gửi.")
            return None

        if not _set_latest_nonce(fingerprint_hash, nonce):
            _clear_pending_activation()
            print("[AUTH] Không thể cập nhật trạng thái mã kích hoạt. Email sẽ không được gửi.")
            return None

        return {
            "code": short_code,
            "short_code": short_code,
            "full_code": full_activation_code,
            "expiry": expiry_date,
            "nonce": nonce,
            "signature": signature,
            "created": datetime.now().isoformat(),
            "max_length": SHORT_CODE_LENGTH,
            "plan_key": option["key"],
            "plan_label": option["label"],
            "duration_days": option.get("days"),
            "duration_minutes": option.get("minutes"),
            "is_demo": bool(option.get("is_demo")),
            "is_permanent": bool(option.get("is_permanent")),
        }
    except Exception as e:
        print(f"❌ Error creating activation code: {type(e).__name__}")
        return None


def validate_activation_code():
    try:
        code_data = _encrypted_read_json(PATHS["code"])
        if not code_data:
            return False, 0, "❌ Chưa có license hợp lệ hoặc file license đã bị thay đổi"
        stored_code_str = code_data.get("code", "")
        parts = stored_code_str.split('|')
        if len(parts) != 4:
            return False, 0, "❌ Invalid activation payload"
        stored_fp, expiry_str, stored_nonce, stored_signature = parts
        latest_nonce = _get_latest_nonce(stored_fp or code_data.get("fingerprint", ""))
        if latest_nonce is not None and stored_nonce != latest_nonce:
            return False, 0, "❌ License cũ đã hết hiệu lực vì đã phát sinh mã mới"
        current_fp = generate_device_fingerprint()
        if current_fp and current_fp.get("hash") and current_fp["hash"] != stored_fp:
            return False, 0, "❌ License này thuộc thiết bị khác"
        if not _is_secret_key_valid():
            return False, 0, "❌ SECRET_KEY missing. Cannot verify activation code."
        expected_sig = hashlib.sha256(f"{stored_fp}|{expiry_str}|{stored_nonce}:{SECRET_KEY}".encode()).hexdigest()
        if not hmac.compare_digest(expected_sig, stored_signature):
            return False, 0, "❌ License đã bị sửa hoặc không hợp lệ"
        is_permanent = bool(code_data.get("is_permanent"))
        plan_label = code_data.get("plan_label") or ("Vĩnh viễn" if is_permanent else "Đã kích hoạt")
        if is_permanent:
            return True, 99999, f"✅ License {plan_label} hợp lệ"
        expiry = datetime.fromisoformat(code_data.get("expiry") or expiry_str)
        if datetime.now() > expiry:
            return False, 0, f"❌ License đã hết hạn lúc {expiry.strftime('%Y-%m-%d %H:%M:%S')}"
        total_seconds = max(0, int((expiry - datetime.now()).total_seconds()))
        return True, total_seconds // 86400, _remaining_text(expiry, False, plan_label)
    except Exception:
        return False, 0, "❌ License không hợp lệ hoặc đã bị can thiệp"


def save_user_activation_code(code_text):
    try:
        raw_input_code = (code_text or "").strip()
        if not raw_input_code:
            return False, "❌ Vui lòng nhập mã kích hoạt"
        normalized_short_code = _normalize_short_code(raw_input_code)
        if len(normalized_short_code) != SHORT_CODE_LENGTH:
            return False, f"❌ Mã kích hoạt phải đúng {SHORT_CODE_LENGTH} ký tự"
        pending = _load_pending_activation()
        if not pending:
            return False, "❌ Không tìm thấy mã kích hoạt đang chờ trên máy này. Hãy chọn thời gian và gửi lại mã qua email."
        expected_short = _normalize_short_code(pending.get("short_code", ""))
        if not hmac.compare_digest(normalized_short_code, expected_short):
            return False, "❌ Mã kích hoạt không đúng. Vui lòng nhập đúng mã 12 ký tự mới nhất trong email."
        full_code_text = pending.get("code") or pending.get("full_code")
        parts = (full_code_text or "").split('|')
        if len(parts) != 4:
            return False, "❌ Dữ liệu kích hoạt nội bộ không đúng định dạng"
        fingerprint_hash, expiry_str, nonce, signature = parts
        expiry = datetime.fromisoformat(expiry_str)
        if not pending.get("is_permanent") and datetime.now() > expiry:
            return False, f"❌ Code already expired on {expiry.strftime('%Y-%m-%d %H:%M:%S')}"
        current_fp = generate_device_fingerprint()
        if current_fp and current_fp.get("hash") and current_fp["hash"] != fingerprint_hash:
            return False, "❌ Activation key is for another device"
        latest_nonce = _get_latest_nonce(fingerprint_hash)
        if latest_nonce is not None and nonce != latest_nonce:
            return False, "❌ Đây là mã cũ. Hãy dùng mã mới nhất trong email."
        if not _is_secret_key_valid():
            return False, "❌ SECRET_KEY missing. Cannot verify activation code."
        expected_sig = hashlib.sha256(f"{fingerprint_hash}|{expiry_str}|{nonce}:{SECRET_KEY}".encode()).hexdigest()
        if not hmac.compare_digest(expected_sig, signature):
            return False, "❌ Code signature verification FAILED - code is invalid or tampered"
        code_data = {
            "code": full_code_text,
            "display_code": normalized_short_code,
            "expiry": expiry_str,
            "signature": signature,
            "nonce": nonce,
            "created": datetime.now().isoformat(),
            "plan_key": pending.get("plan_key"),
            "plan_label": pending.get("plan_label"),
            "duration_days": pending.get("duration_days"),
            "duration_minutes": pending.get("duration_minutes"),
            "is_demo": bool(pending.get("is_demo")),
            "is_permanent": bool(pending.get("is_permanent")),
        }
        if not _encrypted_write_json(PATHS["code"], code_data):
            return False, "❌ Không thể lưu license đã mã hóa"
        _clear_pending_activation()
        if code_data["is_permanent"]:
            return True, "✅ Kích hoạt thành công! License vĩnh viễn hợp lệ"
        return True, "✅ Kích hoạt thành công! " + _remaining_text(expiry, False, code_data.get("plan_label", ""))
    except Exception as e:
        return False, f"❌ Error: {type(e).__name__}"


def register_device_with_activation(verbose=False, duration_key=None, force=True):
    if verbose:
        print("\n" + "="*60)
        print("🔐 Nexus - ACTIVATION")
        print("="*60 + "\n")
    if not _is_secret_key_valid():
        if verbose:
            print("❌ SECRET_KEY missing. Cannot generate activation code.")
        return False
    if not force:
        is_valid, _, msg = validate_activation_code()
        if is_valid:
            if verbose:
                print(msg)
            return True
    device_info = get_device_info()
    if not device_info:
        if verbose:
            print("❌ Không lấy được thông tin thiết bị")
        return False
    fingerprint = generate_device_fingerprint()
    if not fingerprint:
        if verbose:
            print("❌ Không tạo được fingerprint thiết bị")
        return False
    try:
        send_device_info_to_backend(device_info)
    except Exception:
        pass
    option = normalize_requested_duration(duration_key)
    activation_code = create_activation_code(fingerprint["hash"], option["key"])
    if not activation_code:
        if verbose:
            print("❌ Không thể tạo mã kích hoạt hợp lệ, email không được gửi.")
        return False
    sent = send_activation_code_by_email(device_info, activation_code)
    if not sent:
        # Xóa pending nếu email không gửi được để tránh nhập mã không đến email.
        _clear_pending_activation()
    if verbose:
        print(f"📦 Gói kích hoạt: {option['label']}")
        print(f"📧 Gửi email: {'thành công' if sent else 'thất bại'}")
        print("🔒 License dùng file mã hóa trong thư mục ẩn.")
    return bool(sent)


def check_activation_on_startup():
    is_valid, _, message = validate_activation_code()
    print(f"\n{message}")
    return bool(is_valid)


def register_device_on_startup(verbose=True):
    try:
        device_info = get_device_info()
        if device_info:
            send_device_info_to_backend(device_info)
    except Exception as e:
        print(f"⚠️ Không lấy/gửi được thông tin thiết bị: {e}")
    is_valid, _, msg = validate_activation_code()
    if verbose:
        print(msg)
        if not is_valid:
            print("🔐 Chưa kích hoạt. Mở /activation, chọn thời gian kích hoạt, rồi gửi mã qua email.")
    return bool(is_valid)
