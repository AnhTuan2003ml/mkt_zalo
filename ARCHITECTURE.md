# Cấu trúc dự án sau khi tách theo tính năng

Bản này bỏ cách gom toàn bộ source vào `utils/`. Source được chia thành 2 nhóm chính:

- `core/`: mã dùng chung, không chứa nghiệp vụ màn hình.
- `features/`: nghiệp vụ theo từng tính năng cụ thể.

## Cây thư mục chính

```text
.
├── app.py
├── core/
│   └── zalo/
│       ├── enc.py
│       ├── dec.py
│       ├── debugs.py
│       ├── zalo_config.py
│       └── zalo_headers.py
├── features/
│   ├── accounts/
│   │   ├── account_manager.py
│   │   ├── account_network_monitor.py
│   │   ├── get_loginInfo.py
│   │   └── profile_me_v2.py
│   ├── groups/
│   │   ├── add_group.py
│   │   ├── get_group.py
│   │   ├── group_manager.py
│   │   ├── invite_group.py
│   │   └── send_sms_group.py
│   ├── members/
│   │   ├── get_members.py
│   │   └── group_member_service.py
│   ├── messaging/
│   │   ├── add_friend.py
│   │   └── send_sms.py
│   ├── profiles/
│   │   ├── fetch_userinfo.py
│   │   ├── get_avata.py
│   │   ├── get_info.py
│   │   ├── get_single_profile.py
│   │   ├── profile_service.py
│   │   └── search_info_from_phone.py
│   ├── schedules/
│   │   ├── schedule_manager.py
│   │   └── schedule_worker.py
│   └── tasks/
│       └── task_manager.py
├── templates/
├── static/
├── authencation/
├── script/
└── ZaloMemberTool.spec
```

## Vai trò từng nhóm

### `core/zalo/`

Chứa các phần dùng chung cho nhiều API Zalo:

- `enc.py`: mã hóa request params.
- `dec.py`: giải mã response.
- `debugs.py`: ghi file debug.
- `zalo_config.py`: lấy `zpw_ver`.
- `zalo_headers.py`: header dùng khi gọi Zalo API.

### `features/accounts/`

Quản lý tài khoản, profile tài khoản đăng nhập và bắt thông tin từ Chrome DevTools Protocol.

### `features/groups/`

Các nghiệp vụ liên quan đến nhóm Zalo:

- lấy thông tin nhóm,
- tạo nhóm,
- mời vào nhóm,
- gửi tin nhắn vào nhóm,
- quản lý danh sách nhóm cá nhân.

### `features/members/`

Chuyên xử lý lấy thành viên nhóm.

Luồng bắt buộc:

```text
URL nhóm / groupId đầu vào
        ↓
group_member_service.normalize_group_input()
        ↓
Nếu là URL: get_group.py resolve_group_id_from_url() -> groupId
        ↓
get_members.py get_members_by_group_id()
    -> /api/group/getmg với payload {grids, avatar_size, mpage, mcount, imei}
    -> tự động paginate, gom member từ currentMems / memVerList / members / memberIds
        ↓
Trả uidList + groupInfo + memberMap
```

Điểm quan trọng:
- URL không còn được dùng để lấy members trực tiếp. URL chỉ dùng để chuyển thành `groupId`, sau đó luôn lấy members bằng `groupId`.
- `get_members_by_group_id()` trả về dict thống nhất: `{ok, group_id, members, uids, total, pages, raw_group_info, message}`
- Nếu API trả `hasMoreMember` hoặc tổng member lớn hơn số đã lấy, tự động tăng `mpage`.
- Dừng nếu 2 trang liên tiếp không có UID mới (tránh lặp vô hạn).

### `features/profiles/`

Lấy thông tin người dùng/thành viên:

- mini profile batch,
- full profile fallback,
- avatar,
- tìm thông tin từ số điện thoại.

### `features/messaging/`

Các nghiệp vụ gửi tin nhắn cá nhân và kết bạn.

### `features/schedules/`

Quản lý lịch gửi và worker chạy lịch.

### `features/tasks/`

Quản lý task chạy nền, progress và SSE.

## Luồng lấy thành viên hiện tại

```text
app.py / schedule_worker.py
        ↓
features.members.group_member_service.fetch_group_members_by_input()
        ↓
Nếu input là URL:
    features.groups.get_group.resolve_group_id_from_url()
    -> groupId (không lấy members từ URL)
        ↓
features.members.get_members.get_members_by_group_id()
    -> /api/group/getmg với grids payload
    -> tự động paginate đến khi hết member hoặc không còn UID mới
        ↓
features.profiles.profile_service.fetch_profiles_with_single_fallback()
        ↓
features.profiles.profile_service.build_member_rows_from_uids()
        ↓
Trả dữ liệu ra UI hoặc lịch gửi
```

Endpoint lấy member: `POST https://tt-group-wpa.chat.zalo.me/api/group/getmg`
Payload: `{"grids": [group_id], "avatar_size": 120, "member_avatar_size": 120, "mpage": page, "mcount": 500, "imei": imei}`

## Nguyên tắc import mới

Không import kiểu cũ:

```python
from utils.xxx import yyy
```

Dùng import theo package mới:

```python
from features.members.group_member_service import fetch_group_members_by_input
from features.profiles.profile_service import fetch_profiles_with_single_fallback
from core.zalo.zalo_config import get_zpw_ver
```
