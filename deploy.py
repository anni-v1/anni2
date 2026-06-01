#!/usr/bin/env python3
"""
AI Chat Bot — 一键部署脚本
支持 Windows / Linux / macOS
上传文件到服务器、重启她、可选导入聊天记录

Usage:
    python deploy.py                              # 使用默认目录 ./exes
    python deploy.py --slug mybot                 # 指定她
    python deploy.py --slug mybot --import-history chat.json  # 导入聊天记录
    python deploy.py --slug mybot --download-history          # 下载服务器上的聊天记录
"""

import json
import os
import sys
import time
import argparse

try:
    import paramiko
except ImportError:
    print("缺少依赖，请执行：pip install paramiko", file=sys.stderr)
    sys.exit(1)


def load_config(skill_dir: str) -> dict:
    """加载配置文件"""
    config_path = os.path.join(skill_dir, "config.json")
    if not os.path.exists(config_path):
        print(f"[错误] 配置文件不存在: {config_path}", file=sys.stderr)
        print(f"请先运行 python init.py 创建配置", file=sys.stderr)
        sys.exit(1)
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_deploy_config() -> dict:
    """加载部署配置（服务器地址等）"""
    deploy_path = os.path.join(os.path.dirname(__file__), "deploy_config.json")
    if not os.path.exists(deploy_path):
        print("[提示] 未找到 deploy_config.json，请先配置服务器信息")
        print()
        host = input("  服务器 IP: ").strip()
        username = input("  用户名 [root]: ").strip() or "root"
        password = input("  密码: ").strip()
        remote_dir = input("  服务器目录 [/root/ex-skill]: ").strip() or "/root/ex-skill"
        remote_data = input("  服务器数据目录 [/root/exes]: ").strip() or "/root/exes"

        deploy_config = {
            "host": host,
            "username": username,
            "password": password,
            "remote_dir": remote_dir,
            "remote_data": remote_data,
        }
        with open(deploy_path, "w", encoding="utf-8") as f:
            json.dump(deploy_config, f, ensure_ascii=False, indent=4)
        print(f"  ✓ 已保存部署配置到 {deploy_path}")
        print()
        return deploy_config

    with open(deploy_path, "r", encoding="utf-8") as f:
        return json.load(f)


def connect_ssh(host: str, username: str, password: str) -> paramiko.SSHClient:
    """连接服务器"""
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    print(f"[连接] {username}@{host} ...")
    ssh.connect(host, username=username, password=password)
    print(f"[连接] 成功")
    return ssh


def upload_files(ssh: paramiko.SSHClient, skill_dir: str, slug: str,
                 remote_dir: str, remote_data: str):
    """上传文件到服务器"""
    sftp = ssh.open_sftp()

    # 确保远程目录存在
    remote_skill_dir = f"{remote_data}/{slug}"
    for d in [remote_dir, remote_data, remote_skill_dir]:
        try:
            sftp.stat(d)
        except:
            ssh.exec_command(f"mkdir -p {d}")
            time.sleep(0.5)

    # 上传主程序
    local_bot = os.path.join(os.path.dirname(__file__), "wecom_bot.py")
    remote_bot = f"{remote_dir}/wecom_bot.py"
    print(f"[上传] wecom_bot.py -> {remote_bot}")
    sftp.put(local_bot, remote_bot)

    # 上传配置文件
    files_to_upload = ["config.json", "persona.md", "memory.md"]
    for fname in files_to_upload:
        local_path = os.path.join(skill_dir, fname)
        remote_path = f"{remote_skill_dir}/{fname}"
        if os.path.exists(local_path):
            print(f"[上传] {fname} -> {remote_path}")
            sftp.put(local_path, remote_path)
        else:
            print(f"[跳过] {fname} 不存在")

    sftp.close()
    print("[上传] 完成")


def upload_chat_history(ssh: paramiko.SSHClient, skill_dir: str, slug: str,
                        remote_data: str, history_file: str):
    """导入聊天记录到服务器"""
    if not os.path.exists(history_file):
        print(f"[错误] 聊天记录文件不存在: {history_file}")
        return False

    sftp = ssh.open_sftp()
    remote_path = f"{remote_data}/{slug}/chat_history.json"

    # 如果服务器已有记录，先合并
    try:
        tmp = os.path.join(os.path.dirname(__file__), "_remote_history.json")
        sftp.get(remote_path, tmp)
        with open(tmp, "r", encoding="utf-8") as f:
            remote_data_list = json.load(f)
        with open(history_file, "r", encoding="utf-8") as f:
            local_data_list = json.load(f)
        # 合并，去重（按 time + sender + content）
        seen = set()
        merged = []
        for r in remote_data_list + local_data_list:
            key = (r.get("time", ""), r.get("sender", ""), r.get("content", ""))
            if key not in seen:
                seen.add(key)
                merged.append(r)
        merged.sort(key=lambda x: x.get("time", ""))
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(merged, f, ensure_ascii=False, indent=2)
        sftp.put(tmp, remote_path)
        os.unlink(tmp)
        print(f"[导入] 合并后共 {len(merged)} 条聊天记录")
    except:
        # 服务器没有记录，直接上传
        sftp.put(history_file, remote_path)
        with open(history_file, "r", encoding="utf-8") as f:
            count = len(json.load(f))
        print(f"[导入] 上传 {count} 条聊天记录")

    sftp.close()
    return True


