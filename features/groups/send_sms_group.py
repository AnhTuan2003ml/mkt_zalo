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
def cookie_string_to_dict(cookies: str) -> dict:
    cookie_dict = {}
    for item in cookies.split(";"):
        item = item.strip()
        if "=" in item:
            k, v = item.split("=", 1)
            cookie_dict[k] = v
    return cookie_dict


def send_group_msg(
    cookies: str,
    zpw_enk: str,
    grid: str,
    imei: str,
    message: str = "demo",
    ttl: int = 0,
    zpw_ver: str = None,
) -> tuple:
    """
    Gửi tin nhắn text vào nhóm Zalo qua API Zalo Web.

    Args:
        cookies: chuỗi cookie Zalo Web
        zpw_enk: key mã hóa AES Zalo Web
        grid: ID nhóm
        imei: IMEI phiên hiện tại
        message: nội dung tin nhắn
        ttl: thời gian tự hủy, mặc định 0

    Returns:
        Tuple (response_json, decoded_data)
    """

    client_id = int(time.time() * 1000)

    payload = {
        "message": message,
        "clientId": client_id,
        "imei": imei,
        "ttl": ttl,
        "visibility": 0,
        "grid": str(grid),
    }

    plaintext = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    params = zalo_encode(plaintext, zpw_enk, url_encode=True)

    url = "https://tt-group-wpa.chat.zalo.me/api/group/sendmsg"
    query_params = {
        "zpw_ver": get_zpw_ver(zpw_ver),
        "zpw_type": "30",
        "nretry": "0",
    }

    headers = zalo_mobile_headers()

    cookie_dict = cookie_string_to_dict(cookies)

    print("[GROUP SEND] Payload plaintext:")
    print(plaintext)
    print("[GROUP SEND] Params:")
    print(params[:150] + "...")

    response = requests.post(
        url,
        params=query_params,
        data=f"params={params}",
        headers=headers,
        cookies=cookie_dict,
        timeout=30,
    )

    print("[GROUP SEND] Status:", response.status_code)
    print("[GROUP SEND] Raw response:", response.text[:500])

    response.raise_for_status()

    response_json = response.json()

    data_field = response_json.get("data", "")
    if isinstance(data_field, str) and data_field:
        decoded_data = zalo_decode(data_field, zpw_enk)
    else:
        decoded_data = data_field

    return response_json, decoded_data


if __name__ == "__main__":
    ZPW_ENK = "O387DBPhxIloMhX4iJNUaA=="
    IMEI = "228878ef-77d1-4abe-b73a-e6ca3bcc64b0-90daa551604269dbcdcf237b5cc700f3"
    GRID = "9025632183667716948"
    MESSAGE = "test_group"

    COOKIES = (
        "_zlang=vn; _ga=GA1.2.584315698.1779208410; _ga_3EM8ZPYYN3=GS2.2.s1779208409$o1$g0$t1779208409$j60$l0$h0; zpsid=VodN.456044704.11.iFyoGo_bSg-FLLoq8-LohrgM09iQqqAR6z92cnHyheJtI7Q2BWpZeqNbSgy; zpw_sek=rbnz.456044704.a0.cmLxJcr7C17DznuAJ4Sm4HPbLNfFVH9UNdy7LIyNM6aaArzS1Hy4U2S9PN0iUWqp47ofpKQVdHL0Hjg0OIqm4G; __zi=3000.SSZzejyD6zOgdh2mtnLQWYQN_RAG01ICFjIXe9fEM8Wxd-sWcqzOY7EPwgFUJ5-7V9FegJKv.1; __zi-legacy=3000.SSZzejyD6zOgdh2mtnLQWYQN_RAG01ICFjIXe9fEM8Wxd-sWcqzOY7EPwgFUJ5-7V9FegJKv.1; app.event.zalo.me=2032538776634361020"
    )

    print(f"Đang gửi tin nhắn vào nhóm: {GRID}")

    response_json, decoded_data = send_group_msg(
        cookies=COOKIES,
        zpw_enk=ZPW_ENK,
        grid=GRID,
        imei=IMEI,
        message=MESSAGE,
        ttl=0,
    )

    save("group_send_response.json", response_json)
    save("group_send_decoded.json", decoded_data)

    print("\n=== DECODED ===")
    print(json.dumps(decoded_data, indent=2, ensure_ascii=False))

    error_code = decoded_data.get("error_code", 0) if isinstance(decoded_data, dict) else 0

    if error_code == 0:
        print("Gửi tin nhắn nhóm thành công!")
    else:
        print(f"Lỗi gửi tin nhắn nhóm: {error_code}")
