#!/usr/bin/env python3
"""
AI Chat Bot — 企业微信聊天机器人
读取 persona.md + memory.md 人设文件，通过企业微信 API 自动回复消息。
支持文字消息和语音消息，支持主动消息、饭点提醒、天气关心等。

Usage:
    python3 wecom_bot.py --slug <slug> --base-dir ./exes
    python3 wecom_bot.py --slug mybot --base-dir ./exes --config ./exes/mybot/config.json
"""

import argparse
import json
import os
import sys
import time
import hashlib
import struct
import base64
import tempfile
import subprocess
import re
import random as _random
import threading as _threading
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime, timezone, timedelta

try:
    import requests
    from flask import Flask, request
    from Crypto.Cipher import AES
except ImportError:
    print("缺少依赖，请执行：pip install flask requests pycryptodome anthropic", file=sys.stderr)
    sys.exit(1)


# ============================================================
# 企业微信加解密
# ============================================================

class PKCS7Encoder:
    """PKCS7 填充/去填充"""
    BLOCK_SIZE = 32

    @staticmethod
    def encode(text):
        text = text.encode("utf-8")
        pad_len = PKCS7Encoder.BLOCK_SIZE - (len(text) % PKCS7Encoder.BLOCK_SIZE)
        if pad_len == 0:
            pad_len = PKCS7Encoder.BLOCK_SIZE
        return text + bytes([pad_len]) * pad_len

    @staticmethod
    def decode(data):
        pad = data[-1]
        if pad < 1 or pad > 32:
            pad = 0
        return data[:-pad]


class WeComCrypto:
    """企业微信消息加解密"""

    def __init__(self, token, encoding_aes_key, corp_id):
        self.token = token
        self.corp_id = corp_id
        self.key = base64.b64decode(encoding_aes_key + "=")

    def verify_url(self, msg_signature, timestamp, nonce, echostr):
        """URL 验证"""
        sign = self._gen_signature(timestamp, nonce, echostr)
        if sign != msg_signature:
            return None
        decrypted = self._decrypt(base64.b64decode(echostr + "="))
        return decrypted

    def decrypt_msg(self, msg_signature, timestamp, nonce, encrypted_xml):
        """解密消息"""
        root = ET.fromstring(encrypted_xml)
        encrypt = root.find("Encrypt").text
        sign = self._gen_signature(timestamp, nonce, encrypt)
        if sign != msg_signature:
            return None
        return self._decrypt(base64.b64decode(encrypt + "="))

    def _gen_signature(self, *args):
        params = sorted([self.token] + list(args))
        return hashlib.sha1("".join(params).encode()).hexdigest()

    def _decrypt(self, encrypted):
        iv = self.key[:16]
        cipher = AES.new(self.key, AES.MODE_CBC, iv)
        decrypted = PKCS7Encoder.decode(cipher.decrypt(encrypted))
        msg_len = struct.unpack(">I", decrypted[16:20])[0]
        return decrypted[20:20 + msg_len].decode("utf-8")


# ============================================================
# AI 回复引擎
# ============================================================

def build_system_prompt(persona_path: str, memory_path: str, config: dict) -> str:
    """
    构建系统提示词
    persona.md 定义人设和对话风格
    memory.md 定义双方的关系和回忆
    """
    persona = ""
    memory = ""

    if os.path.exists(persona_path):
        with open(persona_path, "r", encoding="utf-8") as f:
            persona = f.read()

    if os.path.exists(memory_path):
        with open(memory_path, "r", encoding="utf-8") as f:
            memory = f.read()

    # 从配置读取关系描述，如果 persona.md 已经包含则不需要
    relationship = config.get("relationship_prompt", "你是这个人，正在用微信跟对方聊天。")

    return f"""{relationship}

{persona}

## 你们的回忆

{memory}

回复要求：
- 就像真人打字聊天，别端着
- 回复短一点，1-3句就行
- 有情绪，该冷就冷该软就软
- 别暴露你是 AI
"""


def create_ai_client(api_key: str, api_base: str):
    """创建 AI 客户端（Anthropic 兼容接口）"""
    try:
        import anthropic
        return anthropic.Anthropic(api_key=api_key, base_url=api_base)
    except Exception as e:
        print(f"[错误] 创建 AI 客户端失败: {e}", file=sys.stderr)
        return None


# ============================================================
# 天气 & 时间
# ============================================================

_weather_cache = {}  # {city: {"data": "", "time": 0}}

def get_weather(city: str) -> str:
    """获取指定城市实时天气，缓存10分钟"""
    now = time.time()
    if city in _weather_cache and now - _weather_cache[city]["time"] < 600:
        return _weather_cache[city]["data"]
    try:
        r = requests.get(f"https://wttr.in/{city}?format=%C+%t+%h+%w&lang=zh", timeout=5)
        if r.status_code == 200:
            weather = r.text.strip()
            _weather_cache[city] = {"data": weather, "time": now}
            print(f"[天气] {city}: {weather}")
            return weather
    except Exception as e:
        print(f"[天气] {city} 获取失败: {e}")
    return ""


