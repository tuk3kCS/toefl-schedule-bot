# Telegram bot theo dõi TOEFL ITP trên máy tính tại Hà Nội

Dự án chạy định kỳ bằng GitHub Actions, đọc lịch thi công khai của IIG Việt Nam và chỉ gửi Telegram khi phát hiện lịch **TOEFL ITP - trên máy tính** mới tại **Hà Nội**.

## API đã được xác minh

Ngày 14/10/2026, endpoint được xác minh trực tiếp từ HTML, JavaScript và phản hồi mạng của trang [Lịch thi tiếng Anh IIG](https://online.iigvietnam.com/lich-thi-tieng-anh):

- `GET https://online.iigvietnam.com/api/iig/Catalog/ExamEnglish`
- `GET https://online.iigvietnam.com/api/iig/Catalog/Area?isTopik=false`
- `GET https://online.iigvietnam.com/api/iig/ExamCalendarEnglish/GetList`

Trang web truyền cấu hình `examEndpoint: "Catalog/ExamEnglish"` và `getListEndpoint: "ExamCalendarEnglish/GetList"`, rồi gọi endpoint danh sách với các tham số `exam`, `area`, `headerQuarterId`, `status`, `dateTest`, `lang`, `pageIndex`, `pageSize`.

Danh mục thực tế tại thời điểm xác minh có:

- Bài thi `TOEFL-ITP-OL` — `TOEFL ITP - trên máy tính`.
- Khu vực `HANOI` — `Hà Nội`.

Bot không hard-code UUID. Mỗi lần chạy, bot lấy lại danh mục và giải mã UUID từ hai mã ổn định ở trên. Endpoint này không phải API công khai có tài liệu cam kết ổn định; IIG có thể thay đổi nó. Khi cấu trúc hoặc đường dẫn thay đổi, bot sẽ dừng với lỗi rõ ràng và **không** cập nhật state.

## Vì sao vẫn cần state tối thiểu?

GitHub Actions là các máy chạy tạm thời. Nếu không giữ bất kỳ trạng thái nào, mỗi lần chạy không thể biết một lịch đã được báo ở lần trước hay chưa. Hai lựa chọn khi đó đều không đúng: hoặc báo lại toàn bộ mỗi lần, hoặc không báo gì ở lần đầu và tiếp tục coi mọi lần sau là “lần đầu”.

Dự án chỉ lưu `.state/iig_toefl_itp_hanoi.json`, gồm:

- chữ ký bộ lọc;
- danh sách SHA-256 của ID lịch đã thấy (tối đa 500 hash);
- thời điểm cập nhật.

File này **không lưu toàn bộ lịch thi**, địa chỉ, ngày thi hay nội dung Telegram. GitHub Actions commit file state sau khi Telegram gửi thành công. Lần chạy đầu chỉ tạo baseline và không gửi các lịch cũ. Nếu nhiều lịch cùng xuất hiện giữa hai lần chạy, bot chỉ gửi một tin cho lịch có ngày/giờ xa nhất và ghi nhận toàn bộ hash mới.

Giới hạn: Telegram và Git không có giao dịch chung. Nếu Telegram đã nhận tin nhưng bước `git push` state thất bại, lần chạy sau có thể gửi trùng một lần. `concurrency` trong workflow ngăn hai lượt chạy chồng nhau, nhưng không thể loại bỏ hoàn toàn tình huống hiếm này nếu không dùng một kho dữ liệu giao dịch bên ngoài.

## Cấu hình Telegram

1. Nhắn `@BotFather` trên Telegram, chạy `/newbot`, rồi lấy bot token.
2. Mở bot vừa tạo và gửi `/start`.
3. Mở URL sau trong trình duyệt, thay `<TOKEN>` bằng token thật:

   `https://api.telegram.org/bot<TOKEN>/getUpdates`

4. Tìm `message.chat.id` trong JSON. Với nhóm, thêm bot vào nhóm, gửi một tin nhắn rồi xem lại `getUpdates`; chat ID nhóm thường là số âm.
5. Trong repository GitHub, vào **Settings → Secrets and variables → Actions → New repository secret** và tạo:

   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`

Không ghi token thật vào code, workflow, commit hoặc issue.

## Đưa dự án lên GitHub và chạy

1. Tạo repository và đẩy toàn bộ file này lên nhánh mặc định.
2. Bật GitHub Actions nếu repository đang tắt Actions.
3. Vào **Actions → Theo dõi TOEFL ITP máy tính tại Hà Nội → Run workflow**.
4. Lần chạy đầu tạo baseline. Từ lần sau, workflow chạy mỗi 15 phút và chỉ gửi khi có ID lịch mới.

Workflow cần quyền `contents: write` để commit state. Nếu organization/repository chặn quyền ghi của `GITHUB_TOKEN`, vào **Settings → Actions → General → Workflow permissions** và chọn **Read and write permissions**. Nếu không thể cấp quyền này, cần thay phần lưu state bằng một kho bền vững khác; bỏ state sẽ không thể chống gửi trùng giữa các máy chạy GitHub Actions.

## Biến tùy chọn

Các giá trị mặc định đã đúng với yêu cầu. Có thể tạo repository variables trong **Settings → Secrets and variables → Actions → Variables**:

- `IIG_EXAM_CODE` (mặc định `TOEFL-ITP-OL`)
- `IIG_EXAM_NAME` (mặc định `TOEFL ITP - trên máy tính`)
- `IIG_AREA_CODE` (mặc định `HANOI`)
- `IIG_AREA_NAME` (mặc định `Hà Nội`)
- `IIG_ONLY_OPEN` (`true` nếu chỉ muốn lịch đang mở; mặc định theo dõi cả mở và đóng)
- `NOTIFY_ON_FIRST_RUN` (`true` nếu muốn gửi lịch xa nhất ngay lần đầu; mặc định `false`)

IIG giới hạn khoảng ngày của request ở tối đa 60 ngày; bot dùng đúng giới hạn này.

## Chạy thử trên máy cá nhân

```bash
python -m venv .venv
python -m pip install -r requirements.txt
python bot.py --dry-run
python -m unittest discover -s tests -v
```

`--dry-run` gọi API thật, in lịch xa nhất khớp bộ lọc nhưng không gửi Telegram và không ghi state.

Để chạy thật ở máy cá nhân, đặt `TELEGRAM_BOT_TOKEN` và `TELEGRAM_CHAT_ID` trong biến môi trường rồi chạy:

```bash
python bot.py --state .state/iig_toefl_itp_hanoi.json
```
