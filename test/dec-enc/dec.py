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

if __name__ == "__main__":
    import os
    script_dir = os.path.dirname(os.path.abspath(__file__))
    ZPW_ENK = "miIvnDDleNQI11rNYxjQUw=="

    cipher_path = os.path.join(script_dir, "CIPHER.txt")
    with open(cipher_path, "r", encoding="utf-8") as f:
        CIPHER = f.read().strip()

    result = zalo_decode(CIPHER, ZPW_ENK)

    decrypted_path = os.path.join(script_dir, "decrypted.txt")
    with open(decrypted_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print("Giải mã thành công!")
    print(json.dumps(result, indent=2, ensure_ascii=False)[:2000])