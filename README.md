Hệ thống AIoT Giám sát Môi trường và Trợ lý Tương tác Giọng nói thời gian thực qua WebSockets
Tên đề tài : Trợ lý giám sát có tương tác giọng nói 2. Kiến trúc Phần cứng (Hardware)
Vi điều khiển trung tâm: ESP32 (Bắt buộc phải dùng dòng này để có đủ cấu hình chạy Wi-Fi, xử lý giao thức WebSockets và có hỗ trợ bộ giải mã I2S phần cứng).
Khối cảm biến (Sensors):
Cảm biến nhiệt độ & độ ẩm SHT31 (Giao tiếp I2C, độ chính xác cao hơn rất nhiều so với dòng DHT cũ).
Cảm biến ánh sáng (Quang trở hoặc BH1750).
Khối âm thanh (Audio I2S):
Bộ thu: Microphone I2S INMP441 (Dạng tròn, thu âm thanh số trực tiếp).
Bộ phát: Mạch khuếch đại Class D I2S MAX98357 kết hợp Loa 3W 8R. 3. Công nghệ Phần mềm (Software Stack)
Backend Framework: Django. Để xử lý giao thức WebSockets thời gian thực, bạn cần tích hợp thêm Django Channels.
Database: PostgreSQL.
Cloud & BaaS: Supabase (Sử dụng để host cơ sở dữ liệu PostgreSQL từ xa, giúp quản lý DB cực kỳ trực quan và tiện lợi).
AI Layer (LLM Free): Gemini API hoặc Groq API để xử lý ngôn ngữ tự nhiên, phân tích ý định của người dùng và bóc tách dữ liệu sang dạng JSON để kích hoạt Rule Engine.

4. Luồng vận hành chính của Hệ thống
   Luồng dữ liệu cảm biến (Real-time Monitoring): ESP32 liên tục đọc dữ liệu Nhiệt độ/Độ ẩm từ SHT31 và Ánh sáng, đóng gói thành các gói tin JSON nhỏ và bắn liên tục lên Django Server qua kết nối WebSockets (duy trì 24/7). Server nhận dữ liệu, lưu lịch sử vào PostgreSQL (Supabase) và cập nhật giao diện Dashboard của người dùng ngay lập tức.
   Luồng điều khiển bằng giọng nói (Voice AI & Rule Engine): Người dùng nói vào Mic INMP441 -> ESP32 gửi luồng dữ liệu âm thanh qua WebSockets lên Server -> Server chạy Speech-to-Text -> Gửi text sang LLM.
   Nếu lệnh là tức thời ("Bật đèn"): LLM dịch ra lệnh JSON, server lưu trạng thái vào DB và bắn tín hiệu WebSockets xuống ESP32 kích hoạt phần cứng.
   Nếu lệnh là thiết lập luật ("Nếu trời tối thì bật đèn"): LLM biên dịch thành cấu trúc logic, Server lưu luật này vào PostgreSQL. Vòng lặp của Server sẽ liên tục đối chiếu data cảm biến gửi lên với luật này để tự động ra lệnh cho ESP32 mà không cần LLM can thiệp lại.
   Luồng phản hồi âm thanh: Khi thực hiện xong lệnh hoặc khi có cảnh báo môi trường nguy hiểm (SHT31 báo nhiệt độ quá cao), Server chạy Text-to-Speech tạo file âm thanh, đẩy ngược qua WebSockets về ESP32 để giải mã qua mạch MAX98357 và phát ra loa 3W.
