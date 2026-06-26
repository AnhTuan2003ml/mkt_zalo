MOBILE_WEB_USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 14; SM-S928B) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Mobile Safari/537.36"
)


def zalo_mobile_headers(extra=None):
    """Headers giống mobile browser/WebView cho các endpoint Zalo Web."""
    headers = {
        "accept": "application/json, text/plain, */*",
        "accept-language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
        "cache-control": "no-cache",
        "content-type": "application/x-www-form-urlencoded",
        "origin": "https://chat.zalo.me",
        "pragma": "no-cache",
        "priority": "u=1, i",
        "referer": "https://chat.zalo.me/",
        "sec-ch-ua": '"Android WebView";v="125", "Chromium";v="125", "Not.A/Brand";v="24"',
        "sec-ch-ua-mobile": "?1",
        "sec-ch-ua-platform": '"Android"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-site",
        "user-agent": MOBILE_WEB_USER_AGENT,
        "x-requested-with": "com.zing.zalo",
    }
    if extra:
        headers.update(extra)
    return headers
