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
def cookies_to_dict(cookies: str) -> dict:
    """
    Chuyển cookie string thành dict cho requests.
    """
    cookie_dict = {}

    if not cookies:
        return cookie_dict

    for item in cookies.split(";"):
        item = item.strip()
        if "=" in item:
            k, v = item.split("=", 1)
            cookie_dict[k.strip()] = v.strip()

    return cookie_dict


def send_friend_request(
    toid: str,
    msg: str,
    imei: str,
    zpw_enk: str,
    cookies: str,
    proxy: str = None,
    zpw_ver: str = None
) -> tuple:
    """
    Gửi lời mời kết bạn Zalo.

    Args:
        toid:    ID người nhận lời mời.
        msg:     Nội dung lời mời.
        imei:    IMEI tài khoản gửi.
        zpw_enk: Key mã hóa Base64.
        cookies: Cookie string.
        proxy:   Proxy tùy chọn, ví dụ: http://user:pass@ip:port

    Returns:
        Tuple (response_json, decoded_data)
    """

    toid = str(toid or "").strip()
    msg = str(msg or "").strip()
    imei = str(imei or "").strip()

    if not toid:
        raise ValueError("toid khong duoc de trong")

    if not msg:
        raise ValueError("msg khong duoc de trong")

    if not imei:
        raise ValueError("imei khong duoc de trong")

    if not zpw_enk:
        raise ValueError("zpw_enk khong duoc de trong")

    if not cookies:
        raise ValueError("cookies khong duoc de trong")

    payload = {
        "toid": toid,
        "msg": msg,
        "reqsrc": 30,
        "imei": imei,
        "language": "vi",
        "srcParams": json.dumps(
            {"uidTo": toid},
            ensure_ascii=False,
            separators=(",", ":")
        )
    }

    plaintext = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    params = zalo_encode(plaintext, zpw_enk, url_encode=True)

    url = "https://tt-friend-wpa.chat.zalo.me/api/friend/sendreq"
    query_params = {
        "zpw_ver": get_zpw_ver(zpw_ver),
        "zpw_type": "30"
    }

    headers = zalo_mobile_headers()

    cookie_dict = cookies_to_dict(cookies)

    proxies = None
    if proxy:
        proxies = {
            "http": proxy,
            "https": proxy
        }

    print("[add_friend] CALL ZALO API")
    print("[add_friend] url=", url)
    print("[add_friend] query_params=", query_params)
    print("[add_friend] toid=", toid)
    print("[add_friend] has_imei=", bool(imei))
    print("[add_friend] has_zpw_enk=", bool(zpw_enk))
    print("[add_friend] cookie_keys=", list(cookie_dict.keys()))
    print("[add_friend] proxy=", proxies)

    response = requests.post(
        url,
        params=query_params,
        data=f"params={params}",
        headers=headers,
        cookies=cookie_dict,
        proxies=proxies,
        timeout=30
    )

    print("[add_friend] HTTP status=", response.status_code)
    print("[add_friend] response text=", response.text[:1000])

    try:
        response_json = response.json()
    except Exception as e:
        raise RuntimeError("Zalo response không phải JSON: " + response.text[:500]) from e

    data_field = response_json.get("data", "")
    if isinstance(data_field, str) and data_field:
        decoded_data = zalo_decode(data_field, zpw_enk)
    else:
        decoded_data = data_field

    return response_json, decoded_data


if __name__ == "__main__":
    ZPW_ENK = "O387DBPhxIloMhX4iJNUaA=="
    IMEI = "228878ef-77d1-4abe-b73a-e6ca3bcc64b0-90daa551604269dbcdcf237b5cc700f3"

    TOID = "336411663026086078"
    MSG = "Xin chào, mình là Ngọc Hiệp. Kết bạn với mình nhé!"

    COOKIES = (
        "_zlang=vn; _ga=GA1.2.584315698.1779208410; _ga_3EM8ZPYYN3=GS2.2.s1779208409$o1$g0$t1779208409$j60$l0$h0; zpsid=VodN.456044704.11.iFyoGo_bSg-FLLoq8-LohrgM09iQqqAR6z92cnHyheJtI7Q2BWpZeqNbSgy; zpw_sek=rbnz.456044704.a0.cmLxJcr7C17DznuAJ4Sm4HPbLNfFVH9UNdy7LIyNM6aaArzS1Hy4U2S9PN0iUWqp47ofpKQVdHL0Hjg0OIqm4G; __zi=3000.SSZzejyD6zOgdh2mtnLQWYQN_RAG01ICFjIXe9fEM8Wxd-sWcqzOY7EPwgFUJ5-7V9FegJKv.1; __zi-legacy=3000.SSZzejyD6zOgdh2mtnLQWYQN_RAG01ICFjIXe9fEM8Wxd-sWcqzOY7EPwgFUJ5-7V9FegJKv.1; app.event.zalo.me=2032538776634361020"
    )

    PROXY = None
    # PROXY = "http://user:pass@ip:port"

    print(f"Dang gui loi moi ket ban toi: {TOID}")
    print(f"Msg: {MSG}")

    response_json, decoded_data = send_friend_request(
        toid=TOID,
        msg=MSG,
        imei=IMEI,
        zpw_enk=ZPW_ENK,
        cookies=COOKIES,
        proxy=PROXY
    )

    save("send_friend_request_response.json", response_json)
    save("send_friend_request_decoded.json", decoded_data)

    print("response_json:")
    print(json.dumps(response_json, ensure_ascii=False, indent=2))

    print("decoded_data:")
    print(json.dumps(decoded_data, ensure_ascii=False, indent=2))

    error_code = decoded_data.get("error_code", 0) if isinstance(decoded_data, dict) else 0

    print(f"error_code: {error_code}")

    if error_code == 0:
        print("Gui loi moi ket ban thanh cong!")
    else:
        print(f"Loi gui loi moi ket ban: {error_code}")
