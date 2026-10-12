"""准备项目独立环境及本地 OCR 模型，然后打开桌面界面。"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import struct
import subprocess
import sys
import venv


ROOT = Path(__file__).resolve().parent
ENV_PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
READY_FILE = ROOT / ".venv" / ".tod-ready"


def run(command):
    subprocess.run(command, cwd=ROOT, check=True)


def main():
    parser = argparse.ArgumentParser(description="首次自动准备环境与模型，之后直接启动 TOD。")
    parser.parse_args()
    if os.name != "nt" or sys.version_info[:2] != (3, 12) or struct.calcsize("P") != 8:
        print("请使用 Windows 和 64 位 Python 3.12，并双击 启动TOD.bat。")
        return 1
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    requirements = ROOT / "requirements.txt"
    digest = hashlib.sha256(requirements.read_bytes()).hexdigest()
    if not ENV_PYTHON.exists():
        print("[1/3] 创建项目独立环境……", flush=True)
        venv.EnvBuilder(with_pip=True).create(ROOT / ".venv")
    # 保留已有环境；版本不合适时提示用户，不自行删除或重建。
    run([str(ENV_PYTHON), "-c", "import sys,struct; sys.exit(0 if sys.version_info[:2] == (3,12) and struct.calcsize('P') == 8 else 1)"])
    ready = READY_FILE.read_text(encoding="utf-8").strip() if READY_FILE.exists() else ""
    if ready != digest:
        print("[2/3] 安装依赖，首次准备需要联网，请等待……", flush=True)
        run([str(ENV_PYTHON), "-m", "pip", "install", "-r", str(requirements)])
        print("[3/3] 准备本地 OCR 模型，不读取业务材料……", flush=True)
        run([str(ENV_PYTHON), "-c", "from engines.paddle_engine import PaddleEngine; PaddleEngine()._lazy_init()"])
        READY_FILE.write_text(digest, encoding="utf-8")
    print("正在打开 TOD……", flush=True)
    run([str(ENV_PYTHON), str(ROOT / "tod_gui.py")])
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, subprocess.CalledProcessError) as error:
        print(f"启动未完成：{error}")
        print("请检查网络、Python 版本和目录写入权限，然后重新启动；已有文件会保留。")
        raise SystemExit(1)
