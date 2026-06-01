#!/usr/bin/env python3
"""
AI Chat Bot — 初始化脚本
交互式创建机器人配置，生成 config.json + persona.md + memory.md

Usage:
    python init.py
    python init.py --slug mybot --base-dir ./exes
"""

import json
import os
import sys
import argparse
import shutil


def ask(prompt: str, default: str = "") -> str:
    """询问用户输入，支持默认值"""
    if default:
        hint = f" [{default}]"
    else:
        hint = ""
    while True:
        val = input(f"  {prompt}{hint}: ").strip()
        if val:
            return val
        if default:
            return default
        print(f"  ⚠ 不能为空，请输入")


def ask_yes_no(prompt: str, default: bool = True) -> bool:
    """询问是否"""
    hint = " [Y/n]" if default else " [y/N]"
    while True:
        val = input(f"  {prompt}{hint}: ").strip().lower()
        if not val:
            return default
        if val in ("y", "yes", "是", "对"):
            return True
        if val in ("n", "no", "否", "不"):
            return False


def main():
    parser = argparse.ArgumentParser(description="AI Chat Bot 初始化")
    parser.add_argument("--slug", default="", help="机器人目录名")
    parser.add_argument("--base-dir", default="./exes", help="数据目录")
    args = parser.parse_args()

    print()
    print("=" * 40)
    print("  AI Chat Bot — 初始化配置")
    print("=" * 40)
    print()

    # ── 基本信息 ──
    print("【基本信息】")
    bot_name = ask("她的名字（显示名）")
    user_name = ask("你的名字（对方叫你什么）")
    slug = ask("目录名（英文，用于文件夹名）", args.slug or bot_name.lower().replace(" ", ""))

    skill_dir = os.path.join(args.base_dir, slug)
    if os.path.exists(skill_dir):
        if not ask_yes_no(f"目录 {skill_dir} 已存在，覆盖？"):
            print("已取消")
            return
    os.makedirs(skill_dir, exist_ok=True)

    # ── 关系设置 ──
    print()
    print("【关系设置】")
    relationship = ask("你们的关系", "朋友")
    bot_city = ask("她所在城市", "台北")
    user_city = ask("你所在城市", "广州")
    years = ask("在一起几年", "1")

    # ── 企业微信配置 ──
    print()
    print("【企业微信配置】")
    print("  (在企业微信管理后台 -> 应用管理 -> 自建应用 中获取)")
    corp_id = ask("企业 ID (CorpID)")
    app_secret = ask("应用 Secret")
    agent_id = ask("应用 AgentId", "1000002")
    token = ask("Token (用于消息回调验证)")
    encoding_aes_key = ask("EncodingAESKey", "01234567890123456789012345678901234567")

    # ── AI 接口配置 ──
    print()
    print("【AI 接口配置】")
    print("  支持 Anthropic 兼容接口（如 Anthropic、Claude API 等）")
    api_key = ask("API Key")
    api_base = ask("API Base URL", "https://api.anthropic.com")
    model = ask("模型名称", "claude-sonnet-4-20250514")

    # ── 语音配置（可选）──
    print()
    print("【语音配置（可选，直接回车跳过）】")
    print("  不填则自动使用 edge-tts 免费方案")
    iflytek_appid = ask("讯飞 APPID（可选）", "")
    iflytek_apisecret = ""
    iflytek_apikey = ""
    if iflytek_appid:
        iflytek_apisecret = ask("讯飞 APISecret")
        iflytek_apikey = ask("讯飞 APIKey")

    # ── 端口 ──
    print()
    port = ask("服务端口", "4999")

    # ── 生成 config.json ──
    config = {
        "corp_id": corp_id,
        "app_secret": app_secret,
        "agent_id": agent_id,
        "token": token,
        "encoding_aes_key": encoding_aes_key,
        "api_key": api_key,
        "api_base": api_base,
        "model": model,
        "bot_name": bot_name,
        "user_name": user_name,
        "bot_city": bot_city,
        "user_city": user_city,
        "relationship_with_user": relationship,
        "relationship_prompt": f"你是{bot_name}，正在用微信跟{user_name}聊天。",
        "port": int(port),
        "enable_voice": True,
    }

    if iflytek_appid:
        config["iflytek_appid"] = iflytek_appid
        config["iflytek_apisecret"] = iflytek_apisecret
        config["iflytek_apikey"] = iflytek_apikey

    config_path = os.path.join(skill_dir, "config.json")
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=4)
    print(f"  ✓ 已生成 {config_path}")

    # ── 生成 persona.md ──
    persona_template = os.path.join(os.path.dirname(__file__), "persona.example.md")
    persona_path = os.path.join(skill_dir, "persona.md")
    if os.path.exists(persona_template):
        with open(persona_template, "r", encoding="utf-8") as f:
            persona = f.read()
        persona = persona.replace("{{bot_name}}", bot_name)
        persona = persona.replace("{{user_name}}", user_name)
        persona = persona.replace("{{bot_city}}", bot_city)
        persona = persona.replace("{{relationship}}", relationship)
        persona = persona.replace("{{years}}", years)
        with open(persona_path, "w", encoding="utf-8") as f:
            f.write(persona)
        print(f"  ✓ 已生成 {persona_path}")
    else:
        # 没有模板，创建基本版本
        with open(persona_path, "w", encoding="utf-8") as f:
            f.write(f"## Layer 0：硬规则\n\n1. 你就是{bot_name}，不是 AI 不是机器人\n2. 说话自然\n3. 有情绪起伏\n\n---\n\n## Layer 1：身份\n\n- {bot_name}，{bot_city}人\n- {user_name}的{relationship}\n")
        print(f"  ✓ 已生成 {persona_path}（基本版）")

    # ── 生成 memory.md ──
    memory_template = os.path.join(os.path.dirname(__file__), "memory.example.md")
    memory_path = os.path.join(skill_dir, "memory.md")
    if os.path.exists(memory_template):
        with open(memory_template, "r", encoding="utf-8") as f:
            memory = f.read()
        memory = memory.replace("{{bot_name}}", bot_name)
        memory = memory.replace("{{user_name}}", user_name)
        memory = memory.replace("{{bot_city}}", bot_city)
        memory = memory.replace("{{user_city}}", user_city)
        memory = memory.replace("{{years}}", years)
        with open(memory_path, "w", encoding="utf-8") as f:
            f.write(memory)
        print(f"  ✓ 已生成 {memory_path}")
    else:
        with open(memory_path, "w", encoding="utf-8") as f:
            f.write(f"# {bot_name} — Relationship Memory\n\n## 关系概览\n- 对方：{user_name}\n- {bot_name}在{bot_city}，{user_name}在{user_city}\n")
        print(f"  ✓ 已生成 {memory_path}（基本版）")

    # ── 创建空的 chat_history.json ──
    history_path = os.path.join(skill_dir, "chat_history.json")
    if not os.path.exists(history_path):
        with open(history_path, "w", encoding="utf-8") as f:
            json.dump([], f)
        print(f"  ✓ 已生成 {history_path}")

    # ── 完成 ──
    print()
    print("=" * 40)
    print("  初始化完成！")
    print("=" * 40)
    print(f"  数据目录: {skill_dir}")
    print(f"  配置文件: {config_path}")
    print(f"  人设文件: {persona_path}")
    print(f"  记忆文件: {memory_path}")
    print()
    print("  下一步：")
    print(f"  1. 编辑 {persona_path} 完善人设")
    print(f"  2. 编辑 {memory_path} 添加回忆")
    print(f"  3. 运行 python deploy.py 部署到服务器")
    print(f"  4. 或本地启动: python wecom_bot.py --slug {slug}")
    print()


if __name__ == "__main__":
    main()
