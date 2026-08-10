"""빌드 스크립트(scripts/build.py)의 인자·경로 규칙과 유출 가드 검증.

``flet build``와 ``vpk``는 이 개발 머신에서 전부 돌려 볼 수 없다(Windows 빌드는 Windows
에서만 되고, 설치기 동작 확인은 실기 몫이다). 그래서 build.py는 "무엇을 실행할지 정하는
순수 함수"와 "그것을 실행하는 얇은 껍데기"로 나뉘어 있고, 여기서는 앞쪽만 잠근다.

여기서 막으려는 회귀는 조용한 것들이다 — 채널이 어긋나 업데이트 피드를 못 찾거나,
``--template``이 빠져 Velopack 훅 패치 없는 러너가 배포되거나, **개발 환경의 .env가 배포
산출물에 실려 나가는 것**. 마지막 항목은 v0.1.0에서 실제로 일어났고 공개 릴리스에 프드프
계정 정보가 평문으로 나갔다. 그래서 verify_no_secrets는 여기서 가장 촘촘히 잠근다.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parents[1]


def _load_build():
    """scripts/build.py를 파일 경로로 로드한다(최상위 이름 ``build``와의 충돌 회피)."""
    spec = importlib.util.spec_from_file_location(
        "pdf_build_script", _REPO_ROOT / "scripts" / "build.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


build = _load_build()


# -- 타깃·채널 매핑 -----------------------------------------------------------


def test_target_mapping_from_platform() -> None:
    assert build.target_for("Windows") == "windows"
    assert build.target_for("Darwin") == "macos"


def test_target_mapping_rejects_unsupported_os() -> None:
    # Linux가 조용히 windows로 떨어지면 엉뚱한 산출물을 만든다.
    with pytest.raises(SystemExit):
        build.target_for("Linux")


def test_channel_per_target() -> None:
    # 채널이 어긋나면 앱이 자기 업데이트 피드를 찾지 못한다.
    assert build.channel_for("windows") == "win"
    assert build.channel_for("macos") == "osx"


def test_releases_json_name_matches_channel() -> None:
    # GithubSource가 이름 완전 일치로만 찾으므로 규칙이 갈리면 업데이트가 죽는다.
    assert build.releases_json_name("windows") == "releases.win.json"
    assert build.releases_json_name("macos") == "releases.osx.json"


# -- 산출물 이름 규약 ---------------------------------------------------------


def test_setup_glob_per_target() -> None:
    assert build.setup_glob("windows") == "*-Setup.exe"
    assert build.setup_glob("macos") == "*-Setup.pkg"


def test_full_nupkg_glob_channel_suffix_rule() -> None:
    """Velopack이 채널 접미사를 빼는 유일한 조합은 Windows 타깃 + win 채널이다.

    "그 OS의 기본 채널이면 뺀다"가 아니다 — macOS/osx에는 면제가 없다. 이 규칙이 어긋나면
    deploy.py가 올릴 파일을 찾지 못한다.
    """
    assert build.full_nupkg_glob("windows", "0.1.1") == "*-0.1.1-full.nupkg"
    assert build.full_nupkg_glob("macos", "0.1.1") == "*-0.1.1-osx-full.nupkg"


# -- vpk pack 인자 ------------------------------------------------------------


def test_macos_pack_args_force_adhoc_signing(tmp_path: Path) -> None:
    """서명 환경변수가 없어도 macOS는 ad-hoc 식별자를 넘겨야 한다.

    vpk가 UpdateMac·sq.version을 Contents/MacOS에 끼워 넣으므로 우리가 먼저 재서명해도
    봉인이 다시 깨진다. 이 인자가 빠지면 설치본이 sealed resource 오류 상태가 된다.
    """
    bundle = tmp_path / "pudufu-recorder.app"
    (bundle / "Contents" / "MacOS").mkdir(parents=True)
    (bundle / "Contents" / "MacOS" / "pudufu-recorder").write_bytes(b"")

    args = build.vpk_pack_args("macos", bundle_dir=bundle, version="0.1.1")

    assert "--signAppIdentity" in args
    assert args[args.index("--signAppIdentity") + 1] == "-"
    # 설치기(.pkg)만 배포하므로 Portable.zip은 만들지 않는다.
    assert "--noPortable" in args


def test_pack_args_carry_channel(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    args = build.vpk_pack_args("windows", bundle_dir=bundle, version="0.1.1")
    assert args[args.index("--channel") + 1] == "win"


# -- 유출 가드 ----------------------------------------------------------------
# v0.1.0 사고의 회귀 테스트. flet build가 프로젝트 폴더를 통째로 복사하므로 exclude 설정이
# 비거나 매칭이 어긋나면 개발 환경이 그대로 배포된다. 그때 막지 못한 이유는 "실수했다"가
# 아니라 검사하는 곳이 아예 없었다는 것이다.


def test_verify_no_secrets_passes_clean_bundle(tmp_path: Path) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "main.pyc").write_bytes(b"\x00")
    (tmp_path / "app" / ".env.example").write_text("PUDUFU_ID=your@email.com")

    # 예외가 나지 않아야 한다. .env.example은 플레이스홀더이므로 통과시킨다.
    build.verify_no_secrets(tmp_path)


@pytest.mark.parametrize("name", [".env", ".git", ".venv"])
def test_verify_no_secrets_blocks_forbidden_entries(tmp_path: Path, name: str) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / name).write_text("secret")

    with pytest.raises(SystemExit):
        build.verify_no_secrets(tmp_path)


def test_verify_no_secrets_blocks_nested_env(tmp_path: Path) -> None:
    """중첩된 산출물 안의 .env도 잡아야 한다.

    v0.1.0에는 이전 빌드의 dist/가 통째로 복사되어 앱 안에 앱이 중첩됐고, .env가 두 벌
    들어갔다. 최상위만 보는 검사는 그 두 번째를 놓친다.
    """
    deep = tmp_path / "app" / "dist" / "inner.app" / "Resources" / "app"
    deep.mkdir(parents=True)
    (deep / ".env").write_text("PUDUFU_PW=secret")

    with pytest.raises(SystemExit):
        build.verify_no_secrets(tmp_path)


def test_verify_no_secrets_blocks_directory_form(tmp_path: Path) -> None:
    # .git은 파일이 아니라 디렉터리로 들어온다.
    (tmp_path / "app" / ".git" / "objects").mkdir(parents=True)
    (tmp_path / "app" / ".git" / "config").write_text("x")

    with pytest.raises(SystemExit):
        build.verify_no_secrets(tmp_path)
