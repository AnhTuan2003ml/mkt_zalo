import base64
import json
from urllib.parse import unquote
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad

ZERO_IV = bytes.fromhex("00000000000000000000000000000000")

def b64_or_b64url_decode(s: str) -> bytes:
    s = s.strip().replace("-", "+").replace("_", "/")
    s += "=" * (-len(s) % 4)
    return base64.b64decode(s)

def zalo_decode(ciphertext_b64: str, zpw_enk: str):
    ciphertext_b64 = unquote(ciphertext_b64.strip())

    key = b64_or_b64url_decode(zpw_enk)
    if len(key) not in (16, 24, 32):
        raise ValueError(f"Invalid AES key length: {len(key)} bytes")

    ciphertext = base64.b64decode(ciphertext_b64)
    if len(ciphertext) % 16 != 0:
        raise ValueError(f"Invalid ciphertext length: {len(ciphertext)} bytes, not multiple of 16")

    cipher = AES.new(key, AES.MODE_CBC, ZERO_IV)
    plaintext = unpad(cipher.decrypt(ciphertext), AES.block_size)

    text = plaintext.decode("utf-8")
    return json.loads(text)

