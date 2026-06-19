"""
A.Z.N.G. 授權金鑰驗證伺服器 v2
- 金鑰驗證
- LINE Webhook（記錄 User ID）
- 綠界付款通知（自動產生金鑰並發送）
"""

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse, HTMLResponse
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
import urllib.parse

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
ECPAY_MERCHANT_ID = os.environ.get("ECPAY_MERCHANT_ID", "")
ECPAY_HASH_KEY = os.environ.get("ECPAY_HASH_KEY", "")
ECPAY_HASH_IV = os.environ.get("ECPAY_HASH_IV", "")
ECPAY_API_URL = "https://payment.ecpay.com.tw/Cashier/AioCheckOut/V5"

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

# ===== 產生綠界檢查碼 =====
def generate_check_mac_value(params: dict) -> str:
    sorted_params = sorted(params.items())
    raw = "&".join(f"{k}={v}" for k, v in sorted_params)
    raw = f"HashKey={ECPAY_HASH_KEY}&{raw}&HashIV={ECPAY_HASH_IV}"
    raw = urllib.parse.quote_plus(raw).lower()
    return hashlib.sha256(raw.encode()).hexdigest().upper()

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
            user_id = event["source"]["userId"]
            send_line_message(user_id,
                "🎉 歡迎加入 A.Z.N.G. Threads 發文輔助系統！\n\n"
                "你可以輸入以下關鍵字獲得對應資訊：\n\n"
                "💰 價格 → 查看方案定價\n"
                "🛠️ 想了解 → 了解產品功能介紹\n"
                "🎁 申請試用 → 申請試用資格\n"
                "💳 購買 → 開始購買流程\n"
                "👨\u200d💼 客服 → 聯絡客服\n\n"
                "有任何問題歡迎直接留言 🙌"
            )
        
        elif event.get("type") == "message":
            user_id = event["source"]["userId"]
            text = event["message"].get("text", "").strip()
            
            if text == "購買":
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
                pay_url = f"https://azng-license-server.onrender.com/pay?uid={user_id}&plan={text}&amount={price}"
                send_line_message(user_id,
                    f"✅ 您選擇的方案：{text}（NT${price}）\n\n"
                    f"請點擊以下連結完成付款：\n{pay_url}\n\n"
                    "付款完成後系統將自動發送金鑰給您 🎁"
                )
            
            elif text in ["客服", "客服諮詢"]:
                send_line_message(user_id,
                    "你好！感謝你聯繫 A.Z.N.G. 客服 🙌\n\n"
                    "我們已收到你的訊息，將會在最短時間內回覆你（通常在數小時內）。\n\n"
                    "如有緊急問題，也歡迎直接在此說明你的狀況，我們會優先處理 ✅"
                )
            
            elif text in ["申請試用", "試用"]:
                send_line_message(user_id,
                    "感謝你的興趣！目前首批試用名額開放中 🎉\n\n"
                    "【試用方案說明】\n"
                    "✦ 試用期間：14 天完整版\n"
                    "✦ 名額限制：首批 5～10 人\n"
                    "✦ 系統需求：Windows 電腦\n"
                    "✦ 試用結束後提供使用回饋，即可享 $299/月 訂閱優惠\n\n"
                    "【申請方式】\n"
                    "請直接在此留言告訴我們：\n"
                    "1️⃣ 你的 Threads 帳號\n"
                    "2️⃣ 你主要想解決什麼問題\n"
                    "3️⃣ 你目前使用 Windows 嗎？\n\n"
                    "我們確認後會盡快與你聯繫安排試用 ✅"
                )
            
            elif text in ["價格", "方案", "定價"]:
                send_line_message(user_id,
                    "以下是 A.Z.N.G. Threads 發文輔助系統的定價方案 💰\n\n"
                    "🔹 試用轉訂閱優惠（提供回饋者）— $299/月\n"
                    "🔹 新用戶首月訂閱 — $399/月\n"
                    "🔹 正式月費 — $499/月\n"
                    "🔹 加購帳號金鑰 — $199\n\n"
                    "✦ 推薦新用戶成功訂閱，當月享 $299\n"
                    "✦ 所有方案均包含功能持續迭代更新\n\n"
                    "如有任何疑問歡迎直接留言詢問 😊"
                )
            
            elif text in ["想了解", "介紹", "功能"]:
                send_line_message(user_id,
                    "感謝你的詢問！以下是 A.Z.N.G. Threads 發文輔助系統的簡介 👇\n\n"
                    "【這套系統能幫你做什麼？】\n\n"
                    "📅 定時發文\n自動從 Google Sheets 讀取內容，在設定時段排程發佈，支援圖片與影片\n\n"
                    "🔍 通知巡邏\n定時掃描 Threads 通知，偵測新留言並整理待處理清單\n\n"
                    "💬 關鍵字回覆\n偵測留言關鍵字，自動送出預設回覆\n\n"
                    "👍 互動輔助\n對新留言自動按讚，維持帳號活躍度\n\n"
                    "📩 私訊自動回覆\n掃描收件匣與陌生訊息，偵測關鍵字後自動回覆，讓你不錯過任何潛在客戶\n\n"
                    "【適合對象】\n"
                    "商家品牌、蝦皮分潤、個人品牌、KOL、接案者、課程銷售者\n\n"
                    "📄 完整介紹：https://azng888.github.io\n\n"
                    "有任何問題歡迎直接留言，我們會盡快回覆你 😊"
                )
            
            else:
                # 抓用戶 LINE 名稱
                display_name = "您"
                try:
                    r = req_lib.get(
                        f"https://api.line.me/v2/bot/profile/{user_id}",
                        headers={"Authorization": f"Bearer {LINE_TOKEN}"},
                        timeout=5
                    )
                    if r.status_code == 200:
                        display_name = r.json().get("displayName", "您")
                except Exception:
                    pass
                
                send_line_message(user_id,
                    f"{display_name} 您好！感謝您的訊息 😊\n\n"
                    "您可以輸入以下關鍵字獲得對應資訊：\n\n"
                    "💰 價格 → 查看方案定價\n"
                    "🛠️ 想了解 → 了解產品功能介紹\n"
                    "🎁 申請試用 → 申請試用資格\n"
                    "💳 購買 → 開始購買流程\n"
                    "👨‍💼 客服 → 聯絡客服"
                )
    
    return {"status": "ok"}

