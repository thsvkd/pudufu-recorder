"""MP4 우선 선택과 HLS fallback 규칙을 검증한다."""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

import pudufu.recorder as recorder_module
from pudufu.models import Course, Lesson, VideoSource
from pudufu.recorder import Recorder


class _GetResponse:
    def __init__(
        self,
        status_code: int = 200,
        chunks: tuple[bytes, ...] = (b"abc", b"def"),
        content_type: str = "video/mp4",
    ) -> None:
        self.status_code = status_code
        self.chunks = chunks
        self.headers = {
            "content-length": str(sum(map(len, chunks))),
            "content-type": content_type,
        }

    def __enter__(self) -> _GetResponse:
        return self

    def __exit__(self, *args) -> None:  # type: ignore[no-untyped-def]
        pass

    def iter_content(self, chunk_size: int):  # type: ignore[no-untyped-def]
        return iter(self.chunks)


class _StalledGetResponse(_GetResponse):
    def __init__(self, cancel: threading.Event) -> None:
        super().__init__()
        self.cancel = cancel

    def iter_content(self, chunk_size: int):  # type: ignore[no-untyped-def]
        self.cancel.set()
        raise requests.ReadTimeout("stalled")


class _BrokenRangeResponse(_GetResponse):
    def iter_content(self, chunk_size: int):  # type: ignore[no-untyped-def]
        raise requests.ReadTimeout("stalled")


def _source() -> VideoSource:
    return VideoSource(
        uid="0123456789abcdef0123456789abcdef",
        mp4_url="https://customer.example/video/downloads/default.mp4",
        hls_url="https://customer.example/video/manifest/video.m3u8",
    )


def _course() -> Course:
    return Course(course_id="course", title="강의", entry_lesson_id="lesson")


def _lesson() -> Lesson:
    return Lesson(
        lesson_id="lesson",
        title="회차",
        duration_sec=60,
        section_index=0,
        section_title="파트",
        index_in_section=0,
        global_index=0,
    )


def test_download_mp4_writes_response_directly(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: _GetResponse())
    recorder = Recorder.__new__(Recorder)
    recorder._probe_stream_duration = lambda source: 60
    destination = tmp_path / "original.part.mp4"
    progress = []

    downloaded = recorder._download_mp4(
        _source().mp4_url,
        destination,
        _lesson(),
        progress.append,
        threading.Event(),
    )

    assert downloaded is True
    assert destination.read_bytes() == b"abcdef"
    assert progress[-1].percent == 100.0


