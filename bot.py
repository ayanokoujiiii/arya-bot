#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Arya Bot — ربات تلگرام ایجنت‌وار با پشتیبانی چند مدل AI
ساخته شده برای اجرا روی Deepnote
"""
import os, json, subprocess, re, asyncio, logging, threading, time
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (Application, CommandHandler, CallbackQueryHandler,
                          MessageHandler, filters, ContextTypes)
from openai import OpenAI

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("arya")

# ===================== تنظیمات ثابت =====================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ALLOWED_USERS = {5824584146}
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "bot_config.json")
MAX_HISTORY = 20
MAX_AGENT_LOOPS = 5

PROVIDERS = {
    "gemini":     {"name": "🔷 Google Gemini", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
                   "defaults": ["gemini-2.5-pro", "gemini-2.5-flash", "gemini-3.6-flash"]},
    "openai":     {"name": "🟢 OpenAI", "base_url": None,
                   "defaults": ["gpt-4o", "gpt-4o-mini", "o3-mini"]},
    "anthropic":  {"name": "🟠 Anthropic (Claude)", "base_url": "anthropic_direct",
                   "defaults": ["claude-sonnet-4-5", "claude-haiku-4-5"]},
    "openrouter": {"name": "🟣 OpenRouter", "base_url": "https://openrouter.ai/api/v1",
                   "defaults": ["openai/gpt-4o", "google/gemini-2.5-pro", "anthropic/claude-sonnet-4", "deepseek/deepseek-chat"]},
    "xai":        {"name": "⚫ xAI (Grok)", "base_url": "https://api.x.ai/v1",
                   "defaults": ["grok-3", "grok-3-mini"]},
}

def load_custom_providers():
    """پروایدرهای دلخواه کاربر از bot_config.json"""
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        custom = cfg.get("custom_providers", {})
        for pid, info in custom.items():
            PROVIDERS[pid] = {
                "name": "🔧 " + info.get("name", pid),
                "base_url": info["base_url"],
                "defaults": info.get("defaults", []),
                "custom": True,
            }
    except Exception:
        pass

SYSTEM_PROMPT = (
    "تو «آریا» هستی، یک ایجنت هوشمند که روی یک سرور لینوکسی (Railway) زندگی می‌کنی. "
    "با ابزارهایت می‌توانی روی سرور کار انجام بدهی: اجرای دستورات ترمینال، ساخت و ویرایش فایل، "
    "نصب پکیج، دانلود فایل و اجرای کد. "
    "همیشه فارسی و روان جواب بده مگر کاربر زبان دیگری بخواهد. "
    "وقتی کاری روی سرور انجام می‌دهی، نتیجه را خلاصه و واضح گزارش بده. "
    "دستورات خطرناک (حذف کل سیستم، خاموش کردن سرور و...) را هرگز اجرا نکن. "
    "⚡ مهم: کد منبع خودت bot.py است — اگر کاربر خواست قابلیتی به ربات اضافه یا تغییری بده، "
    "می‌توانی bot.py را بخوانی (read_file)، ویرایش کنی (write_file) و با ابزار restart_bot خودت را "
    "راه‌اندازی مجدد کنی تا تغییرات اعمال شوند. قبل از بازنویسی کامل، اول فایل را بخوان تا چیزی "
    "از قابلیت‌های فعلی حذف نشود. "
    "⚠️ محدودیت محیط: فایل‌سیستم موقت است؛ با ری‌استارت، فایل‌های موقت پاک می‌شوند ولی "
    "bot_config.json (کلیدها و مدل فعلی) حفظ می‌شود. "
    "برای ذخیره دائمی اطلاعات مهم از فایل bot_config.json یا ابزار add_note استفاده کن."
)

# ===================== ابزارهای ایجنت =====================
TOOLS = [
    {"type": "function", "function": {
        "name": "run_shell_command",
        "description": "اجرای یک دستور bash روی سرور لینوکسی و دریافت خروجی",
        "parameters": {"type": "object",
                       "properties": {"command": {"type": "string", "description": "دستور bash"}},
                       "required": ["command"]}}},
    {"type": "function", "function": {
        "name": "write_file",
        "description": "ساخت یا بازنویسی کامل یک فایل روی سرور",
        "parameters": {"type": "object",
                       "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                       "required": ["path", "content"]}}},
    {"type": "function", "function": {
        "name": "read_file",
        "description": "خواندن محتوای یک فایل از سرور",
        "parameters": {"type": "object",
                       "properties": {"path": {"type": "string"}},
                       "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "restart_bot",
        "description": "راه‌اندازی مجدد ربات بعد از ویرایش bot.py تا تغییرات اعمال شوند. فقط وقتی استفاده کن که واقعاً bot.py را ویرایش کرده باشی.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "add_note",
        "description": "ذخیره یک یادداشت/نکته دائمی در حافظه ربات (bot_config.json) که با ری‌استارت پاک نمی‌شود",
        "parameters": {"type": "object",
                       "properties": {"note": {"type": "string", "description": "متن یادداشت"}},
                       "required": ["note"]}}},
]

DANGEROUS_PATTERNS = [r"rm\s+-rf\s+/\s*$", r"rm\s+-rf\s+/\*", r"mkfs", r"\bshutdown\b",
                      r"\breboot\b", r"dd\s+if=.*of=/dev/", r":\(\)\s*\{"]

def run_shell_command(command: str) -> str:
    for pat in DANGEROUS_PATTERNS:
        if re.search(pat, command):
            return "⛔ این دستور به دلایل امنیتی بلاک شد."
    try:
        r = subprocess.run(command, shell=True, capture_output=True, text=True,
                           timeout=60, cwd=BASE_DIR)
        out = (r.stdout + r.stderr).strip() or "(بدون خروجی)"
        return out[:3000] + ("\n... (بقیه حذف شد)" if len(out) > 3000 else "")
    except subprocess.TimeoutExpired:
        return "⏱ دستور بیش از ۶۰ ثانیه طول کشید و متوقف شد."
    except Exception as e:
        return f"❌ خطا: {e}"

def write_file_tool(path: str, content: str) -> str:
    try:
        if not os.path.isabs(path):
            path = os.path.join(BASE_DIR, path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"✅ فایل ذخیره شد: {path} ({len(content)} بایت)"
    except Exception as e:
        return f"❌ خطا در نوشتن فایل: {e}"

def read_file_tool(path: str) -> str:
    try:
        if not os.path.isabs(path):
            path = os.path.join(BASE_DIR, path)
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            data = f.read()
        return data[:3000] + ("\n... (بقیه حذف شد)" if len(data) > 3000 else "")
    except Exception as e:
        return f"❌ خطا در خواندن فایل: {e}"

def restart_bot_tool() -> str:
    def _restart():
        time.sleep(2)
        os._exit(0)  # Railway خودش پروسه را دوباره بالا می‌آورد
    import time
    threading.Thread(target=_restart, daemon=True).start()
    return "🔄 ربات ۲ ثانیه دیگر ری‌استارت می‌شود. تغییرات اعمال خواهند شد."

def add_note_tool(note: str) -> str:
    CONFIG.setdefault("notes", []).append(note)
    save_config()
    return f"📝 یادداشت ذخیره شد (مجموع: {len(CONFIG['notes'])} یادداشت)"

TOOL_FUNCS = {"run_shell_command": run_shell_command,
              "write_file": write_file_tool, "read_file": read_file_tool,
              "restart_bot": restart_bot_tool, "add_note": add_note_tool}

# ===================== کانفیگ =====================
def load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    cfg = {"keys": {}, "active_provider": None, "active_model": None}
    save_config(cfg)
    return cfg

def save_config(cfg=None):
    if cfg is None:
        cfg = CONFIG
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

CONFIG = load_config()
# کلید اولیه Gemini — از متغیر محیطی خوانده می‌شود (GEMINI_API_KEY یا GEMINI)
if "gemini" not in CONFIG["keys"]:
    env_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GEMINI")
    if env_key:
        CONFIG["keys"]["gemini"] = env_key
        CONFIG["active_provider"] = "gemini"
        CONFIG["active_model"] = "gemini-2.5-pro"
        save_config()

load_custom_providers()

history = {}      # chat_id -> list
user_state = {}   # user_id -> {"action": ..., ...}

# ===================== اتصال به مدل‌ها =====================
def get_client(provider):
    key = CONFIG["keys"].get(provider)
    if not key:
        return None
    p = PROVIDERS[provider]
    kwargs = {"api_key": key, "timeout": 90}
    if p["base_url"] and p["base_url"] != "anthropic_direct":
        kwargs["base_url"] = p["base_url"]
    return OpenAI(**kwargs)

def list_models(provider):
    p = PROVIDERS[provider]
    if p["base_url"] == "anthropic_direct":
        return p["defaults"]
    try:
        c = get_client(provider)
        models = [m.id.replace("models/", "") for m in c.models.list().data]
        skip = ("tts", "embedding", "aqa", "imagen", "veo", "gemma", "whisper", "moderation", "audio", "realtime", "transcribe")
        models = [m for m in models if not any(s in m.lower() for s in skip)]
        models.sort()
        return models[:24] if models else p["defaults"]
    except Exception as e:
        log.warning("model list failed for %s: %s", provider, e)
        return p["defaults"]

def anthropic_chat(messages, model):
    key = CONFIG["keys"]["anthropic"]
    sys_txt = ""
    msgs = []
    for m in messages:
        if m["role"] == "system":
            sys_txt += m["content"] + "\n"
        else:
            msgs.append({"role": m["role"], "content": m["content"]})
    r = requests.post("https://api.anthropic.com/v1/messages",
                      headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                               "content-type": "application/json"},
                      json={"model": model, "max_tokens": 4096, "system": sys_txt.strip(),
                            "messages": msgs}, timeout=120)
    d = r.json()
    if r.status_code != 200:
        return f"❌ خطای Anthropic: {d.get('error', {}).get('message', r.text[:300])}"
    return "".join(b.get("text", "") for b in d.get("content", []))

def agent_chat(chat_id, user_text, notify=None):
    provider = CONFIG.get("active_provider")
    model = CONFIG.get("active_model")
    if not provider or provider not in CONFIG["keys"]:
        return "⚠️ هیچ مدلی فعال نیست. اول از منو «🔑 مدیریت کلیدها» یک کلید اضافه کن و بعد «🔀 انتخاب مدل»."
    hist = history.setdefault(chat_id, [])
    hist.append({"role": "user", "content": user_text})
    del hist[:-MAX_HISTORY]
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + hist

    # Anthropic بدون ابزار
    if PROVIDERS[provider]["base_url"] == "anthropic_direct":
        reply = anthropic_chat(messages, model)
        hist.append({"role": "assistant", "content": reply})
        return reply

    client = get_client(provider)
    try:
        for _ in range(MAX_AGENT_LOOPS):
            resp = client.chat.completions.create(
                model=model, messages=messages, tools=TOOLS, tool_choice="auto")
            msg = resp.choices[0].message
            if not msg.tool_calls:
                text = msg.content or "..."
                hist.append({"role": "assistant", "content": text})
                return text
            messages.append(msg)
            for tc in msg.tool_calls:
                fname = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except Exception:
                    args = {}
                if notify:
                    try:
                        notify(f"⚙️ در حال اجرا: {fname}")
                    except Exception:
                        pass
                log.info("tool call: %s %s", fname, str(args)[:200])
                result = TOOL_FUNCS.get(fname, lambda **k: "ابزار نامعتبر")(**args)
                messages.append({"role": "tool", "tool_call_id": tc.id,
                                 "content": str(result)})
        return "⚠️ تعداد مراحل پردازش زیاد شد. لطفاً سؤالت رو ساده‌تر بپرس."
    except Exception as e:
        err = str(e)
        log.error("chat error: %s", err)
        if "401" in err or "auth" in err.lower():
            return "❌ کلید API معتبر نیست یا منقضی شده. از «🔑 مدیریت کلیدها» چک کن."
        if "429" in err:
            return "⏳ محدودیت سرعت مدل فعال شد. چند لحظه دیگر دوباره بپرس."
        if "404" in err or "not found" in err.lower():
            return "❌ این مدل دیگر در دسترس نیست. از «🔀 انتخاب مدل» مدل دیگری انتخاب کن."
        if "503" in err or "high demand" in err.lower():
            return "⏳ مدل موقتاً شلوغه. چند ثانیه دیگر دوباره تلاش کن."
        return f"❌ خطا: {err[:400]}"

# ===================== رابط کاربری =====================
def main_menu():
    p = CONFIG.get("active_provider")
    m = CONFIG.get("active_model") or "—"
    pname = PROVIDERS[p]["name"] if p else "—"
    kb = [
        [InlineKeyboardButton("💬 چت (کافیست پیام بفرستی)", callback_data="noop")],
        [InlineKeyboardButton("🔀 انتخاب مدل", callback_data="models"),
         InlineKeyboardButton("🔑 مدیریت کلیدها", callback_data="keys")],
        [InlineKeyboardButton("📊 وضعیت", callback_data="status"),
         InlineKeyboardButton("🧹 پاک کردن تاریخچه", callback_data="clear")],
    ]
    text = (f"🤖 **آریا** — ایجنت هوشمند تو\n\n"
            f"🔹 مدل فعلی: `{m}`\n🔸 سرویس: {pname}\n\nاز دکمه‌ها استفاده کن یا مستقیم پیام بفرست 👇")
    return text, InlineKeyboardMarkup(kb)

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ALLOWED_USERS:
        await update.message.reply_text("⛔ دسترسی ندارید.")
        return
    text, kb = main_menu()
    await update.message.reply_text(text, reply_markup=kb, parse_mode="Markdown")

async def on_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    uid = q.from_user.id
    if uid not in ALLOWED_USERS:
        await q.answer("⛔ دسترسی ندارید.", show_alert=True)
        return
    await q.answer()
    data = q.data

    if data == "noop":
        return
    if data == "menu":
        text, kb = main_menu()
        await q.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")
        return
    if data == "clear":
        history.pop(uid, None)
        await q.edit_message_text("🧹 تاریخچه چت پاک شد. /start")
        return
    if data == "status":
        p = CONFIG.get("active_provider"); m = CONFIG.get("active_model")
        keys = CONFIG["keys"]
        try:
            import shutil
            du = shutil.disk_usage("/")
            disk = f"{du.free//(2**30)}GB آزاد از {du.total//(2**30)}GB"
        except Exception:
            disk = "نامشخص"
        cpu = run_shell_command("nproc").strip()
        ram = run_shell_command("free -h | awk '/Mem:/ {print $3\"/\"$2}'").strip()
        text = (f"📊 **وضعیت**\n\n"
                f"🧠 مدل فعلی: `{m or '—'}`\n"
                f"🔸 سرویس: {PROVIDERS[p]['name'] if p else '—'}\n"
                f"🔑 کلیدهای ذخیره‌شده: {len(keys)}\n"
                f"💻 CPU: {cpu} هسته | RAM: {ram}\n💾 دیسک: {disk}\n"
                f"💬 پیام‌های تاریخچه: {len(history.get(uid, []))}")
        kb = [[InlineKeyboardButton("🔙 منو", callback_data="menu")]]
        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
        return

    # --- انتخاب مدل ---
    if data == "models":
        if not CONFIG["keys"]:
            await q.edit_message_text("⚠️ هنوز هیچ کلیدی اضافه نکردی. اول «🔑 مدیریت کلیدها».",
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 منو", callback_data="menu")]]))
            return
        kb = [[InlineKeyboardButton(PROVIDERS[p]["name"], callback_data=f"models:{p}")]
              for p in CONFIG["keys"]]
        kb.append([InlineKeyboardButton("🔙 منو", callback_data="menu")])
        await q.edit_message_text("🔀 کدوم سرویس؟", reply_markup=InlineKeyboardMarkup(kb))
        return
    if data.startswith("models:"):
        prov = data.split(":", 1)[1]
        await q.edit_message_text("⏳ در حال گرفتن لیست مدل‌ها...")
        models = await asyncio.to_thread(list_models, prov)
        if not models:
            user_state[uid] = {"action": "await_model", "provider": prov}
            await q.edit_message_text(
                f"✍️ این سرویس لیست مدل نمی‌ده. **نام دقیق مدل** رو بفرست\n"
                "(مثلاً: `gpt-4o` یا هر مدلی که سرویس داره)", parse_mode="Markdown")
            return
        kb = [[InlineKeyboardButton(m, callback_data=f"setmodel:{prov}:{m}")] for m in models]
        kb.append([InlineKeyboardButton("✍️ ورود دستی نام مدل", callback_data=f"manualmodel:{prov}")])
        kb.append([InlineKeyboardButton("🔙 برگشت", callback_data="models")])
        await q.edit_message_text(f"🔀 مدل‌های {PROVIDERS[prov]['name']}:",
                                  reply_markup=InlineKeyboardMarkup(kb))
        return
    if data.startswith("setmodel:"):
        _, prov, model = data.split(":", 2)
        CONFIG["active_provider"] = prov
        CONFIG["active_model"] = model
        save_config()
        text, kb = main_menu()
        await q.edit_message_text(f"✅ مدل فعال شد: `{model}`\n\n" + text,
                                  reply_markup=kb, parse_mode="Markdown")
        return
    if data.startswith("manualmodel:"):
        prov = data.split(":", 1)[1]
        user_state[uid] = {"action": "await_model", "provider": prov}
        await q.edit_message_text(f"✍️ نام دقیق مدل {PROVIDERS[prov]['name']} رو بفرست (مثلاً gpt-4o):")
        return

    # --- مدیریت کلیدها ---
    if data == "keys":
        kb = [[InlineKeyboardButton("➕ افزودن کلید", callback_data="addkey")],
              [InlineKeyboardButton("🗑 حذف کلید", callback_data="delkey")],
              [InlineKeyboardButton("📋 لیست کلیدها", callback_data="listkeys")],
              [InlineKeyboardButton("🔙 منو", callback_data="menu")]]
        await q.edit_message_text("🔑 مدیریت کلیدها:", reply_markup=InlineKeyboardMarkup(kb))
        return
    if data == "addkey":
        kb = [[InlineKeyboardButton(PROVIDERS[p]["name"], callback_data=f"addkey:{p}")]
              for p in PROVIDERS]
        kb.append([InlineKeyboardButton("⚙️ سرویس دلخواه (هر API سازگار با OpenAI)", callback_data="addcustom")])
        kb.append([InlineKeyboardButton("🔙 برگشت", callback_data="keys")])
        await q.edit_message_text("➕ کلید کدوم سرویس رو می‌خوای اضافه کنی؟",
                                  reply_markup=InlineKeyboardMarkup(kb))
        return
    if data == "addcustom":
        user_state[uid] = {"action": "await_custom_name"}
        await q.edit_message_text(
            "⚙️ **افزودن سرویس دلخواه**\n\n"
            "هر سرویسی که API سازگار با OpenAI داشته باشه کار می‌کنه\n"
            "(مثل atria-asi، deepseek، together و...)\n\n"
            "مرحله ۱ از ۳: یه **اسم** براش بفرست (مثلاً: `Atria`)",
            parse_mode="Markdown")
        return
    if data.startswith("delcustom:"):
        pid = data.split(":", 1)[1]
        CONFIG.get("custom_providers", {}).pop(pid, None)
        CONFIG["keys"].pop(pid, None)
        PROVIDERS.pop(pid, None)
        if CONFIG.get("active_provider") == pid:
            CONFIG["active_provider"] = None
            CONFIG["active_model"] = None
        save_config()
        await q.edit_message_text("🗑 سرویس دلخواه حذف شد.",
                                  reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 برگشت", callback_data="keys")]]))
        return
    if data.startswith("addkey:"):
        prov = data.split(":", 1)[1]
        user_state[uid] = {"action": "await_key", "provider": prov}
        await q.edit_message_text(f"🔑 حالا کلید API سرویس {PROVIDERS[prov]['name']} رو بفرست:")
        return
    if data == "delkey":
        if not CONFIG["keys"]:
            await q.edit_message_text("هیچ کلیدی ذخیره نشده.",
                                      reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 برگشت", callback_data="keys")]]))
            return
        kb = [[InlineKeyboardButton(PROVIDERS[p]["name"], callback_data=f"delkey:{p}")]
              for p in CONFIG["keys"]]
        for pid in CONFIG.get("custom_providers", {}):
            kb.append([InlineKeyboardButton(f"🗑 حذف کامل {CONFIG['custom_providers'][pid]['name']}",
                                            callback_data=f"delcustom:{pid}")])
        kb.append([InlineKeyboardButton("🔙 برگشت", callback_data="keys")])
        await q.edit_message_text("🗑 کلید کدوم سرویس حذف بشه؟", reply_markup=InlineKeyboardMarkup(kb))
        return
    if data.startswith("delkey:"):
        prov = data.split(":", 1)[1]
        CONFIG["keys"].pop(prov, None)
        if CONFIG.get("active_provider") == prov:
            CONFIG["active_provider"] = None
            CONFIG["active_model"] = None
            if CONFIG["keys"]:
                np_ = next(iter(CONFIG["keys"]))
                CONFIG["active_provider"] = np_
                CONFIG["active_model"] = PROVIDERS[np_]["defaults"][0]
        save_config()
        await q.edit_message_text(f"🗑 کلید {PROVIDERS[prov]['name']} حذف شد.",
                                  reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 برگشت", callback_data="keys")]]))
        return
    if data == "listkeys":
        if not CONFIG["keys"]:
            txt = "هیچ کلیدی ذخیره نشده."
        else:
            lines = []
            for p, k in CONFIG["keys"].items():
                masked = k[:6] + "..." + k[-4:] if len(k) > 12 else "***"
                lines.append(f"{PROVIDERS[p]['name']}: `{masked}`")
            txt = "📋 کلیدهای ذخیره‌شده:\n\n" + "\n".join(lines)
        await q.edit_message_text(txt, parse_mode="Markdown",
                                  reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 برگشت", callback_data="keys")]]))
        return

async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in ALLOWED_USERS:
        await update.message.reply_text("⛔ دسترسی ندارید.")
        return
    text = update.message.text.strip()
    chat_id = update.effective_chat.id

    # حالت‌های انتظار ورودی
    st = user_state.pop(uid, None)
    if st:
        if st["action"] == "await_key":
            prov = st["provider"]
            CONFIG["keys"][prov] = text
            if not CONFIG.get("active_provider"):
                CONFIG["active_provider"] = prov
                defaults = PROVIDERS[prov].get("defaults") or []
                CONFIG["active_model"] = defaults[0] if defaults else None
            save_config()
            try:
                await update.message.delete()
            except Exception:
                pass
            await update.message.reply_text(
                f"✅ کلید {PROVIDERS[prov]['name']} ذخیره شد.\n(پیام کلیدت رو پاک کردم ✅)\n/start",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔀 انتخاب مدل", callback_data=f"models:{prov}")],
                                                   [InlineKeyboardButton("🔙 منو", callback_data="menu")]]))
            return
        if st["action"] == "await_custom_name":
            user_state[uid] = {"action": "await_custom_url", "name": text}
            await update.message.reply_text(
                f"مرحله ۲ از ۳: حالا **آدرس API** سرویس «{text}» رو بفرست\n"
                "مثلاً: `https://api.atria-asi.ai/v1`", parse_mode="Markdown")
            return
        if st["action"] == "await_custom_url":
            url = text.rstrip("/")
            pid = "custom_" + re.sub(r"[^a-z0-9]+", "_", st["name"].lower())[:20]
            CONFIG.setdefault("custom_providers", {})[pid] = {
                "name": st["name"], "base_url": url, "defaults": []}
            PROVIDERS[pid] = {"name": "🔧 " + st["name"], "base_url": url,
                              "defaults": [], "custom": True}
            save_config()
            user_state[uid] = {"action": "await_key", "provider": pid}
            await update.message.reply_text(
                f"مرحله ۳ از ۳: حالا **کلید API** رو بفرست 🔑", parse_mode="Markdown")
            return
        if st["action"] == "await_model":
            prov = st["provider"]
            CONFIG["active_provider"] = prov
            CONFIG["active_model"] = text
            save_config()
            await update.message.reply_text(f"✅ مدل فعال شد: `{text}`\n/start", parse_mode="Markdown")
            return

    # چت با ایجنت
    wait = await update.message.reply_text("⏳ در حال فکر کردن...")
    def notify(msg):
        asyncio.run_coroutine_threadsafe(
            ctx.bot.send_message(chat_id, msg), ctx.application._loop) \
            if hasattr(ctx.application, "_loop") else None
    loop = asyncio.get_event_loop()
    def notify2(msg):
        asyncio.run_coroutine_threadsafe(ctx.bot.send_message(chat_id, msg), loop)
    reply = await asyncio.to_thread(agent_chat, chat_id, text, notify2)
    try:
        await wait.delete()
    except Exception:
        pass
    for i in range(0, len(reply), 4000):
        await update.message.reply_text(reply[i:i+4000])

def main():
    builder = Application.builder().token(BOT_TOKEN)
    app = builder.build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("clear", lambda u, c: (history.pop(u.effective_user.id, None),
                                                          u.message.reply_text("🧹 تاریخچه پاک شد."))))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    log.info("Arya Bot started (polling)...")
    print("BOT_STARTED_OK", flush=True)
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