def download_chat_history(ssh: paramiko.SSHClient, skill_dir: str, slug: str,
                          remote_data: str):
    """从服务器下载聊天记录"""
    sftp = ssh.open_sftp()
    remote_path = f"{remote_data}/{slug}/chat_history.json"
    local_path = os.path.join(skill_dir, "chat_history.json")

    try:
        sftp.get(remote_path, local_path)
        with open(local_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        print(f"[下载] 已下载 {len(data)} 条聊天记录到 {local_path}")
    except Exception as e:
        print(f"[下载] 失败: {e}")

    sftp.close()


def restart_bot(ssh: paramiko.SSHClient, slug: str, remote_dir: str,
                remote_data: str, port: int = 4999):
    """重启"""
    # 停止旧进程
    print("[重启] 停止旧进程...")
    ssh.exec_command(f"pkill -f 'wecom_bot.py --slug {slug}'")
    time.sleep(1)

    # 启动新进程
    cmd = (
        f"cd {remote_dir} && "
        f"nohup python3 -u wecom_bot.py --slug {slug} "
        f"--base-dir {remote_data} --port {port} "
        f"> /tmp/wecom_{slug}.log 2>&1 &"
    )
    print(f"[重启] 启动新进程...")
    ssh.exec_command(cmd)
    time.sleep(3)

    # 检查进程
    stdin, stdout, stderr = ssh.exec_command(
        f"ps aux | grep 'wecom_bot.py --slug {slug}' | grep -v grep",
        timeout=5
    )
    output = stdout.read().decode().strip()
    if output:
        print(f"[重启] 成功")
        print(f"  {output}")
    else:
        print(f"[重启] 可能失败，请检查日志: /tmp/wecom_{slug}.log")

    ssh.close()


def main():
    parser = argparse.ArgumentParser(description="AI Chat Bot 部署工具")
    parser.add_argument("--slug", default="", help="目录名")
    parser.add_argument("--base-dir", default="./exes", help="本地数据目录")
    parser.add_argument("--import-history", default="", help="导入聊天记录文件（JSON）")
    parser.add_argument("--download-history", action="store_true", help="从服务器下载聊天记录")
    parser.add_argument("--upload-only", action="store_true", help="只上传文件，不重启")
    args = parser.parse_args()

    # 确定 slug
    slug = args.slug
    if not slug:
        # 自动检测 exes 下的目录
        if os.path.exists(args.base_dir):
            dirs = [d for d in os.listdir(args.base_dir)
                    if os.path.isdir(os.path.join(args.base_dir, d))]
            if len(dirs) == 1:
                slug = dirs[0]
                print(f"[自动检测] slug: {slug}")
            elif len(dirs) > 1:
                print(f"发现多个目录: {', '.join(dirs)}")
                slug = input("请输入要部署的 slug: ").strip()
            else:
                print(f"[错误] {args.base_dir} 下没有目录")
                print(f"请先运行 python init.py 创建")
                sys.exit(1)
        else:
            print(f"[错误] 数据目录不存在: {args.base_dir}")
            sys.exit(1)

    skill_dir = os.path.join(args.base_dir, slug)

    # 加载配置
    config = load_config(skill_dir)
    deploy_config = load_deploy_config()

    print()
    print("=" * 40)
    print(f"  部署 {config.get('bot_name', slug)}")
    print("=" * 40)
    print(f"  服务器: {deploy_config['host']}")
    print(f"  目录: {deploy_config['remote_data']}/{slug}")
    print()

    # 连接
    ssh = connect_ssh(
        deploy_config["host"],
        deploy_config["username"],
        deploy_config["password"],
    )

    # 上传文件
    upload_files(
        ssh, skill_dir, slug,
        deploy_config["remote_dir"],
        deploy_config["remote_data"],
    )

    # 导入聊天记录
    if args.import_history:
        upload_chat_history(
            ssh, skill_dir, slug,
            deploy_config["remote_data"],
            args.import_history,
        )

    # 下载聊天记录
    if args.download_history:
        # 需要重新连接，因为之前的连接可能还在用
        ssh2 = connect_ssh(
            deploy_config["host"],
            deploy_config["username"],
            deploy_config["password"],
        )
        download_chat_history(
            ssh2, skill_dir, slug,
            deploy_config["remote_data"],
        )
        ssh2.close()

    # 重启
    if not args.upload_only:
        restart_bot(
            ssh, slug,
            deploy_config["remote_dir"],
            deploy_config["remote_data"],
            config.get("port", 4999),
        )
    else:
        ssh.close()

    print()
    print("部署完成！")


if __name__ == "__main__":
    main()
