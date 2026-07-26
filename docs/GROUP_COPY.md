# Tự động sao chép nhóm

## Mục đích

Tính năng hỗ trợ lập lịch mời thành viên từ một nhóm nguồn vào nhóm đích mà người dùng có quyền quản lý. Hệ thống không mời toàn bộ cùng lúc mà chia thành từng đợt theo cấu hình.

## Dữ liệu cần nhập

- Tài khoản Zalo thực hiện.
- Link hoặc Group ID của nhóm nguồn.
- Nhóm đích:
  - **Tạo nhóm mới:** nhập tên nhóm. Đợt đầu tiên được dùng để tạo nhóm.
  - **Nhóm có sẵn:** chọn nhóm trong danh sách nhóm cá nhân.
- Thời gian bắt đầu.
- Số người mời trong mỗi đợt, tối đa 100.
- Khoảng cách giữa hai đợt, từ 1 phút đến 30 ngày.
- Xác nhận có quyền mời thành viên và tuân thủ chính sách nền tảng.

## Quy trình chạy

1. Hệ thống đọc nhóm nguồn và lấy danh sách UID thành viên.
2. Bổ sung tên, ảnh đại diện và loại bỏ UID trùng hoặc UID của tài khoản thực hiện.
3. Lưu tác vụ ở trạng thái chờ.
4. Khi đến giờ, worker lấy đúng số người của đợt hiện tại.
5. Nếu chọn tạo nhóm mới, đợt đầu tạo nhóm và lưu Group ID vừa tạo.
6. Các đợt tiếp theo mời vào Group ID đã lưu.
7. Kết quả được ghi riêng cho từng thành viên: thành công, lỗi hoặc đang chờ.
8. Nếu còn người đang chờ, hệ thống tự đặt giờ cho đợt kế tiếp.

## Trạng thái tác vụ

- **Đang chờ:** chưa đến thời gian hoặc đang chờ đợt tiếp theo.
- **Đang chạy:** worker đang gửi một đợt.
- **Hoàn tất:** không còn thành viên chờ và không có lỗi.
- **Hoàn tất một phần:** đã chạy hết nhưng có thành viên bị từ chối/lỗi.
- **Lỗi:** lỗi tài khoản, phiên đăng nhập hoặc API; có thể bấm tiếp tục sau khi xử lý.
- **Đã hủy:** dừng các đợt chưa chạy; có thể tiếp tục nếu còn thành viên chờ.

## Lưu trữ và khôi phục

Tác vụ được lưu trong `data/group_copy_jobs.json`. Tệp này được tạo khi chạy và không cần đưa vào bản vá. Nếu ứng dụng dừng giữa một đợt, tác vụ treo được đưa về trạng thái chờ sau khoảng an toàn để tránh chạy trùng tức thời.
