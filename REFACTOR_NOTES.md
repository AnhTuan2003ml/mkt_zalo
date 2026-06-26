# Refactor notes

## Mục tiêu

- Không gom toàn bộ source vào `utils/` nữa.
- Tách source theo tính năng/nhiệm vụ.
- Xóa trùng logic lấy thành viên giữa route Flask và lịch gửi.
- Chuẩn hóa luồng lấy thành viên: input URL -> resolve `groupId` -> lấy member bằng `groupId`.

## Thay đổi chính

### 1. Bỏ thư mục `utils/`

Các file cũ trong `utils/` đã được chuyển sang:

```text
core/zalo/                  # mã dùng chung Zalo API
features/accounts/          # tài khoản
features/groups/            # nhóm
features/members/           # thành viên nhóm
features/profiles/          # profile/user info/avatar
features/messaging/         # nhắn tin cá nhân/kết bạn
features/schedules/         # lịch gửi
features/tasks/             # task nền
```

### 2. Luồng lấy thành viên mới

File chính:

```text
features/members/group_member_service.py
```

Luồng bắt buộc:

```text
raw input
  ↓
normalize_group_input()
  ↓
Nếu là URL: features.groups.get_group.resolve_group_id_from_url() để lấy groupId
  ↓
features.members.get_members.get_members_by_group_id()
  → /api/group/getmg với payload {grids, avatar_size, mpage, mcount, imei}
  → tự động paginate
  ↓
Trả uidList, groupInfo, memberMap
```

Không lấy thành viên trực tiếp bằng URL nữa.

### 3. Đổi API endpoint lấy thành viên

Đã đổi từ payload cũ `{"grid": json.dumps({group_id: 0})}` sang payload mới:

```json
{
  "grids": ["group_id"],
  "avatar_size": 120,
  "member_avatar_size": 120,
  "mpage": 1,
  "mcount": 500,
  "imei": "imei"
}
```

Hàm `get_members_by_group_id()` tự động paginate:
- Bắt đầu từ `mpage=1`, mỗi page `mcount=500`.
- Gom UID từ `currentMems`, `memVerList`, `members`, `memberIds`.
- Nếu API trả `hasMoreMember=true` hoặc tổng member lớn hơn số đã lấy thì tăng page.
- Dừng nếu 2 trang liên tiếp không có UID mới (tránh lặp vô hạn).
- Trả về dict thống nhất: `{ok, group_id, members, uids, total, pages, raw_group_info, message}`.

Hàm `resolve_group_id_from_url()` trong `features/groups/get_group.py`:
- Chỉ resolve URL → groupId, không lấy members.
- Gọi `/api/group/link/ginfo` với payload `{link, avatar_size, mpage}`.
- Trả về: `{ok, group_id, group_info, message}`.

Tham số `imei` được truyền từ account qua chain:
`app.py → fetch_group_members_by_input(imei=imei) → fetch_group_member_uids(imei=imei) → get_members_by_group_id(imei=imei)`

### 4. Tách profile service

File chính:

```text
features/profiles/profile_service.py
```

Chịu trách nhiệm:

- lấy mini profile theo batch,
- fallback sang full profile nếu thiếu dữ liệu,
- build member rows trả về frontend.

### 5. Cập nhật import

Import cũ:

```python
from utils.get_members import get_members
```

Import mới:

```python
from features.members.get_members import get_members
```

Ví dụ trong `app.py`:

```python
from features.members.group_member_service import fetch_group_members_by_input, format_group_info
from features.profiles.profile_service import fetch_profiles_with_single_fallback, build_member_rows_from_uids
from core.zalo.zalo_config import get_zpw_ver
```

### 6. Build spec

`ZaloMemberTool.spec` đã được cập nhật hidden imports theo package mới `core.*` và `features.*`.

## Kiểm tra đã chạy

```bash
python -m compileall -q app.py core features authencation
```

Kết quả: compile OK.

Không test được API Zalo thật trong môi trường này vì thiếu cookies/zpwEnk/phiên đăng nhập thực tế.

## Sửa parser `/api/group/getmg` theo response thật

Response thật của endpoint `features/members/group_getmg.py` có dạng:

```text
DECODED.data[groupId].memberIds
```

Không phải chỉ `gridInfoMap[groupId]`. Vì vậy parser trong `features/members/get_members.py` đã được sửa để:

1. Nhận dạng cả `data[groupId]`, `data.gridInfoMap[groupId]`, `data.groups[groupId]`.
2. Ưu tiên lấy UID từ `memberIds` trước.
3. Chỉ fallback sang `currentMems`, `admins`, `members`, `memVerList` nếu không có `memberIds`.
4. Dừng phân trang khi số UID lấy được đã bằng `totalMember`, kể cả `hasMoreMember = 1`.
5. Log rõ số `memberIds` đọc được ở mỗi page.

Luồng hiện tại:

```text
URL nhóm -> groupId -> /api/group/getmg -> decoded.data[groupId].memberIds -> lấy profile UID
```

## Fix 2026-06-27 - Dừng ngay khi getmg trả lỗi quyền truy cập

Response thực tế của `/api/group/getmg` có thể trả:

```json
{
  "error_code": 164,
  "error_message": "Bạn không phải thành viên của nhóm này.",
  "data": null
}
```

Đã sửa `features/members/get_members.py`:

- Nếu `decoded.error_code != 0` thì trả lỗi ngay, không tăng `mpage`.
- Riêng lỗi `164` hoặc message chứa `không phải thành viên` sẽ báo rõ account hiện tại không phải thành viên nhóm/không có quyền xem danh sách thành viên.
- Không còn vòng lặp page 1..200 với cùng một lỗi.
- Vì service nhận `ok=False`, route `/run` sẽ dừng trước bước lấy profile.
