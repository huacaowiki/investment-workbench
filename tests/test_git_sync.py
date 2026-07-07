# -*- coding: utf-8 -*-
"""
git_sync 单元测试：在隔离的临时git仓库（本地"远程"，非真实GitHub）中验证
add+commit+push 流程与"无变更跳过""非git目录"等边界情况，不触碰真实项目仓库。
"""
import subprocess

import pytest

from src.utils import git_sync


def _git(args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                          text=True, encoding="utf-8", timeout=30)


@pytest.fixture
def sandbox_repo(tmp_path, monkeypatch):
    """
    造一个 bare远程 + 一个clone的本地仓库，本地仓库路径注入为 PROJECT_ROOT，
    使 git_sync.auto_sync 在这个隔离沙箱里操作，不影响真实项目仓库。
    """
    remote = tmp_path / "remote.git"
    _git(["init", "--bare", str(remote)], cwd=tmp_path)

    local = tmp_path / "local"
    local.mkdir()
    _git(["init"], cwd=local)
    _git(["config", "user.email", "test@example.com"], cwd=local)
    _git(["config", "user.name", "Test"], cwd=local)
    (local / "a.txt").write_text("v1", encoding="utf-8")
    _git(["add", "-A"], cwd=local)
    _git(["commit", "-m", "init"], cwd=local)
    _git(["branch", "-M", "main"], cwd=local)
    _git(["remote", "add", "origin", str(remote)], cwd=local)
    _git(["push", "-u", "origin", "main"], cwd=local)

    monkeypatch.setattr(git_sync, "PROJECT_ROOT", local)
    return local, remote


class TestAutoSync:
    def test_no_changes_skips(self, sandbox_repo):
        local, _ = sandbox_repo
        result = git_sync.auto_sync("test")
        assert result == {"committed": False, "pushed": False, "note": "无变更，跳过同步"}

    def test_commits_and_pushes_new_file(self, sandbox_repo):
        local, remote = sandbox_repo
        (local / "report.md").write_text("新报告", encoding="utf-8")
        result = git_sync.auto_sync("生成市场日报")
        assert result["committed"] is True
        assert result["pushed"] is True
        # 验证远程确实收到了提交
        log = _git(["log", "--oneline", "main"], cwd=remote)
        assert "生成市场日报" in log.stdout

    def test_commits_modified_file(self, sandbox_repo):
        local, remote = sandbox_repo
        (local / "a.txt").write_text("v2-modified", encoding="utf-8")
        result = git_sync.auto_sync("更新配置")
        assert result["committed"] and result["pushed"]
        show = _git(["show", "main:a.txt"], cwd=remote)
        assert show.stdout.strip() == "v2-modified"

    def test_not_a_git_repo(self, tmp_path, monkeypatch):
        empty = tmp_path / "not_git"
        empty.mkdir()
        monkeypatch.setattr(git_sync, "PROJECT_ROOT", empty)
        result = git_sync.auto_sync("test")
        assert result["committed"] is False
        assert "不是git仓库" in result["note"]

    def test_push_failure_still_reports_committed(self, sandbox_repo, monkeypatch):
        """远程不可达时：本地提交仍完成，pushed=False，note里说明可稍后手动推送。"""
        local, remote = sandbox_repo
        _git(["remote", "set-url", "origin", "/nonexistent/path.git"], cwd=local)
        (local / "x.txt").write_text("data", encoding="utf-8")
        result = git_sync.auto_sync("test")
        assert result["committed"] is True
        assert result["pushed"] is False
        assert "手动" in result["note"] or "推送失败" in result["note"]
