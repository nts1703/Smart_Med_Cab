import ssl
import time
import pandas as pd
import requests
from requests.adapters import HTTPAdapter


# Tạo một custom adapter để hạ mức bảo mật SSL xuống, cho phép đọc khóa DH cũ của server
class SSLAdapter(HTTPAdapter):

  def init_poolmanager(self, *args, **kwargs):
    context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
    context.set_ciphers("DEFAULT:@SECLEVEL=1")
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE  # Tạm thời bỏ qua xác thực chứng chỉ
    kwargs["ssl_context"] = context
    return super().init_poolmanager(*args, **kwargs)


# Khởi tạo session và gán adapter
session = requests.Session()
session.mount("https://", SSLAdapter())

# 1. Cấu hình endpoint API
url = "https://ydct-dichvucong.moh.gov.vn/api/services/app/soDangKy/GetAllPublicServerPaging"

headers = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/javascript, */*",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}

all_data = []
skip_count = 0
page_size = 50  # Số lượng bản ghi mỗi lần gọi API
max_result_count = page_size

print("Đang tiến hành cào dữ liệu từ hệ thống Y Dược Cổ Truyền...")

while True:
  payload = {
      "filterText": "",  # Để trống nếu muốn cào toàn bộ danh sách thuốc
      "SoDangKyThuoc": {},
      "KichHoat": True,
      "skipCount": skip_count,
      "maxResultCount": max_result_count,
      "sorting": None,
  }

  try:
    # Gọi qua session đã cấu hình SSL custom
    response = session.post(url, json=payload, headers=headers, timeout=30)

    if response.status_code == 200:
      res_json = response.json()
      result_obj = res_json.get("result", {})
      items = result_obj.get("items", [])
      total_count = result_obj.get("totalCount", 0)

      if not items:
        break

      all_data.extend(items)
      print(f"Đã cào được {len(all_data)} / {total_count} bản ghi...")

      if len(all_data) >= total_count:
        break

      skip_count += page_size
      time.sleep(1)  # Nghỉ 1 giây giữa các request tránh nghẽn server
    else:
      print(f"Lỗi HTTP: {response.status_code}")
      break

  except Exception as e:
    print(f"Đã xảy ra lỗi: {e}")
    break

# 2. Xuất dữ liệu ra file Excel
if all_data:
  df = pd.DataFrame(all_data)
  output_file = "danh_sach_thuoc_ydct.xlsx"
  df.to_excel(output_file, index=False)
  print(
      f"Thành công! Đã lưu {len(all_data)} dòng dữ liệu vào file '{output_file}'."
  )
else:
  print("Không thu thập được dữ liệu nào.")