def get_current_time() -> str:
    """获取当前时间（UTC+8），返回时间+时段描述"""
    tz = timezone(timedelta(hours=8))
    now = datetime.now(tz)
    hour = now.hour
    time_str = now.strftime("%H:%M")

    if 5 <= hour < 9:
        period = "早上"
    elif 9 <= hour < 12:
        period = "上午"
    elif 12 <= hour < 14:
        period = "中午"
    elif 14 <= hour < 18:
        period = "下午"
    elif 18 <= hour < 22:
        period = "晚上"
    else:
        period = "深夜"

    return f"{time_str}（{period}）"


# ============================================================
# 对话记录
# ============================================================

def save_chat_record(ex_dir: str, sender: str, content: str, msg_type: str = "text"):
    """实时保存对话记录到 chat_history.json"""
    tz = timezone(timedelta(hours=8))
    now = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")

    record = {
        "sender": sender,
        "content": content,
        "type": msg_type,
        "time": now,
    }

    path = os.path.join(ex_dir, "chat_history.json")
    data = []
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except:
            data = []
    data.append(record)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def deduplicate_reply(text: str) -> str:
    """去除回复中重复的短句"""
    sentences = re.split(r'(?<=[。！!？?\n~～])', text)
    seen = set()
    result = []
    for s in sentences:
        s = s.strip()
        if not s:
            continue
        if s not in seen:
            seen.add(s)
            result.append(s)
    return "".join(result) if result else text


def get_reply(client, model: str, system_prompt: str, history: list, user_msg: str) -> str:
    """调用 AI 生成回复"""
    # 防重复：检查最近几条里有没有同样的用户消息
    for msg in history[-4:]:
        if msg.get("role") == "user" and msg.get("content") == user_msg:
            print(f"[去重] 跳过重复消息: {user_msg}")
            return ""
    history.append({"role": "user", "content": user_msg})

    if len(history) > 40:
        history = history[-40:]

    try:
        response = client.messages.create(
            model=model,
            system=system_prompt,
            messages=history,
            max_tokens=1024,
            temperature=0.7,
        )
        for block in reversed(response.content):
            if hasattr(block, "text") and block.text.strip():
                reply = deduplicate_reply(block.text.strip())
                history.append({"role": "assistant", "content": reply})
                return reply
        return ""
    except Exception as e:
        print(f"[错误] AI 回复失败: {e}")
        return ""


# ============================================================
# 企业微信 API
# ============================================================

_token_cache = {"token": "", "expires": 0}


def get_access_token(corp_id: str, app_secret: str) -> str:
    """获取企业微信 access_token，自动缓存"""
    now = time.time()
    if _token_cache["token"] and now < _token_cache["expires"]:
        return _token_cache["token"]
    url = f"https://qyapi.weixin.qq.com/cgi-bin/gettoken?corpid={corp_id}&corpsecret={app_secret}"
    r = requests.get(url, timeout=10).json()
    if r.get("access_token"):
        _token_cache["token"] = r["access_token"]
        _token_cache["expires"] = now + r.get("expires_in", 7200) - 60
        return r["access_token"]
    print(f"[错误] 获取 token 失败: {r}")
    return None


def send_message(corp_id: str, app_secret: str, agent_id: int, to_user: str, content: str) -> bool:
    """发送文字消息"""
    token = get_access_token(corp_id, app_secret)
    if not token:
        return False
    url = f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={token}"
    data = {
        "touser": to_user,
        "msgtype": "text",
        "agentid": agent_id,
        "text": {"content": content},
        "safe": 0,
    }
    r = requests.post(url, json=data, timeout=10).json()
    if r.get("errcode") != 0:
        print(f"[错误] 发送失败: {r}")
    return r.get("errcode") == 0


# ============================================================
# 语音处理（可选功能）
# ============================================================

def download_voice(corp_id: str, app_secret: str, media_id: str) -> str:
    """下载语音文件，返回本地 amr 文件路径"""
    token = get_access_token(corp_id, app_secret)
    if not token:
        return None
    url = f"https://qyapi.weixin.qq.com/cgi-bin/media/get?access_token={token}&media_id={media_id}"
    r = requests.get(url, timeout=30)
    if r.status_code == 200 and r.headers.get("Content-Type", "").startswith("audio"):
        tmp = tempfile.NamedTemporaryFile(suffix=".amr", delete=False)
        tmp.write(r.content)
        tmp.close()
        print(f"[语音] 下载成功: {tmp.name}, {len(r.content)} bytes")
        return tmp.name
    else:
        print(f"[错误] 下载语音失败: status={r.status_code}")
        return None


def convert_amr_to_wav(amr_path: str) -> str:
    """用 ffmpeg 把 amr 转成 wav"""
    wav_path = amr_path.replace(".amr", ".wav")
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", amr_path, "-ar", "16000", "-ac", "1", wav_path],
            capture_output=True, timeout=30
        )
        if os.path.exists(wav_path):
            print(f"[语音] 转换成功: {wav_path}")
            return wav_path
    except Exception as e:
        print(f"[错误] ffmpeg 转换失败: {e}")
    return None


