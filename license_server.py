"""
A.Z.N.G. 授權金鑰驗證伺服器 v2
- 金鑰驗證
- LINE Webhook（記錄 User ID）
- 綠界付款通知（自動產生金鑰並發送）
"""

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime, timedelta
import os
import json
import random
import string
import requests as req_lib
import hashlib
import hmac
import base64

app = FastAPI()

# ===== 環境變數 =====
SCOPE = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive"
]
SHEET_URL = os.environ.get("LICENSE_SHEET_URL", "")
CREDS_JSON = os.environ.get("GOOGLE_CREDS_JSON", "")
LINE_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "")

# ===== Sheets 連線 =====
def get_license_sheet():
    creds_dict = json.loads(CREDS_JSON)
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, SCOPE)
    client = gspread.authorize(creds)
    return client.open_by_url(SHEET_URL).worksheet("工作表1")

# ===== 產生金鑰 =====
def generate_key():
    chars = string.ascii_uppercase + string.digits
    def seg():
        return ''.join(random.choices(chars, k=4))
    return f"ANG-{seg()}-{seg()}-{seg()}"

# ===== 發送 LINE 訊息 =====
def send_line_message(user_id: str, text: str):
    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_TOKEN}"
    }
    body = {
        "to": user_id,
        "messages": [{"type": "text", "text": text}]
    }
    resp = req_lib.post(url, headers=headers, json=body, timeout=10)
    return resp.status_code == 200

# ===== 寫入金鑰到 Sheets =====
def write_key_to_sheet(sheet, key, user_name, line_id, plan, expire_date=""):
    now = datetime.now().strftime("%Y/%m/%d")
    sheet.append_row([key, "啟用", user_name, line_id, now, expire_date, f"方案：{plan}"])

# ===== 金鑰驗證 API =====
class LicenseRequest(BaseModel):
    license_key: str

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
        if status == "停用":
            return {"valid": False, "reason": "此金鑰已停用，請聯繫客服"}
        if expire_str:
            try:
                expire_date = datetime.strptime(expire_str, "%Y/%m/%d")
                if datetime.now() > expire_date:
                    return {"valid": False, "reason": "金鑰已到期，請續費"}
            except ValueError:
                pass
        return {"valid": True, "reason": "OK", "status": status}
    return {"valid": False, "reason": "金鑰不存在"}

# ===== LINE Webhook =====
@app.post("/line/webhook")
async def line_webhook(request: Request):
    body = await request.body()
    data = json.loads(body)
    
    for event in data.get("events", []):
        if event.get("type") == "follow":
            # 用戶加好友，發送歡迎訊息
            user_id = event["source"]["userId"]
            send_line_message(user_id,
                "🎉 歡迎加入 A.Z.N.G. Threads 發文輔助系統！\n\n"
                "購買後請傳送「購買」取得專屬付款連結。\n"
                "如有任何問題歡迎直接詢問 🙌"
            )
        
        elif event.get("type") == "message":
            user_id = event["source"]["userId"]
            text = event["message"].get("text", "").strip()
            
            if text == "購買":
                # 回傳付款選項
                send_line_message(user_id,
                    "💳 請選擇方案：\n\n"
                    "🔹 試用轉訂閱：NT$299/月\n"
                    "🔹 新用戶首月：NT$399/月\n"
                    "🔹 正式月費：NT$499/月\n"
                    "🔹 加購帳號金鑰：NT$199\n\n"
                    "請回覆方案名稱，例如：「正式月費」"
                )
            
            elif text in ["試用轉訂閱", "新用戶首月", "正式月費", "加購帳號金鑰"]:
                price_map = {
                    "試用轉訂閱": "299",
                    "新用戶首月": "399",
                    "正式月費": "499",
                    "加購帳號金鑰": "199"
                }
                price = price_map[text]
                # 付款連結帶入 user_id 方便付款後對應
                pay_url = f"https://azng-license-server.onrender.com/pay?uid={user_id}&plan={text}&amount={price}"
                send_line_message(user_id,
                    f"✅ 您選擇的方案：{text}（NT${price}）\n\n"
                    f"請點擊以下連結完成付款：\n{pay_url}\n\n"
                    "付款完成後系統將自動發送金鑰給您 🎁"
                )
    
    return {"status": "ok"}

# ===== 綠界付款通知 =====
@app.post("/ecpay/notify")
async def ecpay_notify(request: Request):
    form = await request.form()
    data = dict(form)
    
    # 確認付款成功
    if data.get("RtnCode") != "1":
        return PlainTextResponse("0|error")
    
    # 從訂單備註取出 user_id 和 plan
    custom_field = data.get("CustomField1", "")
    try:
        parts = custom_field.split("|")
        user_id = parts[0]
        plan = parts[1]
    except Exception:
        return PlainTextResponse("0|error")
    
    try:
        # 產生金鑰
        key = generate_key()
        sheet = get_license_sheet()
        
        # 計算到期日（一個月後）
        expire = (datetime.now() + timedelta(days=30)).strftime("%Y/%m/%d")
        
        # 取用戶名稱（從 LINE API）
        user_name = "用戶"
        try:
            r = req_lib.get(
                f"https://api.line.me/v2/bot/profile/{user_id}",
                headers={"Authorization": f"Bearer {LINE_TOKEN}"},
                timeout=5
            )
            if r.status_code == 200:
                user_name = r.json().get("displayName", "用戶")
        except Exception:
            pass
        
        # 寫入 Sheets
        write_key_to_sheet(sheet, key, user_name, user_id, plan, expire)
        
        # 發送金鑰給用戶
        send_line_message(user_id,
            f"🎉 付款成功！感謝您購買 A.Z.N.G. {plan}\n\n"
            f"🔑 您的授權金鑰：\n{key}\n\n"
            f"📅 有效期限：{expire}\n\n"
            "請將金鑰複製到程式的「金鑰授權」欄位並點擊驗證。\n"
            "如有任何問題請直接在此詢問 🙌"
        )
    except Exception as e:
        return PlainTextResponse("0|error")
    
    return PlainTextResponse("1|OK")

# ===== 付款頁面（導向綠界）=====
@app.get("/pay")
async def pay_redirect(uid: str, plan: str, amount: str):
    # 這裡之後接綠界付款頁，先回傳確認訊息
    return {
        "message": "付款頁面建置中",
        "uid": uid,
        "plan": plan,
        "amount": amount
    }

# ===== 健康檢查 =====
@app.get("/")
def health_check():
    return {"status": "AZNG License Server Running"}
