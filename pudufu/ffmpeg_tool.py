"""ffmpeg/ffprobe 탐색 및 자동 설치.

PATH에 없으면 앱 데이터 폴더에 다운로드해서 사용한다.
자동 다운로드는 현재 macOS(evermeet.cx 빌드)만 지원한다.
"""

from __future__ import annotations

import io
import os
import shutil
import stat
import subprocess
import sys
import zipfile
from collections.abc import Callable
from pathlib import Path

import requests

# evermeet.cx의 배포 경로는 ``/ffmpeg/getrelease/<도구>/zip`` 형태다. 도구 이름을 맨 앞에 두는
# ``/ffprobe/getrelease/zip``은 404가 아니라 **ffmpeg zip으로 302 리다이렉트**되므로, 받아 놓고
# 압축을 풀면 안에 ffprobe가 없어 "압축 해제 실패"로 보인다. 경로를 바꿀 때 주의할 것.
FFMPEG_ZIP_URL = "https://evermeet.cx/ffmpeg/getrelease/ffmpeg/zip"
FFPROBE_ZIP_URL = "https://evermeet.cx/ffmpeg/getrelease/ffprobe/zip"


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

    binary_path = _extract_binary(buf, dest_dir, binary_name)

    mode = binary_path.stat().st_mode
    binary_path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    _verify_runnable(binary_path)
    return binary_path


def _extract_binary(archive: io.BytesIO, dest_dir: Path, binary_name: str) -> Path:
    """압축 안에서 ``binary_name`` 파일 하나만 꺼내 ``dest_dir``에 놓는다.

    통째로 풀지 않고 이름으로 찾는 이유는, 배포처가 엉뚱한 압축 파일을 돌려줬을 때
    "압축 해제 실패"라는 뭉뚱그린 메시지 대신 **안에 뭐가 들어 있었는지**를 남기기
    위해서다. 실제로 ffprobe URL이 ffmpeg zip으로 리다이렉트되던 버그가 그렇게 숨었다.
    """
    with zipfile.ZipFile(archive) as zf:
        member = next(
            (
                info
                for info in zf.infolist()
                if not info.is_dir() and Path(info.filename).name == binary_name
            ),
            None,
        )
        if member is None:
            contents = ", ".join(zf.namelist()[:10]) or "(빈 압축 파일)"
            raise FFmpegNotFound(
                f"내려받은 압축 파일에 {binary_name}이(가) 없습니다. 들어 있던 파일: {contents}"
            )
        # 중간에 끊겨 반쪽짜리 파일이 남으면 다음 실행 때 find_ffmpeg가 그걸 정상으로 보고
        # 집어 든다. 임시 이름으로 다 받은 뒤 한 번에 갈아 끼운다.
        binary_path = dest_dir / binary_name
        tmp_path = dest_dir / f".{binary_name}.part"
        with zf.open(member) as src, tmp_path.open("wb") as out:
            shutil.copyfileobj(src, out)
        tmp_path.replace(binary_path)

    return binary_path


def _verify_runnable(binary_path: Path) -> None:
    """받은 실행 파일이 이 기기에서 실제로 실행되는지 확인한다.

    evermeet.cx 빌드는 x86_64 전용이라 Apple Silicon에서는 Rosetta 2가 있어야 돈다.
    없으면 exec 단계에서 "Bad CPU type" (OSError)로 죽는데, 그 시점이 다운로드가 아니라
    한참 뒤 변환 중이라 원인을 짚기 어렵다. 여기서 미리 걸러 낸다.
    """
    try:
        result = subprocess.run(
            [str(binary_path), "-version"],
            capture_output=True,
            timeout=30,
            check=False,
        )
    except OSError as exc:
        raise FFmpegNotFound(
            f"{binary_path.name}을(를) 실행할 수 없습니다 ({exc}). Apple Silicon에서는 Rosetta 2가 "
            "필요합니다. 터미널에서 `softwareupdate --install-rosetta`를 실행한 뒤 다시 시도하세요."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise FFmpegNotFound(f"{binary_path.name} 실행 확인이 시간 초과됐습니다.") from exc
    if result.returncode != 0:
        detail = (result.stderr or b"").decode("utf-8", "replace").strip()
        raise FFmpegNotFound(
            f"{binary_path.name} 실행 확인에 실패했습니다 (종료 코드 {result.returncode}). {detail}"
        )