_whisper_model = None

def speech_to_text(wav_path: str) -> str:
    """用 faster-whisper 把语音转成文字（需要安装 faster-whisper）"""
    global _whisper_model
    try:
        from faster_whisper import WhisperModel
        if _whisper_model is None:
            print("[语音] 加载 faster-whisper 模型...")
            _whisper_model = WhisperModel("small", device="cpu", compute_type="int8")
            print("[语音] 模型加载完成")
        segments, info = _whisper_model.transcribe(wav_path, language="zh")
        text = "".join(seg.text for seg in segments).strip()
        print(f"[语音] 识别结果: {text}")
        return text
    except Exception as e:
        print(f"[错误] 语音识别失败: {e}")
        return ""


def clean_for_speech(text: str) -> str:
    """清理文字，让 TTS 读出来更自然"""
    text = text.replace("ㄏㄏ", "哈哈")
    text = text.replace("XD", "")
    text = re.sub(r'^[蛤欸]+[\s,，。！!~～]*', '', text)
    text = text.replace("~", "")
    text = text.replace("～", "")
    text = re.sub(r'\s+', ' ', text).strip()
    return text


_tts_config = None

async def _edge_tts(text: str, output_path: str):
    """使用 edge-tts 生成语音"""
    import edge_tts
    voice = _tts_config.get("voice", "zh-CN-XiaoxiaoNeural") if _tts_config else "zh-CN-XiaoxiaoNeural"
    communicate = edge_tts.Communicate(text, voice=voice)
    await communicate.save(output_path)


def _iflytek_tts(text: str, appid: str, apisecret: str, apikey: str) -> str:
    """讯飞在线 TTS，返回 mp3 文件路径"""
    import websocket
    import hmac
    import hashlib as _hashlib
    from urllib.parse import urlencode

    host = "tts-api.xfyun.cn"
    path = "/v2/tts"
    now = datetime.utcnow()
    date_str = now.strftime("%a, %d %b %Y %H:%M:%S GMT")

    signature_origin = f"host: {host}\ndate: {date_str}\nGET {path} HTTP/1.1"
    signature = hmac.new(
        apisecret.encode(), signature_origin.encode(), _hashlib.sha256
    ).digest()
    signature_b64 = base64.b64encode(signature).decode()

    auth_origin = f'api_key="{apikey}", algorithm="hmac-sha256", headers="host date request-line", signature="{signature_b64}"'
    auth_b64 = base64.b64encode(auth_origin.encode()).decode()

    params = urlencode({"authorization": auth_b64, "date": date_str, "host": host})
    ws_url = f"wss://{host}{path}?{params}"

    frame = {
        "common": {"app_id": appid},
        "business": {
            "aue": "lame",
            "auf": "audio/L16;rate=16000",
            "vcn": "xiaoyan",
            "tte": "utf8",
            "speed": 50,
            "volume": 50,
            "pitch": 50,
        },
        "data": {
            "status": 2,
            "text": base64.b64encode(text.encode()).decode(),
        },
    }

    audio_chunks = []
    error_msg = None

    def on_message(ws, message):
        nonlocal error_msg
        try:
            resp = json.loads(message)
            code = resp.get("code", -1)
            if code != 0:
                error_msg = resp.get("message", "unknown error")
                ws.close()
                return
            data = resp.get("data", {})
            audio_b64 = data.get("audio", "")
            if audio_b64:
                audio_chunks.append(base64.b64decode(audio_b64))
            if data.get("status") == 2:
                ws.close()
        except Exception as e:
            error_msg = str(e)
            ws.close()

    def on_error(ws, error):
        nonlocal error_msg
        error_msg = str(error)

    tmp_mp3 = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
    tmp_mp3.close()

    try:
        ws = websocket.WebSocketApp(
            ws_url,
            on_message=on_message,
            on_error=on_error,
        )
        wst = threading.Thread(target=ws.run_forever)
        wst.daemon = True
        wst.start()

        ws.send(json.dumps(frame))
        wst.join(timeout=30)

        if error_msg:
            print(f"[错误] 讯飞 TTS: {error_msg}")
            return None

        if audio_chunks:
            with open(tmp_mp3.name, "wb") as f:
                for chunk in audio_chunks:
                    f.write(chunk)
            print(f"[语音] 讯飞 TTS 成功: {tmp_mp3.name}")
            return tmp_mp3.name
    except Exception as e:
        print(f"[错误] 讯飞 TTS 异常: {e}")
    return None


