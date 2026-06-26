#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
SECRET KEY GENERATOR
Generates cryptographically secure secret keys for activation code signing.

Usage:
    python generate_secret_key.py              # Print to console
    python generate_secret_key.py --update     # Update .env file
    python generate_secret_key.py --file KEY   # Save to file
"""

import secrets
import base64
import sys
import os
from pathlib import Path

def generate_secret_key(length=32):
    """
    Generate cryptographically secure random secret key
    
    Args:
        length: Number of bytes for key (default 32 = 256 bits)
    
    Returns:
        Hex string of secret key
    """
    random_bytes = secrets.token_bytes(length)
    return random_bytes.hex()


def generate_secret_key_base64(length=32):
    """Generate secret key in base64 format"""
    random_bytes = secrets.token_bytes(length)
    return base64.b64encode(random_bytes).decode('utf-8')


def update_env_file():
    """Update .env file with new SECRET_KEY"""
    try:
        env_path = Path(__file__).parent.parent / ".env"
        
        if not env_path.exists():
            print(f"❌ .env file not found at: {env_path}")
            return False
        
        # Generate new key
        new_key = generate_secret_key()
        
        # Read current .env
        with open(env_path, 'r') as f:
            lines = f.readlines()
        
        # Find and replace SECRET_KEY line
        updated = False
        for i, line in enumerate(lines):
            if line.startswith('SECRET_KEY='):
                old_key = line.split('=')[1].strip()
                lines[i] = f"SECRET_KEY={new_key}\n"
                updated = True
                print(f"✅ Updated SECRET_KEY in .env")
                print(f"   Old: {old_key[:20]}...")
                print(f"   New: {new_key[:20]}...")
                break
        
        if not updated:
            print("⚠️  SECRET_KEY not found in .env, adding new line...")
            lines.append(f"\nSECRET_KEY={new_key}\n")
        
        # Write back to .env
        with open(env_path, 'w') as f:
            f.writelines(lines)
        
        print(f"✅ .env file updated successfully!")
        print(f"📁 Location: {env_path}")
        return True
        
    except Exception as e:
        print(f"❌ Error updating .env: {e}")
        return False


def save_to_file(filename):
    """Save SECRET_KEY to a file"""
    try:
        key = generate_secret_key()
        file_path = Path(__file__).parent / filename
        
        with open(file_path, 'w') as f:
            f.write(f"SECRET_KEY={key}\n")
        
        print(f"✅ SECRET_KEY saved to: {file_path}")
        print(f"   Key (first 32 chars): {key[:32]}...")
        return True
        
    except Exception as e:
        print(f"❌ Error saving file: {e}")
        return False


def display_info():
    """Display information about generated key"""
    print("\n" + "="*60)
    print("🔐 SECRET KEY GENERATOR - Activation Code Signing")
    print("="*60)
    print(f"""
PURPOSE:
  - Used for HMAC-SHA256 signing of activation codes
  - Prevents code forgery and tampering
  - Each deployment should have unique key

SECURITY:
  ✅ 256-bit cryptographically secure random
  ✅ Generated with secrets.token_bytes()
  ✅ Resistant to brute-force attacks
  ✅ Never hardcoded in application

KEY SPECIFICATIONS:
  - Format: Hexadecimal string (64 characters)
  - Entropy: 256 bits
  - Algorithm: HMAC-SHA256
  - Rotation: Every deployment or 1 year minimum
""")
    print("="*60 + "\n")


def main():
    """Main function"""
    display_info()
    
    if len(sys.argv) > 1:
        arg = sys.argv[1]
        
        if arg == '--update':
            print("🔄 Updating .env file with new SECRET_KEY...\n")
            update_env_file()
        elif arg == '--file' and len(sys.argv) > 2:
            filename = sys.argv[2]
            print(f"💾 Saving SECRET_KEY to {filename}...\n")
            save_to_file(filename)
        else:
            print(f"❌ Unknown argument: {arg}")
            print("Usage:")
            print("  python generate_secret_key.py              # Print to console")
            print("  python generate_secret_key.py --update     # Update .env")
            print("  python generate_secret_key.py --file KEY   # Save to file")
            sys.exit(1)
    else:
        # Default: Generate and print
        print("📝 Generated SECRET_KEY (Console Output):\n")
        
        print("HEX FORMAT (Recommended):")
        hex_key = generate_secret_key()
        print(f"  SECRET_KEY={hex_key}\n")
        
        print("BASE64 FORMAT (Alternative):")
        b64_key = generate_secret_key_base64()
        print(f"  SECRET_KEY={b64_key}\n")
        
        print("📋 Copy one of the above and paste into .env file\n")
        
        print("⚠️  IMPORTANT:")
        print("  1. Store SECRET_KEY securely (not in version control)")
        print("  2. Different SECRET_KEY for each environment")
        print("  3. Rotate key periodically")
        print("  4. Keep backup of old keys if needed for legacy codes")
        print("\n" + "="*60 + "\n")


if __name__ == "__main__":
    main()
