"""
A.Z.N.G. 授權金鑰驗證伺服器
部署到 Render，負責驗證用戶金鑰是否有效
"""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
import os
import json

app = FastAPI()

# ===== Google Sheets 設定 =====
SCOPE = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive"
]
SHEET_URL = os.environ.get("LICENSE_SHEET_URL", "")
CREDS_JSON = os.environ.get("GOOGLE_CREDS_JSON", "")  # JSON 字串，存在環境變數

def get_license_sheet():
    creds_dict = json.loads(CREDS_JSON)
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, SCOPE)
    client = gspread.authorize(creds)
    return client.open_by_url(SHEET_URL).worksheet("工作表1")

# ===== 驗證請求格式 =====
class LicenseRequest(BaseModel):
    license_key: str

# ===== 驗證 API =====
@app.post("/verify")
def verify_license(req: LicenseRequest):
    key = req.license_key.strip().upper()

    if not key.startswith("ANG-"):
        return {"valid": False, "reason": "金鑰格式錯誤"}

    try:
        sheet = get_license_sheet()
        records = sheet.get_all_records()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"無法連線到金鑰資料庫：{e}")

    for row in records:
        row_key = str(row.get("金鑰", "")).strip().upper()
        if row_key != key:
            continue

        status = str(row.get("狀態", "")).strip()
        expire_str = str(row.get("到期日", "")).strip()

        # 停用
        if status == "停用":
            return {"valid": False, "reason": "此金鑰已停用，請聯繫客服"}

        # 到期日檢查
        if expire_str:
            try:
                expire_date = datetime.strptime(expire_str, "%Y/%m/%d")
                if datetime.now() > expire_date:
                    return {"valid": False, "reason": "金鑰已到期，請續費"}
            except ValueError:
                pass  # 日期格式錯誤視為無期限

        # 有效
        return {"valid": True, "reason": "OK", "status": status}

    return {"valid": False, "reason": "金鑰不存在"}

# ===== 健康檢查 =====
@app.get("/")
def health_check():
    return {"status": "AZNG License Server Running"}
