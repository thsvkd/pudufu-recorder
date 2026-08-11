"""ffmpeg/ffprobe 자동 설치의 다운로드·압축 해제 규칙 검증.

여기서 막으려는 회귀는 v0.1.0의 실제 버그다. ffprobe 배포 주소를
``/ffprobe/getrelease/zip``으로 적었는데 이 주소는 404가 아니라 **ffmpeg zip으로 302
리다이렉트**된다. 그래서 ffprobe를 받는다며 ffmpeg를 다시 받아왔고, 압축 안에 ffprobe가
없으니 "다운로드/압축 해제에 실패"로 끝났다. 네트워크를 타는 테스트는 두지 않는다 —
URL 규약과 압축 해제 로직만 잠근다.
"""

from __future__ import annotations

import io
import shutil
import sys
import zipfile
from pathlib import Path

import pytest

from pudufu import ffmpeg_tool
from pudufu.ffmpeg_tool import (
    FFMPEG_ZIP_URL,
    FFPROBE_ZIP_URL,
    WINDOWS_ZIP_URL,
    FFmpegNotFound,
    _extract_binary,
    binary_name,
    find_ffmpeg,
    install_ffmpeg,
)


def _zip_with(names_to_bytes: dict[str, bytes]) -> io.BytesIO:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in names_to_bytes.items():
            zf.writestr(name, data)
    return buf


# -- URL 규약 -----------------------------------------------------------------


def test_urls_follow_tool_scoped_path() -> None:
    """evermeet.cx의 경로는 ``/ffmpeg/getrelease/<도구>/zip`` 형태다.

    도구 이름을 맨 앞에 두면(``/ffprobe/getrelease/zip``) ffmpeg zip으로 리다이렉트되어
    조용히 엉뚱한 파일을 받는다. 그 형태로 되돌아가는 것을 막는다.
    """
    assert FFMPEG_ZIP_URL == "https://evermeet.cx/ffmpeg/getrelease/ffmpeg/zip"
    assert FFPROBE_ZIP_URL == "https://evermeet.cx/ffmpeg/getrelease/ffprobe/zip"


def test_ffmpeg_and_ffprobe_urls_differ() -> None:
    # 두 주소가 같아지면 같은 파일을 두 번 받는다 — 그것이 이 버그의 증상이었다.
    assert FFMPEG_ZIP_URL != FFPROBE_ZIP_URL


# -- 압축 해제 ----------------------------------------------------------------


def test_extract_picks_binary_by_name(tmp_path: Path) -> None:
    archive = _zip_with({"ffprobe": b"BINARY"})

    path = _extract_binary(archive, tmp_path, "ffprobe")

    assert path == tmp_path / "ffprobe"
    assert path.read_bytes() == b"BINARY"


def test_extract_handles_nested_layout(tmp_path: Path) -> None:
    # 배포처가 폴더를 한 겹 씌워도 이름으로 찾으므로 그대로 동작해야 한다.
    archive = _zip_with({"ffprobe-9.0/ffprobe": b"BINARY"})

    path = _extract_binary(archive, tmp_path, "ffprobe")

    assert path == tmp_path / "ffprobe"
    assert path.read_bytes() == b"BINARY"


def test_extract_reports_actual_contents_when_wrong_archive(tmp_path: Path) -> None:
    """엉뚱한 압축이 오면 **안에 뭐가 있었는지**를 메시지에 남겨야 한다.

    원래 코드는 통째로 풀고 기대한 이름이 없으면 "압축 해제 실패"라고만 했다. 그 뭉뚱그린
    메시지 뒤에 "ffprobe 자리에 ffmpeg가 왔다"는 사실이 숨어 원인 파악이 늦어졌다.
    """
    archive = _zip_with({"ffmpeg": b"WRONG"})

    with pytest.raises(FFmpegNotFound) as excinfo:
        _extract_binary(archive, tmp_path, "ffprobe")

    message = str(excinfo.value)
    assert "ffprobe" in message
    assert "ffmpeg" in message  # 실제로 들어 있던 파일 이름


