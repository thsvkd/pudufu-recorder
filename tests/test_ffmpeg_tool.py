"""ffmpeg/ffprobe 자동 설치의 다운로드·압축 해제 규칙 검증.

여기서 막으려는 회귀는 v0.1.0의 실제 버그다. ffprobe 배포 주소를
``/ffprobe/getrelease/zip``으로 적었는데 이 주소는 404가 아니라 **ffmpeg zip으로 302
리다이렉트**된다. 그래서 ffprobe를 받는다며 ffmpeg를 다시 받아왔고, 압축 안에 ffprobe가
없으니 "다운로드/압축 해제에 실패"로 끝났다. 네트워크를 타는 테스트는 두지 않는다 —
URL 규약과 압축 해제 로직만 잠근다.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from pudufu.ffmpeg_tool import (
    FFMPEG_ZIP_URL,
    FFPROBE_ZIP_URL,
    FFmpegNotFound,
    _extract_binary,
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
