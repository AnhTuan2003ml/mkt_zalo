# Chính sách người dùng

## Cách hoạt động

- Khi mở ứng dụng, mọi giao diện và API nghiệp vụ đều bị chặn nếu người dùng chưa đồng ý chính sách hiện hành.
- Màn hình chính sách yêu cầu tích xác nhận trước khi nút tiếp tục được bật.
- Sau khi đồng ý, ứng dụng lưu phiên bản và thời điểm xác nhận trong `data/user_policy_acceptance.json`.
- Khi `USER_POLICY_VERSION` thay đổi, người dùng phải đọc và đồng ý lại.
- Việc kích hoạt và khởi động các worker chỉ diễn ra sau bước đồng ý chính sách.

## Nội dung hiển thị

Nội dung chính sách chính thức được đặt trong `templates/policy.html`. Khi cập nhật nội dung có thay đổi nghĩa vụ hoặc phạm vi trách nhiệm, cần đồng thời tăng `USER_POLICY_VERSION` trong `app.py` để yêu cầu xác nhận lại.