def test_extract_leaves_no_partial_file_on_failure(tmp_path: Path) -> None:
    # 실패했는데 반쪽 파일이 남으면 다음 실행 때 find_ffmpeg가 정상으로 보고 집어 든다.
    archive = _zip_with({"ffmpeg": b"WRONG"})

    with pytest.raises(FFmpegNotFound):
        _extract_binary(archive, tmp_path, "ffprobe")

    assert list(tmp_path.iterdir()) == []


def test_extract_ignores_directory_entries(tmp_path: Path) -> None:
    # 같은 이름의 디렉터리 항목을 파일로 착각해 꺼내면 안 된다.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("ffprobe/", b"")
        zf.writestr("ffprobe/ffprobe", b"BINARY")

    path = _extract_binary(buf, tmp_path, "ffprobe")

    assert path.read_bytes() == b"BINARY"


# -- Windows 지원 -------------------------------------------------------------
#
# v0.1.x는 darwin이 아니면 곧장 "macOS에서만 지원됩니다"로 끝나서, Windows 사용자는 설치
# 안내를 눌러도 실패 문구만 봤다. 아래는 그 회귀를 막는다.


@pytest.fixture
def as_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")


def test_binary_name_adds_exe_on_windows(as_windows: None) -> None:
    assert binary_name("ffmpeg") == "ffmpeg.exe"


def test_binary_name_stays_bare_on_macos(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")

    assert binary_name("ffprobe") == "ffprobe"


def test_find_ffmpeg_picks_up_exe_on_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, as_windows: None
) -> None:
    """설치는 ffmpeg.exe로 해 놓고 확장자 없는 이름만 찾으면 매번 다시 받게 된다."""
    (tmp_path / "ffmpeg.exe").write_bytes(b"BINARY")
    (tmp_path / "ffprobe.exe").write_bytes(b"BINARY")
    monkeypatch.setattr(ffmpeg_tool, "_app_data_dir", lambda: tmp_path)
    monkeypatch.setattr(shutil, "which", lambda _name: None)

    assert find_ffmpeg() == (tmp_path / "ffmpeg.exe", tmp_path / "ffprobe.exe")


def test_windows_install_downloads_one_archive_for_both_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, as_windows: None
) -> None:
    """gyan.dev 빌드는 zip 하나에 둘 다 들어 있다 — 도구마다 받으면 80MB를 두 번 받는다."""
    archive = _zip_with(
        {
            "ffmpeg-7.1-essentials_build/bin/ffmpeg.exe": b"FFMPEG",
            "ffmpeg-7.1-essentials_build/bin/ffprobe.exe": b"FFPROBE",
        }
    )
    requested: list[str] = []
    monkeypatch.setattr(ffmpeg_tool, "_app_data_dir", lambda: tmp_path)
    monkeypatch.setattr(ffmpeg_tool, "_verify_runnable", lambda _path: None)
    monkeypatch.setattr(
        ffmpeg_tool.requests,
        "get",
        lambda url, **_kwargs: _FakeResponse(url, archive.getvalue(), requested),
    )

    ffmpeg_path, ffprobe_path = install_ffmpeg()

    assert requested == [WINDOWS_ZIP_URL]
    assert ffmpeg_path.read_bytes() == b"FFMPEG"
    assert ffprobe_path.read_bytes() == b"FFPROBE"


def test_install_on_unsupported_platform_says_which_are_supported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")

    with pytest.raises(FFmpegNotFound) as excinfo:
        install_ffmpeg()

    message = str(excinfo.value)
    assert "macOS" in message
    assert "Windows" in message


class _FakeResponse:
    """requests.get 대체용. 네트워크를 타지 않고 준비된 zip 바이트를 흘려보낸다."""

    def __init__(self, url: str, payload: bytes, requested: list[str]) -> None:
        requested.append(url)
        self._payload = payload
        self.headers = {"Content-Length": str(len(payload))}

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    def iter_content(self, chunk_size: int) -> object:
        return iter(
            [self._payload[i : i + chunk_size] for i in range(0, len(self._payload), chunk_size)]
        )
