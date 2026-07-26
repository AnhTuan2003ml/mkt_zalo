# Kiến trúc tổng quan Nexus

Ứng dụng dùng Flask làm lớp giao diện và API nội bộ. Mã nguồn được chia theo nghiệp vụ để hạn chế việc dồn toàn bộ logic vào `app.py`.

## Thành phần chính

```text
app.py                 Điều phối route, kiểm tra chính sách/kích hoạt và nối các tính năng
core/zalo/              Mã hóa, giải mã, header và cấu hình dùng chung khi gọi Zalo
features/accounts/      Quản lý tài khoản và thông tin phiên đăng nhập
features/groups/        Nhóm, tạo nhóm, mời nhóm và tác vụ sao chép nhóm
features/members/       Đọc thành viên từ link nhóm hoặc Group ID
features/profiles/      Bổ sung tên, ảnh và thông tin hồ sơ
features/messaging/     Gửi tin nhắn và kết bạn
features/schedules/     Lịch chiến dịch và worker chạy nền
features/tasks/         Task nền, tiến độ và nhật ký
features/messages/      Dữ liệu hội thoại trong ứng dụng
templates/              Giao diện HTML
static/                 CSS, JavaScript, ảnh và biểu tượng
data/                   Dữ liệu runtime tại máy người dùng
```

## Luồng vào ứng dụng

```text
Mở ứng dụng
  → kiểm tra phiên bản chính sách đã đồng ý
  → nếu chưa đồng ý: chỉ cho truy cập màn hình chính sách
  → nếu đã đồng ý: kiểm tra kích hoạt
  → hợp lệ: mở giao diện làm việc và khởi động worker
```

Phiên bản chính sách được lưu trong `data/user_policy_acceptance.json`. Khi đổi phiên bản chính sách, người dùng phải xác nhận lại.

## Luồng lấy thành viên nhóm

```text
Link nhóm / Group ID
  → resolve về Group ID khi cần
  → gọi API lấy thành viên và phân trang
  → bổ sung hồ sơ thành viên
  → trả danh sách chuẩn hóa cho giao diện hoặc tác vụ nền
```

## Luồng sao chép nhóm

```text
Đọc nhóm nguồn
  → lọc UID hợp lệ và bỏ tài khoản đang thao tác
  → lưu tác vụ trong data/group_copy_jobs.json
  → worker nhận tác vụ đến hạn
  → tạo nhóm mới bằng đợt đầu hoặc mời vào nhóm có sẵn
  → ghi kết quả từng thành viên
  → đặt lịch đợt tiếp theo cho đến khi hoàn tất
```

Tác vụ được lưu bền vững. Nếu ứng dụng dừng giữa đợt, worker sẽ khôi phục tác vụ treo sau khoảng an toàn và tiếp tục từ các thành viên còn chờ.

## Nguyên tắc phát triển

- Nghiệp vụ mới đặt trong `features/<tính_năng>/`; `app.py` chỉ điều phối và kiểm tra dữ liệu đầu vào.
- Mã gọi Zalo dùng các tiện ích chung trong `core/zalo/`.
- Dữ liệu phát sinh khi chạy đặt trong `data/`, không ghi cứng vào mã nguồn.
- Tài liệu từng tính năng đặt trong `docs/`; README chỉ giới thiệu và hướng dẫn sử dụng chung.

Tài liệu nghiệp vụ chi tiết nằm trong [docs/FEATURES.md](docs/FEATURES.md).