# ===== 付款頁面（導向綠界）=====
@app.get("/pay")
async def pay_redirect(uid: str, plan: str, amount: str):
    now = datetime.now().strftime("%Y%m%d%H%M%S")
    order_id = f"ANG{datetime.now().strftime('%Y%m%d%H%M%S')}{random.randint(100,999)}"
    
    params = {
        "MerchantID": ECPAY_MERCHANT_ID,
        "MerchantTradeNo": order_id,
        "MerchantTradeDate": now,
        "PaymentType": "aio",
        "TotalAmount": amount,
        "TradeDesc": f"AZNG {plan}",
        "ItemName": f"A.Z.N.G. {plan}",
        "ReturnURL": "https://azng-license-server.onrender.com/ecpay/notify",
        "ClientBackURL": "https://azng888.github.io",
        "ChoosePayment": "ALL",
        "EncryptType": "1",
        "CustomField1": f"{uid}|{plan}",
    }
    params["CheckMacValue"] = generate_check_mac_value(params)

    # 產生自動提交的 HTML 表單跳轉到綠界
    form_inputs = "\n".join(
        f'<input type="hidden" name="{k}" value="{v}">'
        for k, v in params.items()
    )
    html = f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="utf-8"><title>跳轉付款中...</title></head>
    <body>
        <p>正在跳轉到付款頁面，請稍候...</p>
        <form id="pay_form" method="POST" action="{ECPAY_API_URL}">
            {form_inputs}
        </form>
        <script>document.getElementById('pay_form').submit();</script>
    </body>
    </html>
    """
    return HTMLResponse(content=html)

# ===== 綠界付款通知 =====
@app.post("/ecpay/notify")
async def ecpay_notify(request: Request):
    form = await request.form()
    data = dict(form)
    
    if data.get("RtnCode") != "1":
        return PlainTextResponse("0|error")
    
    custom_field = data.get("CustomField1", "")
    try:
        parts = custom_field.split("|")
        user_id = parts[0]
        plan = parts[1]
    except Exception:
        return PlainTextResponse("0|error")
    
    try:
        key = generate_key()
        sheet = get_license_sheet()
        expire = (datetime.now() + timedelta(days=30)).strftime("%Y/%m/%d")
        
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
        
        write_key_to_sheet(sheet, key, user_name, user_id, plan, expire)
        
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

# ===== 健康檢查 =====
@app.get("/")
def health_check():
    return {"status": "AZNG License Server Running"}
