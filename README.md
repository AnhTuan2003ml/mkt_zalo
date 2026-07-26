# Nexus – Công cụ quản lý và tự động hóa Zalo

Nexus là ứng dụng chạy trên máy tính, hỗ trợ quản lý tài khoản Zalo, nhóm, thành viên và các chiến dịch chăm sóc khách hàng theo lịch. Dữ liệu cấu hình và tiến độ được lưu tại máy đang chạy ứng dụng.

## Tính năng chính

- Quản lý nhiều tài khoản Zalo và thông tin đăng nhập cần thiết.
- Xem, tạo và quản lý các nhóm cá nhân.
- Lấy danh sách thành viên từ link nhóm hoặc Group ID.
- Gửi tin nhắn theo nhóm hoặc theo số điện thoại.
- Tạo lịch chiến dịch và theo dõi tiến độ chạy nền.
- Tự động sao chép thành viên từ nhóm nguồn sang nhóm đích theo từng đợt.
- Lưu tiến độ tác vụ để có thể tiếp tục sau khi mở lại ứng dụng.
- Hiển thị chính sách người dùng và yêu cầu đồng ý trước khi sử dụng.

## Cách sử dụng

1. Khởi động ứng dụng Nexus.
2. Đọc và tích đồng ý chính sách người dùng, sau đó bấm **Đồng ý và tiếp tục**.
3. Kích hoạt phần mềm nếu hệ thống yêu cầu.
4. Vào **Quản lý tài khoản** để thêm hoặc mở tài khoản Zalo.
5. Chọn tính năng cần dùng trên thanh menu bên trái.
6. Với tác vụ tự động, kiểm tra tài khoản, dữ liệu đầu vào, thời gian và số lượng mỗi đợt trước khi bấm chạy.

### Tự động sao chép nhóm

1. Mở **Tự động sao chép nhóm**.
2. Chọn tài khoản thực hiện và dán link hoặc ID nhóm nguồn.
3. Chọn tạo nhóm mới hoặc chọn một nhóm đích có sẵn.
4. Đặt thời gian bắt đầu, số người mỗi đợt và khoảng cách giữa các đợt.
5. Xác nhận quyền mời thành viên rồi bấm **Chạy và lập lịch**.
6. Theo dõi, hủy, tiếp tục hoặc xem chi tiết trong danh sách tác vụ phía dưới.

> Người dùng phải tuân thủ pháp luật, chính sách của Zalo và các nền tảng liên quan; chỉ sử dụng dữ liệu, tài khoản và nhóm mà mình có quyền truy cập.

## Tài liệu chi tiết

- [Kiến trúc tổng quan](docs/ARCHITECTURE.md)
- [Danh mục tính năng](docs/FEATURES.md)
- [Tự động sao chép nhóm](docs/GROUP_COPY.md)
- [Chính sách người dùng](docs/USER_POLICY.md)
