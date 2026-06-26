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
from core.zalo.zalo_config import get_zpw_ver
from core.zalo.zalo_headers import zalo_mobile_headers
def send_sms(cookies: str, zpw_enk: str, to_uid: str, imei: str, message: str = "a",
             zpw_ver: str = None) -> tuple:
    """
    Gửi SMS (tin nhắn Zalo) tới user ID qua API Zalo Web.

    Args:
        cookies:  chuỗi cookie (dạng "; " join)
        zpw_enk:  key mã hóa Zalo Web
        to_uid:   ID người nhận
        imei:     IMEI thiết bị
        message:  nội dung tin nhắn (mặc định "a")

    Returns:
        Tuple (response_json, decoded_data)
    """
    client_id = int(time.time() * 1000)

    payload = {
        "message": message,
        "clientId": client_id,
        "imei": imei,
        "ttl": 0,
        "toid": to_uid,
    }

    plaintext = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    params = zalo_encode(plaintext, zpw_enk, url_encode=False)

    url = "https://tt-chat3-wpa.chat.zalo.me/api/message/sms"
    query_params = {"zpw_ver": get_zpw_ver(zpw_ver), "zpw_type": "30", "nretry": "0"}

    headers = zalo_mobile_headers()

    cookie_dict = {}
    for item in cookies.split(";"):
        item = item.strip()
        if "=" in item:
            k, v = item.split("=", 1)
            cookie_dict[k] = v

    print("[send_sms] CALL ZALO API")
    print("[send_sms] url=", url)
    print("[send_sms] query_params=", query_params)
    print("[send_sms] payload=", plaintext)
    print("[send_sms] to_uid=", to_uid)
    print("[send_sms] has_imei=", bool(imei))
    print("[send_sms] has_zpw_enk=", bool(zpw_enk))
    print("[send_sms] cookie_keys=", list(cookie_dict.keys()))

    response = requests.post(
        url,
        params=query_params,
        data={"params": params},
        headers=headers,
        cookies=cookie_dict,
        timeout=30,
    )

    print("[send_sms] final_url=", response.url)
    print("[send_sms] HTTP status=", response.status_code)
    print("[send_sms] response text=", response.text[:1000])
    try:
        response_json = response.json()
    except Exception as e:
        raise RuntimeError("Zalo response không phải JSON: " + response.text[:500]) from e

    # Zalo trả về data field encrypted – decode để debug
    data_field = response_json.get("data", "")
    if isinstance(data_field, str) and data_field:
        decoded_data = zalo_decode(data_field, zpw_enk)
    else:
        decoded_data = data_field

    return response_json, decoded_data


if __name__ == "__main__":
    ZPW_ENK = "ROuvpeouSknbSyk9e10lNg=="
    IMEI = "2a55cee6-010b-4829-9e21-27382c14df1a-b87543ecbc0ba610d9f06f9f2c432a46"
    TO_UID = "1276248986905994283"
    MESSAGE = "a"

    COOKIES = "zpw_sek=Pamp.246594988.a0.fWPCzx0oxMGSZrzgaJBjPCiGY0-I2Cys_qUR5DLTc3VRN8ytoLdK0O42WIhN3z16pGbqk3PqVh5KCvbpI5ljP0; __zi=3000.SSZzejyD6zOgdh2mtnLQWYQN_RAG01ICFjIXe9fEM8uwd-Yhca5LZtANvwRIIL2DSPpjeZ8p.1; zpdid=41N_arNne3eP79YLM_x8FXiRbvHM_C8v; _zlang=vn; zlogin_session=kW4JGLyjCnIxFnDDLXTbH-Tj1a1V5cH5w6mSLm5HQbsZAGT34b5WNQ0k14CHK6HHVG; zpsid=aI3N.246594988.33.hXqJWPo_mVKm0dFxaB_1zUdCiyUeYVJCg8RvpQSc7NFQB1GHdSlM3VM_mVK; zpw_sek=Pamp.246594988.a0.fWPCzx0oxMGSZrzgaJBjPCiGY0-I2Cys_qUR5DLTc3VRN8ytoLdK0O42WIhN3z16pGbqk3PqVh5KCvbpI5ljP0"

    print(f"Đang gửi SMS tới: {TO_UID}")
    response_json, decoded_data = send_sms(COOKIES, ZPW_ENK, TO_UID, IMEI, MESSAGE)

    with open("sms_response.json", "w", encoding="utf-8") as f:
        json.dump(response_json, f, ensure_ascii=False, indent=2)
    with open("sms_decoded.json", "w", encoding="utf-8") as f:
        json.dump(decoded_data, f, ensure_ascii=False, indent=2)

    error_code = decoded_data.get("error_code", 0) if isinstance(decoded_data, dict) else 0
    print(f"error_code: {error_code}")
    if error_code == 0:
        print("Gửi SMS thành công!")
    else:
        print(f"Lỗi gửi SMS: {error_code}")