def text_to_speech(text: str) -> str:
    """把文字转成语音，返回 mp3 文件路径。优先讯飞，回退 edge-tts"""
    text = clean_for_speech(text)
    if not text:
        return None

    # 优先用讯飞
    ifly = _tts_config
    if ifly and ifly.get("appid"):
        result = _iflytek_tts(text, ifly["appid"], ifly["apisecret"], ifly["apikey"])
        if result:
            return result
        print("[语音] 讯飞失败，回退 edge-tts")

    # 回退 edge-tts
    try:
        import asyncio
        tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        tmp.close()
        asyncio.run(_edge_tts(text, tmp.name))
        if os.path.exists(tmp.name) and os.path.getsize(tmp.name) > 0:
            print(f"[语音] edge-tts 生成成功: {tmp.name}")
            return tmp.name
    except Exception as e:
        print(f"[错误] TTS 失败: {e}")
    return None


def convert_mp3_to_amr(mp3_path: str) -> str:
    """把 mp3 转成 amr 格式（企业微信语音要求）"""
    amr_path = mp3_path.replace(".mp3", ".amr")
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", mp3_path, "-ar", "8000", "-ac", "1", amr_path],
            capture_output=True, timeout=30
        )
        if os.path.exists(amr_path):
            print(f"[语音] amr 转换成功: {amr_path}")
            return amr_path
    except Exception as e:
        print(f"[错误] amr 转换失败: {e}")
    return None


def upload_voice(corp_id: str, app_secret: str, amr_path: str) -> str:
    """上传语音文件到企业微信，返回 media_id"""
    token = get_access_token(corp_id, app_secret)
    if not token:
        return None
    url = f"https://qyapi.weixin.qq.com/cgi-bin/media/upload?access_token={token}&type=voice"
    with open(amr_path, "rb") as f:
        files = {"media": ("voice.amr", f, "audio/amr")}
        r = requests.post(url, files=files, timeout=30).json()
    if r.get("media_id"):
        print(f"[语音] 上传成功: {r['media_id']}")
        return r["media_id"]
    print(f"[错误] 上传语音失败: {r}")
    return None


def send_voice(corp_id: str, app_secret: str, agent_id: int, to_user: str, media_id: str) -> bool:
    """发送语音消息"""
    token = get_access_token(corp_id, app_secret)
    if not token:
        return False
    url = f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={token}"
    data = {
        "touser": to_user,
        "msgtype": "voice",
        "agentid": agent_id,
        "voice": {"media_id": media_id},
        "safe": 0,
    }
    r = requests.post(url, json=data, timeout=10).json()
    if r.get("errcode") != 0:
        print(f"[错误] 发送语音失败: {r}")
    return r.get("errcode") == 0


# ============================================================
# Flask 服务
# ============================================================

