"""배포 스크립트(scripts/deploy.py)의 게이트 판정 검증.

배포는 되돌리기 어렵다 — 잘못 올라간 산출물은 이미 받아 간 사용자에게서 회수되지 않는다.
그래서 deploy.py는 "gh 조회 결과만 보고 판정하는 순수 함수"와 "실제로 올리는 껍데기"로
나뉘어 있고, 여기서는 앞쪽만 잠근다(네트워크를 타지 않는다).

여기서 막으려는 회귀 둘:
    - 게이트가 **뚫리는** 것. 버전을 안 올렸거나 커밋이 어긋난 채로 통과하면 그 버전이 아닌
      코드가 그 버전으로 배포된다.
    - 게이트가 **정상 절차를 막는** 것. draft 릴리스에는 태그가 없어서, 태그 조회만으로
      대조하면 두 번째 플랫폼 배포가 매번 중단된다(실제로 그랬다).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).parents[1]


def _load_deploy():
    """scripts/deploy.py를 파일 경로로 로드한다(build.py와 같은 이유)."""
    spec = importlib.util.spec_from_file_location(
        "pdf_deploy_script", _REPO_ROOT / "scripts" / "deploy.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


deploy = _load_deploy()

_HEAD = "a" * 40
_OTHER = "b" * 40


def _plan(**overrides):
    """정상적인 두 번째 플랫폼 배포를 기본값으로 두고, 필요한 것만 바꾼다."""
    kwargs = {
        "tag": "v0.1.1",
        "prev_tag": "v0.1.0",
        "existing_assets": ["releases.osx.json"],  # 첫 플랫폼(macOS)이 올려 둔 상태
        "releases_json": "releases.win.json",
        "force": False,
        "tag_sha": _HEAD,
        "head_sha": _HEAD,
        "newest_tag": "v0.1.1",
    }
    kwargs.update(overrides)
    return deploy.plan_release(**kwargs)


# -- 릴리스가 없을 때 --------------------------------------------------------


def test_creates_release_when_absent() -> None:
    plan = _plan(existing_assets=None)
    assert plan.mode == "create"
    assert plan.error is None


# -- 두 번째 플랫폼(정상 경로) ------------------------------------------------


def test_second_platform_appends_assets() -> None:
    plan = _plan()
    assert plan.mode == "append"
    assert plan.error is None


# -- 게이트가 막아야 하는 것들 ------------------------------------------------


def test_blocks_when_channel_already_uploaded() -> None:
    # 내 채널 피드가 이미 있으면 이 플랫폼은 이미 배포된 것이다.
    plan = _plan(existing_assets=["releases.win.json"])
    assert plan.error is not None


def test_blocks_when_tag_commit_differs_from_head() -> None:
    # 태그가 가리키는 코드와 지금 빌드하는 코드가 다르면 그 버전이 아닌 것이 나간다.
    plan = _plan(tag_sha=_OTHER)
    assert plan.error is not None


def test_blocks_when_commit_unknown() -> None:
    # 모르는 채 올리는 것이 이 가드가 막으려는 사고다 — 통과시키지 않는다.
    assert _plan(tag_sha=None).error is not None
    assert _plan(head_sha=None).error is not None


def test_blocks_when_not_newest_release() -> None:
    # 낡은 체크아웃에서 돌리면 과거 태그에 에셋이 붙고 latest에는 영영 없는 상태가 된다.
    plan = _plan(newest_tag="v0.2.0")
    assert plan.error is not None


def test_force_overrides_every_gate() -> None:
    plan = _plan(tag_sha=_OTHER, newest_tag="v0.2.0", force=True)
    assert plan.error is None


# -- uv.lock 버전 동기화 게이트 ----------------------------------------------


def test_lockfile_version_reads_own_entry() -> None:
    lock = """
[[package]]
name = "other-package"
version = "9.9.9"