def test_mp4_check_reads_a_byte_with_bounded_range_get(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls = []

    def get(*args, **kwargs):  # type: ignore[no-untyped-def]
        calls.append((args, kwargs))
        return _GetResponse()

    monkeypatch.setattr(requests, "get", get)
    recorder = Recorder.__new__(Recorder)

    assert recorder._check_mp4(_source().mp4_url) is recorder_module._MP4Availability.AVAILABLE
    assert calls[0][1]["headers"] == {"Range": "bytes=0-0"}
    assert calls[0][1]["timeout"] == (3, 1)


def test_mp4_check_reports_unknown_when_range_body_stalls(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: _BrokenRangeResponse())
    recorder = Recorder.__new__(Recorder)

    assert recorder._check_mp4(_source().mp4_url) is recorder_module._MP4Availability.UNKNOWN


def test_download_mp4_rejects_html_response(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        requests,
        "get",
        lambda *args, **kwargs: _GetResponse(content_type="text/html"),
    )
    recorder = Recorder.__new__(Recorder)
    destination = tmp_path / "original.part.mp4"

    downloaded = recorder._download_mp4(
        _source().mp4_url,
        destination,
        _lesson(),
        lambda progress: None,
        threading.Event(),
    )

    assert downloaded is False
    assert not destination.exists()


def test_download_mp4_turns_read_timeout_into_cancellation(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    cancel = threading.Event()
    monkeypatch.setattr(
        requests,
        "get",
        lambda *args, **kwargs: _StalledGetResponse(cancel),
    )
    recorder = Recorder.__new__(Recorder)

    with pytest.raises(recorder_module._Cancelled):
        recorder._download_mp4(
            _source().mp4_url,
            tmp_path / "original.part.mp4",
            _lesson(),
            lambda progress: None,
            cancel,
        )


def test_missing_mp4_uses_single_pass_hls(tmp_path: Path) -> None:
    source = _source()
    recorder = Recorder.__new__(Recorder)
    recorder.client = SimpleNamespace(get_video_source=lambda *args: source)
    recorder.keep_original = False
    recorder.speed = 1.5
    recorder._check_mp4 = lambda url: recorder_module._MP4Availability.UNAVAILABLE
    attempts = []
    recorder._convert = lambda input_source, *args: attempts.append(input_source)

    recorder._process_lesson_once(
        _course(), _lesson(), tmp_path / "final.mp4", lambda progress: None, threading.Event()
    )

    assert attempts == [source.hls_url]


def test_mp4_source_failure_during_conversion_falls_back_to_hls(tmp_path: Path) -> None:
    source = _source()
    recorder = Recorder.__new__(Recorder)
    recorder.client = SimpleNamespace(get_video_source=lambda *args: source)
    recorder.keep_original = False
    recorder.speed = 1.5
    availability = iter(
        (recorder_module._MP4Availability.AVAILABLE, recorder_module._MP4Availability.UNAVAILABLE)
    )
    recorder._check_mp4 = lambda url: next(availability)
    attempts = []

    def convert(input_source, *args) -> None:  # type: ignore[no-untyped-def]
        attempts.append(input_source)
        if input_source == source.mp4_url:
            raise RuntimeError("source disappeared")

    recorder._convert = convert

    recorder._process_lesson_once(
        _course(), _lesson(), tmp_path / "final.mp4", lambda progress: None, threading.Event()
    )

    assert attempts == [source.mp4_url, source.hls_url]


def test_local_conversion_error_does_not_fall_back_to_hls(tmp_path: Path) -> None:
    source = _source()
    recorder = Recorder.__new__(Recorder)
    recorder.client = SimpleNamespace(get_video_source=lambda *args: source)
    recorder.keep_original = False
    recorder.speed = 1.5
    recorder._check_mp4 = lambda url: recorder_module._MP4Availability.AVAILABLE
    attempts = []

    def convert(input_source, *args) -> None:  # type: ignore[no-untyped-def]
        attempts.append(input_source)
        raise RuntimeError("disk full")

    recorder._convert = convert

    with pytest.raises(RuntimeError, match="disk full"):
        recorder._process_lesson_once(
            _course(),
            _lesson(),
            tmp_path / "final.mp4",
            lambda progress: None,
            threading.Event(),
        )

    assert attempts == [source.mp4_url]


def test_unknown_recheck_does_not_mask_local_conversion_error(tmp_path: Path) -> None:
    source = _source()
    recorder = Recorder.__new__(Recorder)
    recorder.client = SimpleNamespace(get_video_source=lambda *args: source)
    recorder.keep_original = False
    recorder.speed = 1.5
    availability = iter(
        (recorder_module._MP4Availability.AVAILABLE, recorder_module._MP4Availability.UNKNOWN)
    )
    recorder._check_mp4 = lambda url: next(availability)
    recorder._convert = lambda *args: (_ for _ in ()).throw(RuntimeError("disk full"))

    with pytest.raises(RuntimeError, match="disk full"):
        recorder._process_lesson_once(
            _course(),
            _lesson(),
            tmp_path / "final.mp4",
            lambda progress: None,
            threading.Event(),
        )


def test_keep_original_download_falls_back_from_mp4_to_hls(tmp_path: Path) -> None:
    source = _source()
    recorder = Recorder.__new__(Recorder)
    recorder.client = SimpleNamespace(get_video_source=lambda *args: source)
    recorder.keep_original = True
    recorder.speed = 1.5
    recorder._download_mp4 = lambda *args: False
    hls_downloads = []
    conversions = []

    def download_hls(url, destination, *args) -> None:  # type: ignore[no-untyped-def]
        hls_downloads.append(url)
        destination.write_bytes(b"hls-original")

    recorder._download_hls = download_hls
    recorder._convert = lambda input_source, *args: conversions.append(input_source)
    final_path = tmp_path / "final.mp4"

    recorder._process_lesson_once(
        _course(), _lesson(), final_path, lambda progress: None, threading.Event()
    )

    original_path = tmp_path / "원본" / "final.mp4"
    assert hls_downloads == [source.hls_url]
    assert original_path.read_bytes() == b"hls-original"
    assert conversions == [original_path]


def test_streaming_conversion_keeps_ffmpeg_automatic_best_stream_selection(
    tmp_path: Path,
) -> None:
    recorder = Recorder.__new__(Recorder)
    recorder.ffmpeg = Path("ffmpeg")
    recorder.speed = 1.5
    recorder._use_videotoolbox = False
    recorder._read_bitrate = lambda source: (_ for _ in ()).throw(
        AssertionError("software encoding must not probe bitrate")
    )
    commands = []

    def run_ffmpeg(cmd, *args) -> None:  # type: ignore[no-untyped-def]
        commands.append(cmd)
        Path(cmd[-1]).write_bytes(b"converted")

    recorder._run_ffmpeg = run_ffmpeg
    final_path = tmp_path / "final.mp4"

    recorder._convert(
        _source().hls_url,
        final_path,
        60,
        "streaming",
        "내려받으며 변환 중",
        _lesson(),
        lambda progress: None,
        threading.Event(),
    )

    assert final_path.read_bytes() == b"converted"
    assert commands[0][commands[0].index("-i") + 1] == _source().hls_url
    assert commands[0][commands[0].index("-vf") + 1] == "setpts=PTS/1.5"
    assert commands[0][commands[0].index("-af") + 1] == "atempo=1.5"
    assert "-map" not in commands[0]


def test_run_ffmpeg_cancels_silent_process_promptly() -> None:
    recorder = Recorder.__new__(Recorder)
    cancel = threading.Event()
    timer = threading.Timer(0.1, cancel.set)
    timer.start()
    started = time.monotonic()

    with pytest.raises(recorder_module._Cancelled):
        recorder._run_ffmpeg(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            None,
            "streaming",
            _lesson(),
            lambda progress: None,
            cancel,
        )

    timer.cancel()
    assert time.monotonic() - started < 2
