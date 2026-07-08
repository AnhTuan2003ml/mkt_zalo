# Activation Code System

## Overview

The activation code system generates secure, device-bound licenses with a 30-day expiry. Each code is:
- ✅ Device-specific (tied to MAC address, hostname, OS)
- ✅ Time-limited (30 days from generation)
- ✅ Immutable (cannot be modified after creation)
- ✅ Encrypted and securely stored
- ✅ Sent to 2 recipients via email

## Installation

### 1. Install Dependencies

```bash
pip install python-dotenv cryptography requests
```

### 2. Configure .env File

Copy `.env.example` to `.env` and update with your credentials:

```bash
cp .env.example .env
```

**Required fields:**
- `SENDMAIL_USER`: Gmail account for sending codes
- `SENDMAIL_PASS`: Gmail app password (NOT regular password)
- `RECEIVER_EMAIL_1`: First recipient (e.g., admin1@example.com)
- `RECEIVER_EMAIL_2`: Second recipient (e.g., admin2@example.com)
- `ACTIVATION_DAYS_VALID`: Days until license expires (default: 30)
- `SECRET_KEY`: Secret key for code signing (change in production!)

### 3. Gmail Setup

To use Gmail for sending emails:

1. Enable 2-Factor Authentication on your Gmail account
2. Generate an App Password:
   - Go to myaccount.google.com/apppasswords
   - Select "Mail" and "Windows Computer"
   - Copy the generated password
   - Use this in `SENDMAIL_PASS` (NOT your actual password)

## Usage

### First Run - Generate and Send Code

```python
from authencation.send_info_device import register_device_with_activation

# Generate activation code and send to recipients
register_device_with_activation()
```

Output:
```
============================================================
🔐 BYPASS VIDEO TOOL - ACTIVATION CODE GENERATOR
============================================================

📱 Device Information:
   🖥️  Device: MY-COMPUTER
   👤 User: john_doe
   📱 MAC: aa:bb:cc:dd:ee:ff
   🌐 IP: 192.168.1.100
   🗺️  Location: Ho Chi Minh, Vietnam
   💻 OS: Windows 10

✅ Device fingerprint: a1b2c3d4e5f6...

✅ Activation code generated
   📅 Valid until: 2026-07-03T10:30:45.123456
   🔐 Signature: 1a2b3c4d5e6f...

✅ Activation code saved securely (read-only)

📧 Sending activation code to recipients...

✅ Activation code sent to: admin1@example.com
✅ Activation code sent to: admin2@example.com

✅ Device activation completed successfully!
📧 Code sent to: admin1@example.com, admin2@example.com
```

### Subsequent Runs + Monthly Renewal Behavior

The system now automatically handles **per-device, time-limited keys**:

- On every app start, it checks if the **current device** has a valid (non-expired + matching fingerprint) activation code.
- If yes → use it, no new email.
- If the code has expired (or no code / wrong device), the app will:
  1. Automatically generate a **brand new unique key** for this exact device (new nonce + new expiry from "now + ACTIVATION_DAYS_VALID").
  2. Email the new key.
  3. Prompt the user to paste the new key.

This means:
- Same physical machine next month → completely different key.
- Keys cannot be shared between devices (enforced by fingerprint + signature).
- Key lifetime is strictly controlled by the `ACTIVATION_DAYS_VALID` value in `.env`.

```python
from authencation.send_info_device import check_activation_on_startup

# Check if license is still valid
is_valid = check_activation_on_startup()
```

Output (when valid):
```
✅ License valid for 27 more days
```

When expired on next startup:
- New key is emailed automatically.
- User pastes the fresh key for the new period.

## Activation Code Structure

Each (new) code looks like (includes nonce for verifiable signature, | separator):
```
a1b2c3d4e5f6789a0b1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3|2026-07-03T10:30:45.123456|1a2b3c4d5e6f7890|7f8e9d0c1b2a3f4e5d6c7b8a9f0e1d2c3b4a5f6e7d8c9b0a1f2e3d4c5b6a7c8d9e0f
```

Legacy 3-part codes (with : in expiry) are still accepted for backward compat:
```
a1b2c3d4e5f6789:2026-07-03T10:30:45.123456:7f8e9d0c1b2a3f4e5d6c7b8a9f0e1d2c3b4a5f6e7d8c9b0a1f2e3d4c5b6a7
```

Breaking down (new):
- **Device Fingerprint Hash** (full 64 hex): Device-specific
- **Expiry Date**: ISO (may contain :)
- **Nonce**: random for sig
- **Signature**: sha256( hash|expiry|nonce : SECRET_KEY )

Parser in save_user_activation_code handles both formats safely (never naive split on all : ).

