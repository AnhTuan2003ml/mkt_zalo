import json
import requests

from core.zalo.enc import zalo_encode
from core.zalo.dec import zalo_decode
from core.zalo.debugs import save
from core.zalo.zalo_config import get_zpw_ver
from core.zalo.zalo_headers import zalo_mobile_headers


def parse_cookie_string(cookies: str) -> dict:
    """
    Chuyển cookie dạng browser string thành dict cho requests.
    """
    cookie_dict = {}
    for item in cookies.split(";"):
        item = item.strip()
        if "=" in item:
            key, value = item.split("=", 1)
            cookie_dict[key] = value
    return cookie_dict


def get_my_profile_v2(
    imei: str,
    zpw_enk: str,
    cookies: str,
    avatar_size: int = 120,
    zpw_ver: str = None,
    zpw_type: str = "30",
    os: str = "7",
    browser: str = "0",
    timeout: int = 30,
):
    """
    Gọi API:
    GET https://tt-profile-wpa.chat.zalo.me/api/social/profile/me-v2

    Payload trước mã hóa:
    {
      "avatar_size": 120,
      "imei": "..."
    }

    Lưu ý quan trọng:
    - Đây là GET query string.
    - Không URL-encode sẵn params nếu sau đó truyền vào requests.get(..., params=...),
      nếu không sẽ bị double encode.
    """

    payload = {
        "avatar_size": avatar_size,
        "imei": imei,
    }

    plaintext = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    # Với GET + requests.params:
    # để requests tự encode URL query, tránh double encode.
    encrypted_params = zalo_encode(plaintext, zpw_enk, url_encode=False)

    url = "https://tt-profile-wpa.chat.zalo.me/api/social/profile/me-v2"

    query_params = {
        "zpw_ver": get_zpw_ver(zpw_ver),
        "zpw_type": zpw_type,
        "params": encrypted_params,
        "os": os,
        "browser": browser,
    }

    headers = zalo_mobile_headers()

    cookie_dict = parse_cookie_string(cookies)

    response = requests.get(
        url,
        params=query_params,
        headers=headers,
        cookies=cookie_dict,
        timeout=timeout,
    )

    response.raise_for_status()
    response_json = response.json()

    data_field = response_json.get("data", "")

    if isinstance(data_field, str) and data_field:
        decoded_data = zalo_decode(data_field, zpw_enk)
    else:
        decoded_data = data_field

    return response_json, decoded_data


if __name__ == "__main__":
    import sys

    def _safe_reconfigure():
        try:
            if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
                sys.stdout.reconfigure(encoding='utf-8')
        except Exception:
            pass

    _safe_reconfigure()

    ZPW_ENK = "ROuvpeouSknbSyk9e10lNg=="

    IMEI = "746f81b2-0da1-405a-aec6-133cdfa5cc33-b87543ecbc0ba610d9f06f9f2c432a46"

    COOKIES = (
        "zpw_sek=Pamp.246594988.a0.fWPCzx0oxMGSZrzgaJBjPCiGY0-I2Cys_qUR5DLTc3VRN8ytoLdK0O42WIhN3z16pGbqk3PqVh5KCvbpI5ljP0; __zi=3000.SSZzejyD6zOgdh2mtnLQWYQN_RAG01ICFjIXe9fEM8uwd-Yhca5LZtANvwRIIL2DSPpjeZ8p.1; zpdid=41N_arNne3eP79YLM_x8FXiRbvHM_C8v; zpsid=aI3N.246594988.33.hXqJWPo_mVKm0dFxaB_1zUdCiyUeYVJCg8RvpQSc7NFQB1GHdSlM3VM_mVK; zpw_sek=Pamp.246594988.a0.fWPCzx0oxMGSZrzgaJBjPCiGY0-I2Cys_qUR5DLTc3VRN8ytoLdK0O42WIhN3z16pGbqk3PqVh5KCvbpI5ljP0; _zlang=vn"
    )

    print("[INFO] Đang gọi API profile/me-v2...")
    print(f"[INFO] IMEI: {IMEI}")

    raw_response, decoded_response = get_my_profile_v2(
        imei=IMEI,
        zpw_enk=ZPW_ENK,
        cookies=COOKIES,
        avatar_size=120,
    )

    save("profile_me_v2_raw.json", raw_response)
    save("profile_me_v2_decoded.json", decoded_response)

    print("\n[RAW RESPONSE]")
    print(json.dumps(raw_response, ensure_ascii=False, indent=2))

    print("\n[DECODED DATA]")
    print(json.dumps(decoded_response, ensure_ascii=False, indent=2))
