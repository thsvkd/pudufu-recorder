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
]