[[package]]
name = "pudufu-recorder"
version = "0.1.1"
"""
    assert deploy.lockfile_version(lock, "pudufu-recorder") == "0.1.1"


def test_lockfile_version_missing_entry_is_none() -> None:
    assert deploy.lockfile_version("[[package]]\nname = 'x'\n", "pudufu-recorder") is None


def test_blocks_when_lockfile_version_lags() -> None:
    """pyproject만 올리고 uv.lock을 안 맞추면 빌드 중 uv sync가 워킹 트리를 더럽힌다.

    그 시점은 워킹 트리 검사를 이미 통과한 뒤라 이번 배포는 그대로 나가고, 다음 배포가
    영문 모를 "커밋되지 않은 변경"으로 막힌다.
    """
    assert deploy.check_lockfile_version("0.1.0", "0.1.1", force=False) is not None
    assert deploy.check_lockfile_version("0.1.1", "0.1.1", force=False) is None
    assert deploy.check_lockfile_version(None, "0.1.1", force=False) is not None
    assert deploy.check_lockfile_version("0.1.0", "0.1.1", force=True) is None


# -- 워킹 트리 게이트 ---------------------------------------------------------


def test_blocks_dirty_worktree() -> None:
    # 빌드는 작업 트리의 파일을 담는데 태그는 커밋을 가리킨다. 어긋나면 어느 커밋에도 없는
    # 코드가 그 버전으로 배포된다.
    assert deploy.check_worktree_clean(" M pudufu/client.py\n", force=False) is not None
    assert deploy.check_worktree_clean("", force=False) is None
    assert deploy.check_worktree_clean(None, force=False) is not None
    assert deploy.check_worktree_clean(" M x.py\n", force=True) is None


def test_blocks_unpushed_head() -> None:
    # 릴리스 태그는 원격에 있는 커밋만 가리킬 수 있다.
    assert deploy.check_head_pushed(_HEAD, remote_has_head=False, force=False) is not None
    assert deploy.check_head_pushed(_HEAD, remote_has_head=True, force=False) is None
    assert deploy.check_head_pushed(None, remote_has_head=True, force=False) is not None


# -- draft 릴리스의 태그 부재 -------------------------------------------------


def test_tag_commit_falls_back_to_release_target(monkeypatch) -> None:
    """draft 릴리스에는 태그가 없다 — GitHub은 공개 시점에 태그를 만든다.

    이 스크립트가 안내하는 절차는 첫 플랫폼이 draft를 남기는 것이라, 태그 조회만으로는
    두 번째 플랫폼에서 항상 실패한다. 릴리스가 기록해 둔 targetCommitish로 대조해야 한다.
    """
    calls: list[list[str]] = []

    class _Proc:
        def __init__(self, returncode: int, stdout: str) -> None:
            self.returncode = returncode
            self.stdout = stdout

    def fake_run(command, **kwargs):
        calls.append(command)
        # 태그 조회는 draft라 실패한다(commits/<tag> → 422).
        if command[:2] == ["gh", "api"] and command[2].endswith("/v0.1.1"):
            return _Proc(1, "")
        # 릴리스는 대상 커밋을 들고 있다.
        if command[:3] == ["gh", "release", "view"]:
            return _Proc(0, f"{_HEAD}\n")
        # 그 값을 다시 커밋으로 푼다.
        return _Proc(0, f"{_HEAD}\n")

    monkeypatch.setattr(deploy.subprocess, "run", fake_run)

    assert deploy.tag_commit("v0.1.1") == _HEAD
    assert any(cmd[:3] == ["gh", "release", "view"] for cmd in calls)


def test_tag_commit_is_none_when_release_absent(monkeypatch) -> None:
    class _Proc:
        returncode = 1
        stdout = ""

    monkeypatch.setattr(deploy.subprocess, "run", lambda *a, **k: _Proc())

    assert deploy.tag_commit("v9.9.9") is None
