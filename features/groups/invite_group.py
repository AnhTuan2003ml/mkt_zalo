import sys

def _safe_reconfigure():
    try:
        if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
            sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

_safe_reconfigure()

import json
import re
import time
import requests
from core.zalo.enc import zalo_encode
from core.zalo.dec import zalo_decode
from core.zalo.debugs import save
from core.zalo.zalo_config import get_zpw_ver
from core.zalo.zalo_headers import zalo_mobile_headers
def parse_grid_input(value: str) -> str:
    """
    Trích xuất group ID (số) từ input.
    - Nếu là link (https://zalo.me/g/708481543450629056) → trả về ID thuần
    - Nếu là số thuần (708481543450629056)               → trả về nguyên

    Args:
        value: link nhóm hoặc group ID (str)

    Returns:
        Group ID dạng số thuần, ví dụ: "708481543450629056"
    """
    value = value.strip()
    # Nếu là link → trích xuất ID từ path /g/<id>
    if value.startswith("http"):
        m = re.search(r"/g/(\d+)", value)
        if m:
            return m.group(1)
    # Nếu đã là số thuần
    if re.fullmatch(r"\d+", value):
        return value
    return value  # fallback — để API tự báo lỗi


def invite_group(grid: str, members: list, imei: str, zpw_enk: str, cookies: str, zpw_ver: str = None) -> tuple:
    """
    Mời thành viên vào nhóm Zalo.

    Args:
        grid:       Group ID hoặc link nhóm (tự nhận diện: link hoặc ID đều OK)
        members:    Danh sách userId cần mời (list str)
        imei:       IMEI thiết bị
        zpw_enk:    Key mã hóa Base64
        cookies:    Cookie string

    Returns:
        Tuple (response_json, decoded_data)
    """
    grid = parse_grid_input(grid)

    client_id = str(int(time.time() * 1000))

    payload = {
        "grid":        grid,
        "members":     members,
        "memberTypes": [-1] * len(members),
        "imei":        imei,
        "clientLang":  "vi",
    }

    plaintext = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    params = zalo_encode(plaintext, zpw_enk, url_encode=True)

    url = "https://tt-group-wpa.chat.zalo.me/api/group/invite/v2"
    query_params = {"zpw_ver": get_zpw_ver(zpw_ver), "zpw_type": "30"}

    headers = zalo_mobile_headers()

    cookie_dict = {}
    for item in cookies.split(";"):
        item = item.strip()
        if "=" in item:
            k, v = item.split("=", 1)
            cookie_dict[k] = v

    response = requests.post(
        url,
        params=query_params,
        data=f"params={params}",
        headers=headers,
        cookies=cookie_dict,
        timeout=30,
    )

    response_json = response.json()

    data_field = response_json.get("data", "")
    if isinstance(data_field, str) and data_field:
        decoded_data = zalo_decode(data_field, zpw_enk)
    else:
        decoded_data = data_field

    return response_json, decoded_data


if __name__ == "__main__":
    ZPW_ENK = "JD6nFp7NcReu4f0za71ygQ=="
    IMEI = "746f81b2-0da1-405a-aec6-133cdfa5cc33-b87543ecbc0ba610d9f06f9f2c432a46"
    # Nhập link đầy đủ HOẶC chỉ group ID — cả 2 đều được
    # GRID = "https://zalo.me/g/9025632183667716948"
    GRID = "708481543450629056"   # ← chỉ cần ID cũng OK
    MEMBERS = ["336411663026086078"]

    COOKIES = "zpw_sek=jW9K.456044704.a0.CcpAWzr-W2Xzpgup_7w0AAPSvKF_HA9YcKZkRgr9pakF6_KSkqxwIUfF-K-CGxqAe4KPz5iu4_qvVrv39HU0A0; _zlang=vn; zpdid=41Nya5VseZOR6fgPMVZAFnGLdv5PySq_; zlogin_session=kW4JGLyjCnIxFnDDLXTbH-Tj1a1V66f2xsqIK0PGP5gaA0b45L1kMgSl2aGMKMfMVG; zpsid=yDCF.456044704.8.77uYy3b_P3MPpqekDNzaDKmC5W4CILG13KXK0GBckBD_xB7HEA8FpL1_P3K; zpw_sek=jW9K.456044704.a0.CcpAWzr-W2Xzpgup_7w0AAPSvKF_HA9YcKZkRgr9pakF6_KSkqxwIUfF-K-CGxqAe4KPz5iu4_qvVrv39HU0A0; __zi=3000.SSZzejyD6zOgdh2mtnLQWYQN_RAG01ICFjIXe9fEM8Wxd-sWcqzOY7EPwgFGHbU7TfFWhp4u.1"

    print(f"Dang moi thanh vien vao nhom: {GRID}")
    response_json, decoded_data = invite_group(GRID, MEMBERS, IMEI, ZPW_ENK, COOKIES)

    save("invite_group_response.json", response_json)
    save("invite_group_decoded.json", decoded_data)

    error_code = decoded_data.get("error_code", 0) if isinstance(decoded_data, dict) else 0
    print(f"error_code: {error_code}")
    if error_code == 0:
        print("Moi thanh vien thanh cong!")
    else:
        print(f"Loi moi thanh vien: {error_code}")
