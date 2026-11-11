import os, sys, re, json, time, uuid, random, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import asyncio
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters

# ─── TELEGRAM BOT CONFIG ──────────────────────────────────────────────────────
BOT_TOKEN = "9265161951:AAEqQv7-1uTs8K67ReDYUGj9dZ3qADE2bZU" # Tera Token

# ─── DUMMY WEB SERVER (Render Free Tier ke liye) ──────────────────────────────
class WebServerHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        self.wfile.write(b"Bot is alive!")

def run_web_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), WebServerHandler)
    print(f"🌐 Web server running on port {port}")
    server.serve_forever()

user_sessions = {}

# ─── API CLIENT ───────────────────────────────────────────────────────────────
class Taxi:
    BASE = "https://api.c2.moving.tech/pilot/app/v2"
    def __init__(self):
        self.s = requests.Session()
        self.s.mount("https://", HTTPAdapter(max_retries=Retry(total=3, backoff_factor=1)))
        self.session_id = str(uuid.uuid4())
        self.device_token = uuid.uuid4().hex
        self.device = "vivo/V2205/Android v13/V2205/Handset"
        self.token = None

    def _hdr(self):
        h={"x-rn-version":"--","x-config-version":"0.0.1","x-client-version":"0.0.28",
           "x-bundle-version":"0.0.0","x-device":self.device, "x-package":"in.mobility.bharatTaxi",
           "content-type":"application/json", "user-agent":"okhttp/4.12.0",
           "accept-encoding":"gzip", "session_id":self.session_id}
        if self.token: h["token"]=self.token
        return h

    def send_otp(self, phone):
        h=self._hdr(); h["x-sender-hash"]="AEdL582H847"
        p={"merchantId":"BHARAT_TAXI","mobileNumber":phone,"mobileCountryCode":"+91",
           "allowBlockedUserLogin":True,"senderHash":"AEdL582H847"}
        try:
            r=self.s.post(f"{self.BASE}/auth",headers=h,json=p,timeout=15)
            if r.status_code==200: return True, r.json().get("authId")
            return False, f"HTTP {r.status_code}"
        except Exception as e: return False, str(e)

    def verify(self, aid, otp):
        try:
            r=self.s.post(f"{self.BASE}/auth/{aid}/verify",headers=self._hdr(),
                          json={"otp":otp,"deviceToken":self.device_token},timeout=15)
            if r.status_code==200:
                t=r.json().get("token")
                if t: self.token=t; return True
            return False
        except: return False

    def get_rewards(self):
        try:
            r=self.s.get(f"{self.BASE}/rewards",headers=self._hdr(),timeout=15)
            if r.status_code==200: return r.json()
        except: pass
        return []

def _clean_num(raw):
    if not raw: return ""
    x = re.sub(r"[^0-9]", "", str(raw))
    if len(x) == 12 and x.startswith("91"): x = x[2:]
    if len(x) == 11 and x.startswith("0"): x = x[1:]
    return x if (len(x) == 10 and x[0] in "6789") else ""

# ─── BOT COMMANDS ─────────────────────────────────────────────────────────────
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🚕 <b>BHARAT TAXI BULK BOT</b> 🚕\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "👉 <code>/send 9999999999 9876543210</code>\n"
        "👉 <code>/verify 1234 5678 9012</code>\n"
    )
    await update.message.reply_text(msg, parse_mode="HTML")

async def send_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.message.from_user.id
    args = context.args
    if not args:
        return await update.message.reply_text("❌ Format: <code>/send 9999999999</code>", parse_mode="HTML")
    
    valid_nums = [_clean_num(x) for x in args]
    valid_nums = [n for n in valid_nums if n]
    if not valid_nums:
        return await update.message.reply_text("❌ No valid numbers.")

    wait_msg = await update.message.reply_text(f"⏳ Sending OTPs...", parse_mode="HTML")
    session_nums = {}
    success_count = 0
    
    for phone in valid_nums:
        taxi = Taxi()
        ok, aid = await asyncio.to_thread(taxi.send_otp, phone)
        if ok:
            session_nums[phone] = {"client": taxi, "aid": aid}
            success_count += 1
        await asyncio.sleep(2)

    if success_count == 0:
        return await wait_msg.edit_text("❌ All failed (IP Block).")

    user_sessions[uid] = {"numbers": session_nums, "order": [n for n in valid_nums if n in session_nums]}
    txt = f"✅ <b>OTP Sent to {success_count} numbers!</b>\n\n"
    for i, n in enumerate(user_sessions[uid]["order"], 1):
        txt += f"{i}. <code>{n}</code>\n"
    txt += f"\n👇 <code>/verify 1234 5678</code>"
    await wait_msg.edit_text(txt, parse_mode="HTML")

async def verify_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.message.from_user.id
    args = context.args
    if uid not in user_sessions:
        return await update.message.reply_text("⚠️ Send numbers first via /send", parse_mode="HTML")

    otps = [p.strip() for p in args if p.isdigit() and len(p)==4]
    session_data = user_sessions[uid]
    numbers_order = session_data["order"]
    
    if len(otps) != len(numbers_order):
        return await update.message.reply_text(f"⚠️ Count mismatch!", parse_mode="HTML")

    wait_msg = await update.message.reply_text("⏳ Verifying...", parse_mode="HTML")
    await asyncio.sleep(1)
    await wait_msg.delete()

    for i, phone in enumerate(numbers_order):
        otp = otps[i]
        data = session_data["numbers"][phone]
        taxi = data["client"]
        aid = data["aid"]
        status_msg = await update.message.reply_text(f"🔄 <code>{phone}</code> verifying...", parse_mode="HTML")
        
        ok_verify = await asyncio.to_thread(taxi.verify, aid, otp)
        if ok_verify:
            rewards = await asyncio.to_thread(taxi.get_rewards)
            coupons = [r for r in rewards if isinstance(r, dict) and r.get("couponCode")] if isinstance(rewards, list) else []
            if coupons:
                txt = f"🎉 <b>JACKPOT!</b>\n📱 {phone}\n"
                for c in coupons:
                    txt += f"🍦 {c.get('couponCode')}\n"
            else:
                txt = f"✅ <b>Login OK</b>\n📱 {phone}\n⚠️ No Coupons"
            await status_msg.edit_text(txt, parse_mode="HTML")
        else:
            await status_msg.edit_text(f"❌ <b>Wrong OTP</b>\n📱 {phone}", parse_mode="HTML")
        await asyncio.sleep(1.5)
        
    del user_sessions[uid]
    await update.message.reply_text("✅ <b>Process Complete!</b>", parse_mode="HTML")

if __name__ == '__main__':
    threading.Thread(target=run_web_server, daemon=True).start()
    print("🚀 Bot Started!")
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("send", send_cmd))
    app.add_handler(CommandHandler("verify", verify_cmd))
    app.run_polling()
