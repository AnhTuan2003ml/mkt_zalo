# Zalo Member Tool - Feature-based Refactor

Dự án đã được refactor lại theo hướng chia module theo tính năng, không còn gom toàn bộ source vào `utils/`.

## Cấu trúc chính

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
│   ├── groups/
│   ├── members/
│   ├── messaging/
│   ├── profiles/
│   ├── schedules/
│   └── tasks/
├── templates/
├── static/
├── authencation/
├── script/
├── ARCHITECTURE.md
├── REFACTOR_NOTES.md
└── ZaloMemberTool.spec
```

## Nhóm module

| Thư mục | Nhiệm vụ |
|---|---|
| `core/zalo/` | Mã dùng chung: encrypt/decrypt, header, config, debug |
| `features/accounts/` | Quản lý tài khoản, capture login info, profile tài khoản |
| `features/groups/` | API nhóm: lấy info nhóm, tạo nhóm, mời nhóm, gửi tin nhắn nhóm |
| `features/members/` | Lấy thành viên nhóm |
| `features/profiles/` | Mini profile, full profile fallback, avatar, user info, phone search |
| `features/messaging/` | Gửi tin nhắn cá nhân, kết bạn |
| `features/schedules/` | Quản lý lịch gửi và worker chạy lịch |
| `features/tasks/` | Task chạy nền, progress, SSE |

## Luồng lấy thành viên nhóm

Luồng bắt buộc:

```text
Người dùng nhập URL nhóm hoặc groupId
        ↓
features.members.group_member_service.fetch_group_members_by_input()
        ↓
Nếu input là URL:
    features.groups.get_group.resolve_group_id_from_url()
    -> resolve URL thành groupId
        ↓
features.members.get_members.get_members_by_group_id()
    -> gọi /api/group/getmg với payload {grids, avatar_size, mpage, mcount, imei}
    -> tự động paginate nếu API trả hasMoreMember
        ↓
features.profiles.profile_service.fetch_profiles_with_single_fallback()
        ↓
features.profiles.profile_service.build_member_rows_from_uids()
        ↓
Trả dữ liệu về UI hoặc lịch gửi
```

Điểm quan trọng:
- URL nhóm chỉ dùng để lấy `groupId`. Sau đó luôn gọi lấy thành viên bằng `groupId`.
- Endpoint lấy member: `POST https://tt-group-wpa.chat.zalo.me/api/group/getmg`
- Payload chuẩn: `{"grids": [group_id], "avatar_size": 120, "member_avatar_size": 120, "mpage": page, "mcount": 500, "imei": imei}`

## Import mới

Không dùng kiểu cũ:

```python
from utils.xxx import yyy
```

Dùng kiểu mới:

```python
from features.members.group_member_service import fetch_group_members_by_input
from features.profiles.profile_service import fetch_profiles_with_single_fallback
from core.zalo.zalo_config import get_zpw_ver
```

## Kiểm tra cú pháp

```bash
python -m compileall -q app.py core features authencation
```

## Build

File `ZaloMemberTool.spec` đã được cập nhật hidden imports theo cấu trúc mới.

### Ghi chú getmg/memberIds

Bản này đã sửa parser endpoint `/api/group/getmg` theo response thực tế: danh sách UID thành viên được lấy từ `decoded.data[groupId].memberIds`. Field `currentMems` chỉ dùng làm dữ liệu phụ vì thường chỉ chứa một vài member mẫu.

### Xử lý nhóm không có quyền truy cập

Nếu `/api/group/getmg` trả `error_code = 164` với nội dung `Bạn không phải thành viên của nhóm này`, tool sẽ dừng ngay tại page hiện tại và trả lỗi về giao diện. Tool không tiếp tục gọi page 2..200 và không gọi API lấy profile.
