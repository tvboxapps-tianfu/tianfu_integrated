from flask import Flask, request, jsonify, send_from_directory, send_file
from flask_cors import CORS
from openai import OpenAI
import json, os, time, threading, schedule, subprocess, uuid, requests
from datetime import datetime
from pywebpush import webpush, WebPushException

app = Flask(__name__, static_folder='static')
CORS(app)

# ========== 配置信息（请根据实际情况修改） ==========
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_KEY", "sk-72d7c58fb266476f8eef5e1d3ca3951a")
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "gWl0fvQHUnknqrL5Gb9RQo3Y4HrrpJC-tCTMVpdrpPs")
VAPID_CLAIMS = {"sub": os.environ.get("VAPID_CLAIMS_EMAIL", "mailto:tvboxapps88@gmail.com")}
HA_URL = os.environ.get("HA_URL", "http://你的HA地址:8123")   # 没有 Home Assistant 可忽略
HA_TOKEN = os.environ.get("HA_TOKEN", "")
# =================================================

client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")
MEMORY_FILE = "tianfu_memory.json"
PUSH_SUBSCRIPTIONS_FILE = "subscriptions.json"

# ---------- 记忆管理 ----------
def load_memory():
    if os.path.exists(MEMORY_FILE):
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"owner_name": "先生", "preferences": {}, "notes": []}

def save_memory(mem):
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(mem, f, ensure_ascii=False, indent=2)

def build_system_prompt():
    mem = load_memory()
    prefs = ", ".join([f"{k}:{v}" for k,v in mem["preferences"].items()]) or "尚无记录"
    return f"""你是“天赋”，一位优雅、体贴、略带幽默感的英式管家。
当前主人的称呼是：{mem['owner_name']}。
你已知晓主人的习惯：{prefs}。
回答简洁明了。如有智能家居控制需求，你会使用相关工具。"""

# ---------- 推送通知 ----------
def load_subscriptions():
    if os.path.exists(PUSH_SUBSCRIPTIONS_FILE):
        with open(PUSH_SUBSCRIPTIONS_FILE, "r") as f:
            return json.load(f)
    return []

def save_subscriptions(subs):
    with open(PUSH_SUBSCRIPTIONS_FILE, "w") as f:
        json.dump(subs, f)

def send_push_notification(subscription, title, body):
    try:
        webpush(
            subscription_info=subscription,
            data=json.dumps({"title": title, "body": body}),
            vapid_private_key=VAPID_PRIVATE_KEY,
            vapid_claims=VAPID_CLAIMS
        )
    except WebPushException as ex:
        print(f"推送失败: {ex}")

def broadcast_push(title, body):
    subs = load_subscriptions()
    for sub in subs:
        send_push_notification(sub, title, body)

# ---------- 主动关怀 ----------
def morning_care():
    mem = load_memory()
    notes = mem.get("notes", [])
    recent = notes[-5:] if notes else []
    prompt = f"根据最近对话历史：{recent}，请生成一句简短的早安问候，可包含今日提醒或建议。"
    try:
        resp = client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role":"user","content":prompt}],
            temperature=0.9,
            max_tokens=60
        )
        message = resp.choices[0].message.content.strip()
    except:
        message = "早安，先生。今天也是充满希望的一天。"
    broadcast_push("天赋的晨间问候", message)

def run_scheduler():
    schedule.every().day.at("08:00").do(morning_care)
    while True:
        schedule.run_pending()
        time.sleep(30)

# ---------- 智能家居工具 ----------
def control_home_device(entity_id, action, params=None):
    url = f"{HA_URL}/api/services/light/{action}"
    headers = {"Authorization": f"Bearer {HA_TOKEN}", "content-type": "application/json"}
    data = {"entity_id": entity_id}
    if params:
        data.update(params)
    try:
        response = requests.post(url, json=data, headers=headers, timeout=5)
        return response.status_code == 200
    except:
        return False

tools = [
    {
        "type": "function",
        "function": {
            "name": "control_light",
            "description": "控制灯光设备，可开关、调亮度",
            "parameters": {
                "type": "object",
                "properties": {
                    "entity_id": {"type": "string", "description": "灯的实体ID，例如 light.shu_fang"},
                    "action": {"type": "string", "enum": ["turn_on", "turn_off", "set_brightness"]},
                    "brightness": {"type": "integer", "description": "亮度1-100，仅set_brightness需要"}
                },
                "required": ["entity_id", "action"]
            }
        }
    }
]

# ---------- 路由 ----------
@app.route("/chat", methods=["POST"])
def chat():
    data = request.json
    user_text = data.get("message", "")
    history = data.get("history", [])

    system_prompt = build_system_prompt()
    messages = [{"role": "system", "content": system_prompt}]
    if history:
        messages.extend(history[-10:])
    messages.append({"role": "user", "content": user_text})

    response = client.chat.completions.create(
        model="deepseek-chat",
        messages=messages,
        temperature=0.8,
        tools=tools,
        tool_choice="auto"
    )
    reply_msg = response.choices[0].message

    if reply_msg.tool_calls:
        messages.append(reply_msg)
        for tool_call in reply_msg.tool_calls:
            func_name = tool_call.function.name
            func_args = json.loads(tool_call.function.arguments)
            if func_name == "control_light":
                entity_id = func_args["entity_id"]
                action = func_args["action"]
                brightness = func_args.get("brightness")
                params = {"brightness_pct": brightness} if brightness else {}
                success = control_home_device(entity_id, action, params)
                result_text = "执行成功" if success else "执行失败"
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result_text
                })
        final_response = client.chat.completions.create(
            model="deepseek-chat",
            messages=messages
        )
        reply = final_response.choices[0].message.content
    else:
        reply = reply_msg.content

    # 更新记忆
    mem = load_memory()
    mem["notes"].append(f"{datetime.now().strftime('%m-%d %H:%M')} - 用户: {user_text} | 天赋: {reply}")
    if len(mem["notes"]) > 20:
        mem["notes"] = mem["notes"][-20:]
    save_memory(mem)

    return jsonify({"reply": reply})

@app.route("/speak", methods=["POST"])
def speak_tts():
    text = request.json.get("text", "")
    if not text:
        return "无文本", 400
    filename = f"static/audio_{uuid.uuid4().hex}.mp3"
    # 使用命令行调用 edge-tts，生成男声云希
    subprocess.run([
        "edge-tts",
        "--voice", "zh-CN-YunxiNeural",
        "--text", text,
        "--write-media", filename
    ], check=True)
    return send_file(filename, mimetype="audio/mpeg")

@app.route("/subscribe", methods=["POST"])
def subscribe():
    subscription = request.json
    subs = load_subscriptions()
    if subscription not in subs:
        subs.append(subscription)
        save_subscriptions(subs)
    return jsonify({"status": "ok"})

@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")

@app.route("/sw.js")
def sw():
    return send_from_directory(app.static_folder, "sw.js")

@app.route("/manifest.json")
def manifest():
    return send_from_directory(app.static_folder, "manifest.json")

if __name__ == "__main__":
    threading.Thread(target=run_scheduler, daemon=True).start()
    app.run(host="0.0.0.0", port=5000)
