"""코어 패키지(pudufu)를 안전하게 불러오는 브릿지.

코어 모듈(client.py / ffmpeg_tool.py / recorder.py)이 아직 없어도
GUI가 임포트 에러 없이 실행되도록, 없는 모듈은 None으로 대체한다.
models.py는 이미 존재하므로 항상 그대로 가져온다.
"""

from __future__ import annotations

from pudufu.models import Course, Lesson, Progress, Summary

try:
    from pudufu.client import LoginError, PuduFuClient

    HAS_CLIENT = True
except ImportError:
    HAS_CLIENT = False

    class LoginError(Exception):
        """코어 미구현 시 사용하는 자리표시자."""

    PuduFuClient = None  # type: ignore[assignment,misc]

try:
    from pudufu.ffmpeg_tool import FFmpegNotFound, find_ffmpeg, install_ffmpeg

    HAS_FFMPEG_TOOL = True
except ImportError:
    HAS_FFMPEG_TOOL = False

    class FFmpegNotFound(Exception):
        """코어 미구현 시 사용하는 자리표시자."""

    find_ffmpeg = None  # type: ignore[assignment]
    install_ffmpeg = None  # type: ignore[assignment]

try:
    from pudufu.recorder import Recorder

    HAS_RECORDER = True
except ImportError:
    HAS_RECORDER = False
    Recorder = None  # type: ignore[assignment,misc]

try:
    from pudufu.velopack_update import REPO_URL
    from pudufu.velopack_update import apply_and_restart
    from pudufu.velopack_update import check as check_update
    from pudufu.velopack_update import current_version
    from pudufu.velopack_update import download as download_update
    from pudufu.velopack_update import is_installed
    from pudufu.velopack_update import run_startup_maintenance
    from pudufu.velopack_update import target_version

    HAS_VELOPACK = True
except ImportError:
    HAS_VELOPACK = False
    REPO_URL = None  # type: ignore[assignment]

    def run_startup_maintenance() -> None:
        """코어 미구현 시 사용하는 자리표시자. 아무 것도 하지 않는다."""

    def is_installed() -> bool:
        return False

    def current_version() -> str | None:
        return None

    def check_update():  # type: ignore[no-untyped-def]
        return None

    def target_version(info) -> str:  # type: ignore[no-untyped-def]
        return ""

    def download_update(info, progress_cb=None) -> None:  # type: ignore[no-untyped-def]
        """코어 미구현 시 사용하는 자리표시자."""

    def apply_and_restart(info) -> None:  # type: ignore[no-untyped-def]
        """코어 미구현 시 사용하는 자리표시자."""


def get_package_version() -> str:
    """비설치 실행 등 current_version()이 없을 때 표시할 패키지 버전 폴백."""
    import pudufu

    return getattr(pudufu, "__version__", "dev")


__all__ = [
    "Course",
    "Lesson",
    "Progress",
    "Summary",
    "PuduFuClient",
    "LoginError",
    "find_ffmpeg",
    "install_ffmpeg",
    "FFmpegNotFound",
    "Recorder",
    "HAS_CLIENT",
    "HAS_FFMPEG_TOOL",
    "HAS_RECORDER",
    "REPO_URL",
    "run_startup_maintenance",
    "is_installed",
    "current_version",
    "check_update",
    "target_version",
    "download_update",
    "apply_and_restart",
    "get_package_version",
    "HAS_VELOPACK",
]
