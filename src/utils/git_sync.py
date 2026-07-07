# -*- coding: utf-8 -*-
"""
git_sync.py — 自动提交并推送到远程仓库（双电脑无缝同步）
用户授权（2026-07-07）：本项目的所有改动，在每次 run.py 命令执行后自动
add + commit + push 到 GitHub，无需手动确认或告知。

设计原则：
  - 绝不因同步失败影响主流程：任何git异常都被捕获，返回状态供调用方打印提示；
  - push 前先 pull --rebase --autostash，降低双机异步操作产生分叉冲突的概率；
  - 无变更（git status 干净）时直接跳过，不产生空提交；
  - 仅同步 git 跟踪范围内的文件（.gitignore 排除的 data/ 缓存等由 OneDrive
    实时文件同步负责，两者互补，互不重复）。
"""
from __future__ import annotations

import subprocess
from datetime import datetime

from src.utils.file_utils import PROJECT_ROOT


def _run(args: list[str], timeout: int = 60) -> tuple[int, str]:
    """执行 git 子命令，返回 (returncode, 合并后的stdout+stderr)。永不抛异常。"""
    try:
        r = subprocess.run(["git", *args], cwd=PROJECT_ROOT, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=timeout)
        return r.returncode, (r.stdout + r.stderr).strip()
    except Exception as exc:  # noqa: BLE001 —— 同步是辅助功能，任何异常都不能向上传播
        return -1, f"{type(exc).__name__}: {exc}"


def auto_sync(message: str) -> dict:
    """
    自动同步入口：add -A → commit（无变更则跳过）→ pull --rebase → push。
    返回 {"committed": bool, "pushed": bool, "note": str}。
    """
    code, out = _run(["rev-parse", "--is-inside-work-tree"])
    if code != 0:
        return {"committed": False, "pushed": False, "note": "当前目录不是git仓库，跳过同步"}

    code, out = _run(["status", "--porcelain"])
    if code != 0:
        return {"committed": False, "pushed": False, "note": f"git status 失败：{out[:150]}"}
    if not out.strip():
        return {"committed": False, "pushed": False, "note": "无变更，跳过同步"}

    _run(["add", "-A"])
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    commit_msg = f"{message}（自动同步 {ts}）\n\nCo-Authored-By: Claude <noreply@anthropic.com>"
    code, out = _run(["commit", "-m", commit_msg])
    if code != 0:
        return {"committed": False, "pushed": False, "note": f"提交失败：{out[:200]}"}

    # 拉取远程避免双机分叉（rebase保持线性历史；本地未提交的其他变更已在上一步提交，autostash兜底）
    _run(["pull", "--rebase", "--autostash"], timeout=30)
    code, out = _run(["push"], timeout=30)
    if code != 0:
        return {"committed": True, "pushed": False,
                "note": f"已提交但推送失败，稍后可手动 git push（{out[:200]}）"}
    return {"committed": True, "pushed": True, "note": "已提交并推送到 GitHub"}
