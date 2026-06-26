import base64
import json
import os
from urllib.parse import quote
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

ZERO_IV = bytes.fromhex("00000000000000000000000000000000")


def b64_or_b64url_decode(s: str) -> bytes:
    """
    Decode Base64 hoặc Base64URL.
    Dùng cho zpw_enk.
    """
    s = s.strip().replace("-", "+").replace("_", "/")
    s += "=" * (-len(s) % 4)
    return base64.b64decode(s)


def zalo_encode(plaintext: str, zpw_enk: str, url_encode: bool = False) -> str:
    """
    Mã hóa plaintext theo logic Zalo Web encodeAES.

    Args:
        plaintext: chuỗi JSON/plaintext cần mã hóa
        zpw_enk: key dạng Base64/Base64URL
        url_encode: True nếu muốn output an toàn để đưa vào query/body form

    Returns:
        Base64 ciphertext string, hoặc URL-encoded ciphertext nếu url_encode=True
    """
    key = b64_or_b64url_decode(zpw_enk)

    if len(key) not in (16, 24, 32):
        raise ValueError(f"Invalid AES key length: {len(key)} bytes")

    cipher = AES.new(key, AES.MODE_CBC, ZERO_IV)
    padded = pad(plaintext.encode("utf-8"), AES.block_size)
    ciphertext = cipher.encrypt(padded)

    output = base64.b64encode(ciphertext).decode("utf-8")

    if url_encode:
        return quote(output, safe="")

    return output
