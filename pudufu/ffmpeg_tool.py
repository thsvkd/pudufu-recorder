"""ffmpeg/ffprobe 탐색 및 자동 설치.

PATH에 없으면 앱 데이터 폴더에 다운로드해서 사용한다.
자동 다운로드는 현재 macOS(evermeet.cx 빌드)만 지원한다.
"""

from __future__ import annotations

import io
import os
import shutil
import stat
import sys
import zipfile
from pathlib import Path
from typing import Callable

import requests

FFMPEG_ZIP_URL = "https://evermeet.cx/ffmpeg/getrelease/zip"
FFPROBE_ZIP_URL = "https://evermeet.cx/ffprobe/getrelease/zip"


class FFmpegNotFound(Exception):
    """ffmpeg/ffprobe를 찾거나 설치할 수 없을 때 발생한다."""


def _app_data_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "PudufuRecorder" / "bin"
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA", str(Path.home()))
        return Path(base) / "PudufuRecorder" / "bin"
    base = os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))
    return Path(base) / "PudufuRecorder" / "bin"


def find_ffmpeg() -> tuple[Path, Path] | None:
    """(ffmpeg, ffprobe) 경로를 찾는다. 없으면 None."""
    app_dir = _app_data_dir()
    local_ffmpeg = app_dir / "ffmpeg"
    local_ffprobe = app_dir / "ffprobe"
    if local_ffmpeg.exists() and local_ffprobe.exists():
        return local_ffmpeg, local_ffprobe

    path_ffmpeg = shutil.which("ffmpeg")
    path_ffprobe = shutil.which("ffprobe")
    if path_ffmpeg and path_ffprobe:
        return Path(path_ffmpeg), Path(path_ffprobe)

    return None


def install_ffmpeg(
    on_progress: Callable[[float], None] | None = None,
) -> tuple[Path, Path]:
    """ffmpeg/ffprobe를 앱 데이터 폴더에 다운로드한다. (macOS 전용)"""
    if sys.platform != "darwin":
        raise FFmpegNotFound(
            "ffmpeg 자동 설치는 macOS에서만 지원됩니다. "
            "패키지 매니저(brew 등)로 ffmpeg를 직접 설치한 뒤 PATH에 추가해주세요."
        )

    app_dir = _app_data_dir()
    app_dir.mkdir(parents=True, exist_ok=True)

    def scaled(start: float, end: float) -> Callable[[float], None] | None:
        if on_progress is None:
            return None
        return lambda ratio: on_progress(start + (end - start) * ratio)

    ffmpeg_path = _download_binary(FFMPEG_ZIP_URL, app_dir, "ffmpeg", scaled(0.0, 0.5))
    ffprobe_path = _download_binary(FFPROBE_ZIP_URL, app_dir, "ffprobe", scaled(0.5, 1.0))

    if on_progress is not None:
        on_progress(1.0)

    return ffmpeg_path, ffprobe_path


def _download_binary(
    url: str,
    dest_dir: Path,
    binary_name: str,
    on_progress: Callable[[float], None] | None,
) -> Path:
    with requests.get(url, stream=True, timeout=60) as resp:
        resp.raise_for_status()
        total = int(resp.headers.get("Content-Length", 0))
        buf = io.BytesIO()
        downloaded = 0
        for chunk in resp.iter_content(chunk_size=65536):
            buf.write(chunk)
            downloaded += len(chunk)
            if on_progress is not None and total:
                on_progress(min(1.0, downloaded / total))

    with zipfile.ZipFile(buf) as zf:
        zf.extractall(dest_dir)

    binary_path = dest_dir / binary_name
    if not binary_path.exists():
        raise FFmpegNotFound(f"{binary_name} 다운로드/압축 해제에 실패했습니다.")

    mode = binary_path.stat().st_mode
    binary_path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    return binary_path