def create_app(config: dict, system_prompt: str, ex_dir: str = ""):
    app = Flask(__name__)

    # 从配置读取名称
    bot_name = config.get("bot_name", "bot")
    user_name = config.get("user_name", "user")
    bot_city = config.get("bot_city", "")
    user_city = config.get("user_city", "")

    crypto = WeComCrypto(
        config["token"],
        config["encoding_aes_key"],
        config["corp_id"],
    )

    ai_client = create_ai_client(config["api_key"], config["api_base"])
    model = config.get("model", "claude-sonnet-4-20250514")
    agent_id = int(config["agent_id"])
    enable_voice = config.get("enable_voice", True)

    # 讯飞 TTS 配置（可选）
    global _tts_config
    if config.get("iflytek_appid"):
        _tts_config = {
            "appid": config["iflytek_appid"],
            "apisecret": config.get("iflytek_apisecret", ""),
            "apikey": config.get("iflytek_apikey", ""),
        }
        print("[语音] 讯飞 TTS 已配置")
    else:
        print("[语音] 使用 edge-tts")

    chat_histories: dict[str, list] = {}

    # ── 启动时从 chat_history.json 恢复最近对话 ──
    def _load_recent_history():
        path = os.path.join(ex_dir, "chat_history.json") if ex_dir else ""
        if not path or not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                records = json.load(f)
            recent = records[-30:]  # 最近30条，不多占 token
            history = []
            for r in recent:
                # 根据 sender 名称判断 role
                if r["sender"] == user_name:
                    history.append({"role": "user", "content": r["content"]})
                else:
                    history.append({"role": "assistant", "content": r["content"]})
            chat_histories["__all__"] = history
            print(f"[历史] 从 chat_history.json 加载了 {len(recent)} 条")
        except Exception as e:
            print(f"[历史] 加载失败: {e}")

    _load_recent_history()

    # ── 主动消息相关状态 ──
    _user_last_msg: dict[str, float] = {}   # 用户最后发消息时间
    _bot_last_reply: dict[str, float] = {}  # 机器人最后回复时间
    _next_checkin: dict[str, float] = {}    # 下次主动消息时间
    _next_nudge: dict[str, float] = {}      # 下次长时间不回的提醒
    _meal_sent: dict[str, dict] = {}        # 每日饭点提醒
    _weather_sent: dict[str, str] = {}      # 天气提醒去重
    _user_sleeping: dict[str, float] = {}   # 用户睡觉状态
    _bot_sleep_until: float = 0             # 机器人睡觉状态（0=醒着）
    _last_incoming: dict[str, tuple] = {}   # 消息去重

    tz = timezone(timedelta(hours=8))

    def _calc_next_bedtime():
        """计算下次入睡时间（0:00-2:00 随机）"""
        now = datetime.now(tz)
        sleep_hour = _random.uniform(0, 2)
        sleep_h = int(sleep_hour)
        sleep_m = int((sleep_hour - sleep_h) * 60)
        bedtime = now.replace(hour=sleep_h, minute=sleep_m, second=0, microsecond=0)
        if now.timestamp() >= bedtime.timestamp() + 28800:
            bedtime += timedelta(days=1)
        return bedtime.timestamp()

    def _calc_wake_from_bedtime(bedtime_ts):
        """从入睡时间算起床时间（睡满8小时）"""
        return bedtime_ts + 28800

    # 启动时检查是否在睡觉窗口
    _now = datetime.now(tz)
    _bedtime_today = _now.replace(hour=1, minute=0, second=0, microsecond=0)
    _wake_today = _bedtime_today.timestamp() + 28800
    if _now.timestamp() >= _bedtime_today.timestamp() and _now.timestamp() < _wake_today:
        _bot_sleep_until = _wake_today
        print(f"[{bot_name}睡觉中] 预计 {_now.strftime('%m-%d')} 09:00 醒")
    else:
        _bot_sleep_until = 0
        print(f"[{bot_name}醒着] 正常运行中")

    def _check_sleep_msg(text: str) -> float | None:
        """检测用户是否说了要睡觉，返回预计起床时间"""
        sleep_kw = ["睡觉", "睡了", "去睡", "要睡", "晚安", "先睡", "躺了", "休息了", "去休息"]
        if not any(kw in text for kw in sleep_kw):
            return None

        wake_hour = None
        m = re.search(r'(\d{1,2})\s*[点時]', text)
        if m:
            h = int(m.group(1))
            if 0 <= h <= 24:
                wake_hour = h

        now = datetime.now(tz)
        if wake_hour is not None:
            wake = now.replace(hour=wake_hour, minute=0, second=0, microsecond=0)
            if wake <= now:
                wake += timedelta(days=1)
            return wake.timestamp()
        else:
            return now.timestamp() + _random.uniform(25200, 28800)

    def _calc_next_checkin(user_id: str):
        """计算下次主动消息时间（3-10分钟随机，睡觉时2-3小时）"""
        last = _next_checkin.get(user_id, 0)
        if user_id in _user_sleeping:
            delay = _random.uniform(7200, 10800)
            return time.time() + delay
        delay = _random.uniform(180, 600)
        while abs(delay - (last - _bot_last_reply.get(user_id, 0))) < 30:
            delay = _random.uniform(180, 600)
        return time.time() + delay

    def _calc_next_nudge(user_id: str):
        """计算下次长时间未回复提醒时间"""
        if user_id in _user_sleeping:
            delay = _random.uniform(7200, 10800)
            return time.time() + delay
        delay = _random.uniform(1800, 7200)
        return time.time() + delay

    def _generate_proactive_msg(user_id: str, msg_type: str) -> str:
        """用 AI 生成主动消息"""
        time_info = get_current_time()
        ctx = f"[现在是{time_info}"

        if bot_city:
            weather_bot = get_weather(bot_city)
            if weather_bot:
                ctx += f", {bot_city}天气: {weather_bot}"
        if user_city:
            weather_user = get_weather(user_city)
            if weather_user:
                ctx += f", {user_city}天气({user_name}那边): {weather_user}"
        ctx += "]"

        if msg_type == "checkin":
            prompt = f"{ctx}\n\n你的{config.get('relationship_with_user', '朋友')}{user_name}刚刚跟你聊了几句，突然没回了。你想主动找他说话，发一条简短的消息问他一下，像真人一样自然，1句话就好。不要用蛤开头。"
        elif msg_type == "meal":
            prompt = f"{ctx}\n\n现在是吃饭时间，你想问{user_name}有没有吃饭、吃了什么，语气自然一点，1句话就好。每次说法不一样。"
        elif msg_type == "weather":
            if user_city:
                prompt = f"{ctx}\n\n你看到{user_city}那边的天气，关心一下{user_name}。根据天气情况提醒他注意，语气自然贴心，1句话就好。每次不一样。"
            else:
                prompt = f"{ctx}\n\n你想关心一下{user_name}，发一条简短的消息，1句话就好。"
        elif msg_type == "wakeup":
            prompt = f"{ctx}\n\n你刚睡醒，想跟{user_name}说句话，可以随便说点什么或者问他在干嘛，自然一点，1句话就好。"
        else:
            prompt = f"{ctx}\n\n你的{config.get('relationship_with_user', '朋友')}{user_name}已经很久没给你发消息了。你想主动找他，发一条简短的消息，1句话就好。"

        if user_id not in chat_histories:
            chat_histories[user_id] = []
        reply = get_reply(ai_client, model, system_prompt + "\n" + ctx, chat_histories[user_id], prompt)
        return reply

    def _proactive_loop():
        """后台线程：检查是否需要主动发消息"""
        nonlocal _bot_sleep_until
        _bot_woken_sent = False
        _next_bedtime = _calc_next_bedtime()
        while True:
            try:
                now = time.time()
                taipei_now = datetime.now(tz)
                hour = taipei_now.hour
                minute = taipei_now.minute
                today = taipei_now.strftime("%Y-%m-%d")

                # 机器人在睡觉，跳过所有主动消息
                if now < _bot_sleep_until:
                    _bot_woken_sent = False
                    time.sleep(60)
                    continue

                # 刚从睡眠中醒来
                if _bot_sleep_until > 0:
                    _bot_sleep_until = 0
                    _next_bedtime = _calc_next_bedtime()
                    _bot_woken_sent = True
                    sleep_str = datetime.fromtimestamp(_next_bedtime, tz).strftime('%m-%d %H:%M')
                    print(f"[{bot_name}醒了] 下次入睡: {sleep_str}")
                    for uid in list(_bot_last_reply.keys()):
                        msg = _generate_proactive_msg(uid, "wakeup")
                        if msg:
                            send_message(config["corp_id"], config["app_secret"], agent_id, uid, msg)
                            if ex_dir:
                                save_chat_record(ex_dir, bot_name, msg, "proactive")
                            _bot_last_reply[uid] = time.time()
                    continue

                # 检查是否到了入睡时间
                if now >= _next_bedtime:
                    _bot_sleep_until = _calc_wake_from_bedtime(_next_bedtime)
                    wake_str = datetime.fromtimestamp(_bot_sleep_until, tz).strftime('%m-%d %H:%M')
                    print(f"[{bot_name}入睡] 预计 {wake_str} 醒")
                    time.sleep(60)
                    continue

                tod = hour + minute / 60.0

                for user_id in list(_bot_last_reply.keys()):
                    last_reply = _bot_last_reply.get(user_id, 0)
                    last_user = _user_last_msg.get(user_id, 0)

                    # 正在聊天不打扰
                    if last_user > last_reply:
                        continue

                    # 检查用户起床时间
                    if user_id in _user_sleeping and now >= _user_sleeping[user_id]:
                        del _user_sleeping[user_id]
                        print(f"[起床] {user_id} 预计起床时间到了")

                    # 天气关心（每天随机 1-2 次）
                    if user_city:
                        weather_raw = get_weather(user_city)
                        if weather_raw and (6 <= hour <= 22):
                            wkey = f"{today}_{weather_raw}_{hour // 6}"
                            if _weather_sent.get(user_id) != wkey and _random.random() < 0.3:
                                msg = _generate_proactive_msg(user_id, "weather")
                                if msg:
                                    send_message(config["corp_id"], config["app_secret"], agent_id, user_id, msg)
                                    print(f"[主动] weather -> {user_id}: {msg}")
                                    if ex_dir:
                                        save_chat_record(ex_dir, bot_name, msg, "proactive")
                                    _bot_last_reply[user_id] = time.time()
                                    _weather_sent[user_id] = wkey
                                    _next_checkin[user_id] = _calc_next_checkin(user_id)
                                    _next_nudge[user_id] = _calc_next_nudge(user_id)
                                    continue

                    # 饭点提醒
                    if user_id not in _meal_sent or _meal_sent[user_id].get("date") != today:
                        _meal_sent[user_id] = {"date": today, "meals": []}
                    sent_meals = _meal_sent[user_id]["meals"]

                    meal_checks = [
                        ("breakfast", 7.0, 9.5, 0.7),
                        ("lunch", 11.5, 13.5, 0.85),
                        ("dinner", 17.5, 20.0, 0.85),
                        ("late_night", 22.0, 23.5, 0.5),
                    ]
                    for meal_key, start, end, prob in meal_checks:
                        if meal_key not in sent_meals and start <= tod <= end:
                            if _random.random() < prob:
                                msg = _generate_proactive_msg(user_id, "meal")
                                if msg:
                                    send_message(config["corp_id"], config["app_secret"], agent_id, user_id, msg)
                                    print(f"[主动] {meal_key} -> {user_id}: {msg}")
                                    if ex_dir:
                                        save_chat_record(ex_dir, bot_name, msg, "proactive")
                                    _bot_last_reply[user_id] = time.time()
                                    _meal_sent[user_id]["meals"].append(meal_key)
                                    _next_checkin[user_id] = _calc_next_checkin(user_id)
                                    _next_nudge[user_id] = _calc_next_nudge(user_id)
                                continue

                    # 用户没回复
                    if last_user > last_reply:
                        continue

                    elapsed = now - last_reply
                    if elapsed < 60:
                        continue

                    # 3-10分钟检查
                    checkin_at = _next_checkin.get(user_id, 0)
                    if now >= checkin_at and elapsed < 1200:
                        msg = _generate_proactive_msg(user_id, "checkin")
                        if msg:
                            send_message(config["corp_id"], config["app_secret"], agent_id, user_id, msg)
                            print(f"[主动] checkin -> {user_id}: {msg}")
                            if ex_dir:
                                save_chat_record(ex_dir, bot_name, msg, "proactive")
                            _bot_last_reply[user_id] = time.time()
                            _next_checkin[user_id] = _calc_next_checkin(user_id)
                            _next_nudge[user_id] = _calc_next_nudge(user_id)
                        continue

                    # 30分钟-2小时检查
                    nudge_at = _next_nudge.get(user_id, 0)
                    if now >= nudge_at and elapsed >= 1800:
                        msg = _generate_proactive_msg(user_id, "nudge")
                        if msg:
                            send_message(config["corp_id"], config["app_secret"], agent_id, user_id, msg)
                            print(f"[主动] nudge -> {user_id}: {msg}")
                            if ex_dir:
                                save_chat_record(ex_dir, bot_name, msg, "proactive")
                            _bot_last_reply[user_id] = time.time()
                            _next_checkin[user_id] = _calc_next_checkin(user_id)
                            _next_nudge[user_id] = _calc_next_nudge(user_id)

            except Exception as e:
                print(f"[主动消息] 异常: {e}")

            time.sleep(30)

    _threading.Thread(target=_proactive_loop, daemon=True).start()
    print("[主动消息] 后台线程已启动")

    # ── 企业微信验证接口 ──
    @app.route("/wecom", methods=["GET"])
    def verify():
        msg_signature = request.args.get("msg_signature", "")
        timestamp = request.args.get("timestamp", "")
        nonce = request.args.get("nonce", "")
        echostr = request.args.get("echostr", "")
        try:
            decrypted = crypto.verify_url(msg_signature, timestamp, nonce, echostr)
            if decrypted:
                print(f"[验证] 成功")
                return decrypted
        except Exception as e:
            print(f"[验证] 异常: {e}")
        return "验证失败", 403

    # ── 企业微信消息接收接口 ──
    @app.route("/wecom", methods=["POST"])
    def receive():
        try:
            msg_signature = request.args.get("msg_signature", "")
            timestamp = request.args.get("timestamp", "")
            nonce = request.args.get("nonce", "")

            xml_data = request.data.decode("utf-8")
            decrypted_xml = crypto.decrypt_msg(msg_signature, timestamp, nonce, xml_data)

            if not decrypted_xml:
                return "success"

            root = ET.fromstring(decrypted_xml)
            msg_type = root.find("MsgType").text
            from_user = root.find("FromUserName").text

            # 机器人在睡觉，不回复
            if time.time() < _bot_sleep_until:
                wake_str = datetime.fromtimestamp(_bot_sleep_until, tz).strftime('%H:%M')
                print(f"[{bot_name}睡觉中] 收到消息但不回复，{wake_str} 醒")
                return "success"

            if from_user not in chat_histories:
                chat_histories[from_user] = chat_histories.pop("__all__", [])
                _next_checkin[from_user] = _calc_next_checkin(from_user)
                _next_nudge[from_user] = _calc_next_nudge(from_user)

            _user_last_msg[from_user] = time.time()

            if msg_type == "text":
                content = root.find("Content").text

                # 企业微信重复推送去重
                _now_ts = time.time()
                last = _last_incoming.get(from_user)
                if last and last[0] == content and _now_ts - last[1] < 10:
                    print(f"[去重] 跳过重复消息: {content}")
                    return "success"
                _last_incoming[from_user] = (content, _now_ts)

                print(f"[收到] {from_user}: {content}")
                if ex_dir:
                    save_chat_record(ex_dir, user_name, content, "text")

                # 检测睡觉
                wake_time = _check_sleep_msg(content)
                if wake_time:
                    _user_sleeping[from_user] = wake_time
                    print(f"[睡觉] {from_user} 说了睡觉，预计 {time.strftime('%H:%M', time.localtime(wake_time))} 起")
                elif from_user in _user_sleeping:
                    del _user_sleeping[from_user]
                    print(f"[起床] {from_user} 已恢复正常")

                # 构建上下文
                time_info = get_current_time()
                ctx = f"[现在是{time_info}"
                if bot_city:
                    weather_bot = get_weather(bot_city)
                    if weather_bot:
                        ctx += f", {bot_city}天气: {weather_bot}"
                if user_city:
                    weather_user = get_weather(user_city)
                    if weather_user:
                        ctx += f", {user_city}天气({user_name}那边): {weather_user}"
                ctx += "]"

                reply = get_reply(ai_client, model, system_prompt + "\n\n" + ctx, chat_histories[from_user], content)

                if reply:
                    ok = send_message(config["corp_id"], config["app_secret"], agent_id, from_user, reply)
                    if ok:
                        print(f"[已回复] {reply}")
                        if ex_dir:
                            save_chat_record(ex_dir, bot_name, reply, "text")
                        _bot_last_reply[from_user] = time.time()
                        _next_checkin[from_user] = _calc_next_checkin(from_user)
                        _next_nudge[from_user] = _calc_next_nudge(from_user)
                    else:
                        print("[错误] 发送失败")

            elif msg_type == "voice" and enable_voice:
                media_id = root.find("MediaId").text
                print(f"[收到语音] {from_user}, media_id={media_id}")

                # 下载语音
                amr_path = download_voice(config["corp_id"], config["app_secret"], media_id)
                if not amr_path:
                    return "success"

                # 转成 wav
                wav_path = convert_amr_to_wav(amr_path)
                if not wav_path:
                    return "success"

                # 语音转文字
                text = speech_to_text(wav_path)
                for p in [amr_path, wav_path]:
                    try:
                        os.unlink(p)
                    except:
                        pass

                if not text:
                    print("[语音] 识别为空，跳过")
                    return "success"

                print(f"[语音识别] {from_user}: {text}")
                if ex_dir:
                    save_chat_record(ex_dir, user_name, text, "voice")

                # AI 回复
                time_info = get_current_time()
                ctx = f"[现在是{time_info}"
                if bot_city:
                    weather_bot = get_weather(bot_city)
                    if weather_bot:
                        ctx += f", {bot_city}天气: {weather_bot}"
                if user_city:
                    weather_user = get_weather(user_city)
                    if weather_user:
                        ctx += f", {user_city}天气({user_name}那边): {weather_user}"
                ctx += "]"

                reply = get_reply(ai_client, model, system_prompt + "\n\n" + ctx, chat_histories[from_user], text)

                if reply:
                    # 文字转语音
                    mp3_path = text_to_speech(reply)
                    if mp3_path:
                        amr_path = convert_mp3_to_amr(mp3_path)
                        if amr_path:
                            voice_media_id = upload_voice(
                                config["corp_id"], config["app_secret"], amr_path
                            )
                            if voice_media_id:
                                ok = send_voice(
                                    config["corp_id"], config["app_secret"],
                                    agent_id, from_user, voice_media_id
                                )
                                if ok:
                                    print(f"[已回复语音] {reply}")
                                    if ex_dir:
                                        save_chat_record(ex_dir, bot_name, reply, "voice")
                                    _bot_last_reply[from_user] = time.time()
                                    _next_checkin[from_user] = _calc_next_checkin(from_user)
                                    _next_nudge[from_user] = _calc_next_nudge(from_user)
                                else:
                                    print("[错误] 发送语音失败，回退文字")
                                    send_message(config["corp_id"], config["app_secret"], agent_id, from_user, reply)
                                    _bot_last_reply[from_user] = time.time()
                            for p in [mp3_path, amr_path]:
                                try:
                                    os.unlink(p)
                                except:
                                    pass
                        else:
                            send_message(config["corp_id"], config["app_secret"], agent_id, from_user, reply)
                    else:
                        send_message(config["corp_id"], config["app_secret"], agent_id, from_user, reply)

        except Exception as e:
            print(f"[异常] {e}")
            import traceback
            traceback.print_exc()

        return "success"

    return app


