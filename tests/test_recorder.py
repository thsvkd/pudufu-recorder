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


def _youtube_source() -> VideoSource:
    return VideoSource(
        uid="auFRYPDpiMQ",
        mp4_url=None,
        hls_url=None,
        youtube_url="https://www.youtube.com/watch?v=auFRYPDpiMQ",
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


def test_missing_video_source_retries_then_reports_error(tmp_path: Path) -> None:
    attempts = 0

    def get_video_source(*args):  # type: ignore[no-untyped-def]
        nonlocal attempts
        attempts += 1
        return None

    recorder = Recorder.__new__(Recorder)
    recorder.client = SimpleNamespace(get_video_source=get_video_source)
    recorder.output_dir = tmp_path
    recorder.keep_original = False
    progress = []

    status, message = recorder._process_lesson(
        _course(), _lesson(), progress.append, threading.Event()
    )

    assert attempts == recorder_module._MAX_ATTEMPTS
    assert status == "error"
    assert "다운로드 가능한 영상을 찾지 못했습니다" in message
    assert progress[-1].stage == "error"
    assert progress[-1].message == message


def test_youtube_source_downloads_then_converts_and_cleans_temporary_original(
    tmp_path: Path,
) -> None:
    source = _youtube_source()
    recorder = Recorder.__new__(Recorder)
    recorder.client = SimpleNamespace(get_video_source=lambda *args: source)
    recorder.keep_original = False
    recorder.speed = 1.5
    downloads = []
    conversions = []

    def download_youtube(url, destination, *args) -> None:  # type: ignore[no-untyped-def]
        downloads.append((url, destination))
        destination.write_bytes(b"youtube-original")

    def convert(input_source, *args) -> None:  # type: ignore[no-untyped-def]
        conversions.append(input_source)
        assert input_source.read_bytes() == b"youtube-original"

    recorder._download_youtube = download_youtube
    recorder._convert = convert
    final_path = tmp_path / "final.mp4"

    recorder._process_lesson_once(
        _course(), _lesson(), final_path, lambda progress: None, threading.Event()
    )

    temporary_original = tmp_path / ".raw_lesson_final.mp4"
    assert downloads == [
        (
            source.youtube_url,
            tmp_path / ".raw_lesson_final.part.mp4",
        )
    ]
    assert conversions == [temporary_original]
    assert not temporary_original.exists()


def test_download_youtube_uses_bundled_ffmpeg_and_reports_progress(
    monkeypatch, tmp_path: Path
) -> None:  # type: ignore[no-untyped-def]
    captured_options = {}

    class _YoutubeDL:
        def __init__(self, options) -> None:  # type: ignore[no-untyped-def]
            captured_options.update(options)

        def __enter__(self):  # type: ignore[no-untyped-def]
            return self

        def __exit__(self, *args) -> None:  # type: ignore[no-untyped-def]
            pass

        def extract_info(self, url: str, download: bool):  # type: ignore[no-untyped-def]
            hook = captured_options["progress_hooks"][0]
            hook(
                {
                    "status": "downloading",
                    "downloaded_bytes": 25,
                    "total_bytes": 100,
                }
            )
            downloaded_path = Path(captured_options["outtmpl"].replace("%(ext)s", "mp4"))
            downloaded_path.write_bytes(b"youtube")
            hook({"status": "finished"})
            return {"filepath": str(downloaded_path)}

    monkeypatch.setattr(recorder_module, "YoutubeDL", _YoutubeDL)
    monkeypatch.setattr(recorder_module, "find_deno_bin", lambda: "/bundled/deno")
    recorder = Recorder.__new__(Recorder)
    recorder.ffmpeg = tmp_path / "tools" / "ffmpeg"
    destination = tmp_path / "video.part.mp4"
    progress = []

    recorder._download_youtube(
        _youtube_source().youtube_url,
        destination,
        _lesson(),
        progress.append,
        threading.Event(),
    )

    assert destination.read_bytes() == b"youtube"
    assert captured_options["ffmpeg_location"] == str(recorder.ffmpeg.parent)
    assert captured_options["js_runtimes"] == {"deno": {"path": "/bundled/deno"}}
    assert captured_options["merge_output_format"] == "mp4"
    assert captured_options["noplaylist"] is True
    assert len(captured_options["postprocessor_hooks"]) == 1
    assert callable(captured_options["match_filter"])
    assert [item.percent for item in progress] == [0.0, 25.0, 100.0]


def test_download_youtube_cancels_from_progress_hook(monkeypatch, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    cancel = threading.Event()

    class _YoutubeDL:
        def __init__(self, options) -> None:  # type: ignore[no-untyped-def]
            self.hook = options["progress_hooks"][0]
            self.output_dir = Path(options["outtmpl"]).parent

        def __enter__(self):  # type: ignore[no-untyped-def]
            return self

        def __exit__(self, *args) -> None:  # type: ignore[no-untyped-def]
            pass

        def extract_info(self, url: str, download: bool):  # type: ignore[no-untyped-def]
            (self.output_dir / "video.f137.mp4.part").write_bytes(b"partial")
            cancel.set()
            self.hook({"status": "downloading"})

    monkeypatch.setattr(recorder_module, "YoutubeDL", _YoutubeDL)
    monkeypatch.setattr(recorder_module, "find_deno_bin", lambda: "/bundled/deno")
    recorder = Recorder.__new__(Recorder)
    recorder.ffmpeg = tmp_path / "ffmpeg"

    with pytest.raises(recorder_module._Cancelled):
        recorder._download_youtube(
            _youtube_source().youtube_url,
            tmp_path / "video.part.mp4",
            _lesson(),
            lambda progress: None,
            cancel,
        )

    assert not list(tmp_path.glob(".ytdlp_*"))


@pytest.mark.parametrize("boundary", ["metadata", "postprocessing"])
def test_download_youtube_cancels_between_non_download_phases(
    monkeypatch, tmp_path: Path, boundary: str
) -> None:  # type: ignore[no-untyped-def]
    cancel = threading.Event()

    class _YoutubeDL:
        def __init__(self, options) -> None:  # type: ignore[no-untyped-def]
            self.options = options

        def __enter__(self):  # type: ignore[no-untyped-def]
            return self

        def __exit__(self, *args) -> None:  # type: ignore[no-untyped-def]
            pass

        def extract_info(self, url: str, download: bool):  # type: ignore[no-untyped-def]
            cancel.set()
            if boundary == "metadata":
                self.options["match_filter"]({})
            self.options["postprocessor_hooks"][0]({"status": "started"})

    monkeypatch.setattr(recorder_module, "YoutubeDL", _YoutubeDL)
    monkeypatch.setattr(recorder_module, "find_deno_bin", lambda: "/bundled/deno")
    recorder = Recorder.__new__(Recorder)
    recorder.ffmpeg = tmp_path / "ffmpeg"

    with pytest.raises(recorder_module._Cancelled):
        recorder._download_youtube(
            _youtube_source().youtube_url,
            tmp_path / "video.part.mp4",
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
