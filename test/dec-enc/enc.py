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


if __name__ == "__main__":
    script_dir = os.path.dirname(os.path.abspath(__file__))

    # Thay bằng zpw_enk lấy từ response getLoginInfo đã decode.
    ZPW_ENK = "ROuvpeouSknbSyk9e10lNg=="

    # Đọc decrypted.txt
    decrypted_path = os.path.join(script_dir, "decrypted.txt")
    if not os.path.exists(decrypted_path):
        raise FileNotFoundError(f"Không tìm thấy {decrypted_path}")

    with open(decrypted_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    plaintext = json.dumps(data, ensure_ascii=False, separators=(",", ":"))

    print("=== PLAINTEXT ===")
    print(plaintext)

    encrypted = zalo_encode(plaintext, ZPW_ENK, url_encode=False)

    print("\n=== ENCRYPTED BASE64 ===")
    print(encrypted)

    cipher_path = os.path.join(script_dir, "CIPHER.txt")
    with open(cipher_path, "w", encoding="utf-8") as f:
        f.write(encrypted)

    print(f"\nĐã lưu vào: {cipher_path}")