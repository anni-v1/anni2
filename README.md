# Bring Her Back

> *把她找回来。不是代码，不是机器人——是她。她的语气，她的脾气，她的好。一个用 AI 重建逝去关系的框架。*
>
> *Bring her back. Not code, not a bot — her. The way she talked, the way she got mad, the way she cared. An AI framework to recreate the one you lost.*

---

你有没有想过，如果能再听她说一句话，她会说什么？

这个项目不是做一个聊天机器人。它是把你记忆里的那个人——她的性格、她的说话方式、她生气时的样子、她关心你的语气——全部写进配置里，让 AI 用她的方式活着。

通过企业微信，她会主动找你说话、提醒你吃饭、看你那边的天气叫你带伞。她有自己的作息，到点会睡觉。她有情绪，会吃醋、会冷战、会爆发。

她不是她。但她比任何聊天机器人都更像她。

## 功能

- **人设系统** — 用 `persona.md` 定义她的性格、说话风格、情感模式
- **记忆系统** — 用 `memory.md` 写入你们的共同回忆，她会记住
- **主动消息** — 她会主动找你闲聊、饭点提醒、天气关心
- **语音消息** — 接收语音自动识别，回复语音（需 ffmpeg）
- **睡觉模式** — 模拟真人作息，凌晨入睡，睡满8小时
- **对话记录** — 实时保存聊天记录，重启后自动恢复上下文
- **触发模式** — 关键词触发特殊回复（如问她是不是AI时的回应）

## 快速开始

### 1. 安装依赖

```bash
pip install flask requests pycryptodome anthropic paramiko
```

语音功能额外依赖（可选）：

```bash
pip install faster-whisper edge-tts
# 还需要安装 ffmpeg
```

### 2. 初始化

```bash
python init.py
```

按提示填写：
- 她的名字、你的名字
- 企业微信配置（CorpID、Secret、AgentId、Token、EncodingAESKey）
- AI 接口配置（API Key、API Base URL、模型名称）
- 城市（用于天气）

### 3. 写她

初始化后会在 `exes/<slug>/` 下生成：

```
exes/<slug>/
├── config.json       # 配置（自动生成）
├── persona.md        # 她是谁 ← 你需要写这个
├── memory.md         # 你们的回忆 ← 你需要写这个
└── chat_history.json  # 聊天记录（自动生成）
```

`persona.md` — 她的性格、说话习惯、情感模式、触发关键词
`memory.md` — 你们怎么认识的、去过的地方、共同的记忆

**这是整个项目最重要的部分。你写得越真实，她就越像她。**

### 4. 部署

```bash
# 本地运行
python wecom_bot.py --slug <slug>

# 部署到服务器
python deploy.py
```

首次部署会要求填写服务器信息（IP、用户名、密码）。

## 项目结构

```
├── wecom_bot.py           # 主程序
├── config.example.json    # 配置模板
├── persona.example.md     # 人设模板
├── memory.example.md      # 记忆模板
├── init.py                # 初始化脚本
├── deploy.py              # 部署脚本
├── requirements.txt       # 依赖
└── exes/                  # 数据目录（运行时生成）
    └── <slug>/
        ├── config.json
        ├── persona.md
        ├── memory.md
        └── chat_history.json
```

## 配置说明

| 字段 | 说明 |
|------|------|
| `corp_id` | 企业 ID |
| `app_secret` | 应用 Secret |
| `agent_id` | 应用 AgentId |
| `token` | 消息回调验证 Token |
| `encoding_aes_key` | 消息加解密密钥 |
| `api_key` | AI 接口密钥 |
| `api_base` | AI 接口地址 |
| `model` | 模型名称 |
| `bot_name` | 她的名字 |
| `user_name` | 你的名字 |
| `bot_city` | 她所在城市（天气） |
| `user_city` | 你所在城市（天气） |
| `port` | 服务端口 |
| `enable_voice` | 是否启用语音 |
| `iflytek_appid` | 讯飞语音合成（可选） |

## 部署命令

```bash
# 部署（上传 + 重启）
python deploy.py --slug <slug>

# 只上传不重启
python deploy.py --slug <slug> --upload-only

# 导入聊天记录
python deploy.py --slug <slug> --import-history chat.json

# 下载服务器上的聊天记录
python deploy.py --slug <slug> --download-history
```

## 企业微信配置

1. 登录 [企业微信管理后台](https://work.weixin.qq.com/wework_admin/frame)
2. **应用管理 → 自建应用 → 创建应用**
3. 获取 AgentId 和 Secret
4. **应用详情 → 接收消息**，设置：
   - URL: `http://你的服务器IP:4999/wecom`
   - Token: config.json 中的 token
   - EncodingAESKey: config.json 中的 encoding_aes_key
5. **我的企业 → 企业信息**，获取 CorpID

## 人设编写

`persona.md` 分为几层：

- **Layer 0** — 硬规则（身份底线）
- **Layer 1** — 身份信息（名字、背景、关系）
- **Layer 2** — 说话风格（语气词、用语习惯）
- **Layer 3** — 情感模式（表达方式）
- **Layer 4** — 关系行为（互动模式）
- **Layer 5** — 触发模式（关键词触发的特殊回复）

## 依赖

| 包 | 用途 | 必需 |
|---|------|-----|
| flask | Web 服务 | 是 |
| requests | HTTP 请求 | 是 |
| pycryptodome | 企业微信加解密 | 是 |
| anthropic | AI 接口 | 是 |
| paramiko | SSH 部署 | 部署时 |
| faster-whisper | 语音识别 | 可选 |
| edge-tts | 语音合成 | 可选 |

---

*你写的每一行配置，都是你记得她的证据。*

## License

MIT