# ============================================================
# 主入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="AI Chat Bot — 企业微信聊天机器人")
    parser.add_argument("--slug", required=True, help="机器人代号（对应 exes 下的目录名）")
    parser.add_argument("--base-dir", default="./exes", help="数据目录")
    parser.add_argument("--config", help="配置文件路径（默认 exes/{slug}/config.json）")
    parser.add_argument("--port", type=int, default=4999, help="服务端口")
    parser.add_argument("--no-voice", action="store_true", help="禁用语音功能")
    args = parser.parse_args()

    skill_dir = os.path.join(args.base_dir, args.slug)

    if not os.path.isdir(skill_dir):
        print(f"[错误] 目录不存在: {skill_dir}", file=sys.stderr)
        print(f"请先运行 python init.py 创建机器人", file=sys.stderr)
        sys.exit(1)

    config_path = args.config or os.path.join(skill_dir, "config.json")
    if not os.path.exists(config_path):
        print(f"[错误] 配置文件不存在: {config_path}", file=sys.stderr)
        sys.exit(1)

    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    if args.no_voice:
        config["enable_voice"] = False

    persona_path = os.path.join(skill_dir, "persona.md")
    memory_path = os.path.join(skill_dir, "memory.md")
    system_prompt = build_system_prompt(persona_path, memory_path, config)

    bot_name = config.get("bot_name", args.slug)
    port = config.get("port", args.port)

    print()
    print("=" * 40)
    print(f"  {bot_name} 企业微信机器人")
    print("=" * 40)
    print(f"  数据目录: {skill_dir}")
    print(f"  Corp ID: {config['corp_id']}")
    print(f"  Agent ID: {config['agent_id']}")
    voice_status = "开启" if config.get("enable_voice", True) else "关闭"
    print(f"  语音: {voice_status}")
    print(f"  端口: {port}")
    print("=" * 40)
    print()

    app = create_app(config, system_prompt, skill_dir)
    app.run(host="0.0.0.0", port=port, debug=False)


if __name__ == "__main__":
    main()