## Security Features

### 1. Device Fingerprint
- Generated from: MAC address, hostname, OS version, username
- SHA-256 hashed
- Immutable (cannot be changed without hardware changes)
- Ensures code works only on the intended device

### 2. Encryption
- Uses Fernet (symmetric encryption)
- Encryption key stored separately in code file
- Code cannot be manually modified
- File permissions set to read-only after creation

### 3. Signature Verification
- HMAC-SHA256 signature with SECRET_KEY
- Prevents tampering with activation date
- Prevents code forgery

### 4. 30-Day Expiry
- License must be renewed every 30 days
- Clear expiry date in ISO format
- Server-side validation on startup

### 5. Storage Location
- Stored in: `%APPDATA%\ZaloMemberTool\.activation_code`
- Windows-specific AppData location (survives reinstalls)
- Read-only permissions prevent modification
- Separate from application files

## File Locations

```
%APPDATA%\ZaloMemberTool\
├── .activation_sent     # Marker file (indicates first run completed)
├── .activation_code     # Encrypted activation code
└── .device_fingerprint  # Device fingerprint info
```

## Email Templates

### Activation Code Email

Sent to both recipients with:
- Device name, username, MAC address, location
- Activation code (large, monospace font)
- Expiry date and validity period
- Security warnings
- Generation timestamp

## Troubleshooting

### Email Not Sending

**Error:** "SMTP connection failed"

**Solutions:**
1. Check Gmail credentials in `.env`
2. Verify Gmail App Password (not regular password)
3. Ensure 2FA is enabled on Gmail account
4. Check internet connection
5. Gmail may rate-limit - try again later

### Code File Not Found

**Error:** "❌ Activation code not found"

**Solutions:**
1. Run `register_device_with_activation()` first
2. Check if code file exists: `%APPDATA%\ZaloMemberTool\.activation_code`
3. Restore from backup if corrupted

### License Expired

**Error:** "❌ Activation code expired on 2026-07-03"

**Solution:**
1. Delete marker file: `%APPDATA%\ZaloMemberTool\.activation_sent`
2. Run `register_device_with_activation()` again
3. New code will be generated and sent

### Device Fingerprint Mismatch

**Error:** "❌ Device fingerprint mismatch"

**Cause:** Code was generated on a different device/OS

**Solution:**
1. Cannot transfer code between devices
2. Each device needs its own activation code
3. If hardware changed significantly, regenerate code

## Integration Example

```python
import sys
from authencation.send_info_device import check_activation_on_startup, register_device_with_activation

def main():
    # Check if first run
    import os
    appdata = os.path.expandvars(r"%APPDATA%\ZaloMemberTool")
    marker_file = os.path.join(appdata, ".activation_sent")
    
    if not os.path.exists(marker_file):
        print("🎉 Welcome to Nexus!")
        print("Generating activation code...\n")
        
        if not register_device_with_activation():
            print("❌ Failed to register device")
            sys.exit(1)
    else:
        # Check license validity
        if not check_activation_on_startup():
            print("❌ License expired or invalid")
            sys.exit(1)
    
    # Continue with main application
    print("\n✅ Application ready to use!\n")
    # ... your application code here

if __name__ == "__main__":
    main()
```

## Advanced Configuration

### Change Expiry Period

Edit `.env`:
```bash
ACTIVATION_DAYS_VALID=60  # 60 days instead of 30
```

### Add More Recipients

Modify `send_info_device.py`:
```python
recipients = [
    RECEIVER_EMAIL_1,
    RECEIVER_EMAIL_2,
    "new_recipient@example.com"  # Add more
]
```

### Custom Secret Key

For production, change `SECRET_KEY` in `.env`:
```bash
SECRET_KEY=$(openssl rand -base64 32)  # Generate random key
```

## Privacy & Security Notes

- ✅ Codes are device-bound and cannot be transferred
- ✅ No personal data stored beyond device info
- ✅ Email credentials stored in local `.env` file (not committed to git)
- ✅ Activation codes cannot be reverse-engineered
- ✅ Code files are read-only and encrypted
- ⚠️ Keep `.env` file secure (never commit to version control)
- ⚠️ Change `SECRET_KEY` before production deployment

## License

MIT License - See LICENSE file for details


## Cập nhật mã kích hoạt ngắn

- Mã gửi qua email và nhập trên giao diện chỉ dài tối đa 12 ký tự.
- Ví dụ: `A7K9P2XQ4MZ8`.
- Dữ liệu ký số đầy đủ vẫn được lưu nội bộ trên máy để check thiết bị, hạn dùng và chống dùng lại mã cũ.
