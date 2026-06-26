import sys

def _safe_reconfigure():
    try:
        if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
            sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

_safe_reconfigure()

import json
import time
import requests
from core.zalo.enc import zalo_encode
from core.zalo.dec import zalo_decode
from core.zalo.debugs import save
from core.zalo.zalo_config import get_zpw_ver
from core.zalo.zalo_headers import zalo_mobile_headers
def create_group(gname: str, members: list, zpw_enk: str, cookies: str, imei: str, zpw_ver: str = None) -> tuple:
    """
    Tạo nhóm Zalo với danh sách thành viên.

    Args:
        gname:     Tên nhóm
        members:   Danh sách userId (list str)
        zpw_enk:   Key mã hóa Base64
        cookies:   Cookie string
        imei:      IMEI thiết bị

    Returns:
        Tuple (response_json, decoded_data)
    """
    client_id = str(int(time.time() * 1000))

    # memberTypes mặc định -1 cho mỗi thành viên
    member_types = [-1] * len(members)

    payload = {
        "clientId":    int(client_id),
        "gname":       gname,
        "gdesc":       None,
        "members":     members,
        "memberTypes": member_types,
        "nameChanged": 1,
        "createLink":  1,
        "clientLang":  "vi",
        "imei":        imei,
        "groupType":   1,
        "zsource":     601,
    }

    plaintext = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    print(f"[DEBUG] Plaintext: {plaintext}")
    params = zalo_encode(plaintext, zpw_enk, url_encode=True)

    url = "https://tt-group-wpa.chat.zalo.me/api/group/create/v2"
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
    GNAME = "Demo1"
    MEMBERS = ["4169937595301144779","336411663026086078"]

    COOKIES = "zpw_sek=jW9K.456044704.a0.CcpAWzr-W2Xzpgup_7w0AAPSvKF_HA9YcKZkRgr9pakF6_KSkqxwIUfF-K-CGxqAe4KPz5iu4_qvVrv39HU0A0; _zlang=vn; zpdid=41Nya5VseZOR6fgPMVZAFnGLdv5PySq_; zlogin_session=kW4JGLyjCnIxFnDDLXTbH-Tj1a1V66f2xsqIK0PGP5gaA0b45L1kMgSl2aGMKMfMVG; zpsid=yDCF.456044704.8.77uYy3b_P3MPpqekDNzaDKmC5W4CILG13KXK0GBckBD_xB7HEA8FpL1_P3K; zpw_sek=jW9K.456044704.a0.CcpAWzr-W2Xzpgup_7w0AAPSvKF_HA9YcKZkRgr9pakF6_KSkqxwIUfF-K-CGxqAe4KPz5iu4_qvVrv39HU0A0; __zi=3000.SSZzejyD6zOgdh2mtnLQWYQN_RAG01ICFjIXe9fEM8Wxd-sWcqzOY7EPwgFGHbU7TfFWhp4u.1"

    print(f"Đang tạo nhóm: {GNAME}")
    response_json, decoded_data = create_group(GNAME, MEMBERS, ZPW_ENK, COOKIES, IMEI)

    save("add_group_response.json", response_json)
    save("add_group_decoded.json", decoded_data)

    error_code = decoded_data.get("error_code", 0) if isinstance(decoded_data, dict) else 0
    print(f"error_code: {error_code}")
    if error_code == 0:
        print("Tạo nhóm thành công!")
        if isinstance(decoded_data, dict):
            print(f"Group ID: {decoded_data.get('data', {}).get('groupId', 'N/A')}")
    else:
        print(f"Lỗi tạo nhóm: {error_code}")
