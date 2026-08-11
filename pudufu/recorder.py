"""강의 영상을 다운로드하고 1.5배속으로 변환하는 레코더."""

from __future__ import annotations

import platform
import re
import subprocess
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from pudufu.client import PuduFuClient
from pudufu.ffmpeg_tool import NO_WINDOW
from pudufu.models import Course, Lesson, Progress, Summary
from pudufu.util import sanitize_filename

_TIME_RE = re.compile(r"time=(\d+):(\d\d):(\d\d(?:\.\d+)?)")
_MIN_BITRATE = 1_000_000
_MAX_BITRATE = 12_000_000
_MAX_ATTEMPTS = 3  # 최초 시도 1회 + 재시도 2회


class _Cancelled(Exception):
    """cancel 이벤트가 set되어 처리를 중단할 때 내부적으로 사용한다."""


class Recorder:
    def __init__(
        self,
        client: PuduFuClient,
        ffmpeg: Path,
        ffprobe: Path,
        output_dir: Path,
        speed: float = 1.5,
        keep_original: bool = False,
        workers: int = 2,
    ) -> None:
        self.client = client
        self.ffmpeg = Path(ffmpeg)
        self.ffprobe = Path(ffprobe)
        self.output_dir = Path(output_dir)
        self.speed = speed
        self.keep_original = keep_original
        self.workers = max(1, workers)
        self._use_videotoolbox = self._check_videotoolbox()

    def run(
        self,
        course: Course,
        lessons: list[Lesson],
        on_progress: Callable[[Progress], None],
        cancel: threading.Event,
    ) -> Summary:
        done = skipped = failed = 0
        errors: list[tuple[str, str]] = []
        lock = threading.Lock()

        with ThreadPoolExecutor(max_workers=self.workers) as executor:
            futures = {
                executor.submit(self._process_lesson, course, lesson, on_progress, cancel): lesson
                for lesson in lessons
            }
            for future in as_completed(futures):
                lesson = futures[future]
                try:
                    status, message = future.result()
                except Exception as exc:  # 예상치 못한 예외에 대한 방어
                    status, message = "error", str(exc)
                with lock:
                    if status == "done":
                        done += 1
                    elif status == "skipped":
                        skipped += 1
                    elif status == "error":
                        failed += 1
                        errors.append((lesson.title, message))
                    # 'cancelled'는 집계하지 않는다.

        return Summary(done=done, skipped=skipped, failed=failed, errors=errors)

    # -- 강의 1개 처리 -----------------------------------------------------

    def _process_lesson(
        self,
        course: Course,
        lesson: Lesson,
        on_progress: Callable[[Progress], None],
        cancel: threading.Event,
    ) -> tuple[str, str]:
        if cancel.is_set():
            return ("cancelled", "취소됨")

        final_path = self._final_path(course, lesson)
        if final_path.exists() and final_path.stat().st_size > 0:
            on_progress(
                Progress(lesson=lesson, stage="skipped", percent=100.0, message="이미 다운로드됨")
            )
            return ("skipped", "이미 다운로드됨")

        final_path.parent.mkdir(parents=True, exist_ok=True)

        last_error = "알 수 없는 오류"
        for _attempt in range(_MAX_ATTEMPTS):
            if cancel.is_set():
                return ("cancelled", "취소됨")
            try:
                self._process_lesson_once(course, lesson, final_path, on_progress, cancel)
                on_progress(Progress(lesson=lesson, stage="done", percent=100.0, message="완료"))
                return ("done", "")
            except _Cancelled:
                self._cleanup_partials(course, lesson, final_path)
                return ("cancelled", "취소됨")
            except _SkippedNoVideo:
                self._cleanup_partials(course, lesson, final_path)
                return ("skipped", "영상 없음")
            except Exception as exc:
                last_error = str(exc)
                self._cleanup_partials(course, lesson, final_path)
                continue

        on_progress(Progress(lesson=lesson, stage="error", percent=0.0, message=last_error))
        return ("error", last_error)

    def _process_lesson_once(
        self,
        course: Course,
        lesson: Lesson,
        final_path: Path,
        on_progress: Callable[[Progress], None],
        cancel: threading.Event,
    ) -> None:
        on_progress(
            Progress(lesson=lesson, stage="fetching", percent=0.0, message="영상 정보 조회 중")
        )
        uid = self.client.get_video_uid(course.course_id, lesson.lesson_id)
        if uid is None:
            on_progress(Progress(lesson=lesson, stage="skipped", percent=0.0, message="영상 없음"))
            raise _SkippedNoVideo()
        if cancel.is_set():
            raise _Cancelled()

        m3u8_url = f"https://videodelivery.net/{uid}/manifest/video.m3u8"
        raw_path = self._raw_path(course, lesson, final_path)
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_part = _part_path(raw_path)

        # 강의에 따라 사이트가 재생시간을 표시하지 않는 경우가 있다.
        # 그럴 때는 m3u8 매니페스트를 ffprobe로 조회해 진행률 분모를 구한다.
        total_sec = lesson.duration_sec
        if total_sec is None:
            total_sec = self._probe_stream_duration(m3u8_url)

        on_progress(
            Progress(lesson=lesson, stage="downloading", percent=0.0, message="다운로드 중")
        )
        download_cmd = [
            str(self.ffmpeg),
            "-y",
            "-v",
            "error",
            "-stats",
            "-i",
            m3u8_url,
            "-c",
            "copy",
            "-bsf:a",
            "aac_adtstoasc",
            str(raw_part),
        ]
        self._run_ffmpeg(download_cmd, total_sec, "downloading", lesson, on_progress, cancel)
        raw_part.replace(raw_path)

        bitrate = self._read_bitrate(raw_path)
        filter_complex = self._build_filter_complex()
        final_part = _part_path(final_path)

        if self._use_videotoolbox:
            video_codec_args = ["-c:v", "h264_videotoolbox", "-b:v", str(bitrate)]
        else:
            video_codec_args = ["-c:v", "libx264", "-crf", "20", "-preset", "veryfast"]

        convert_cmd = [
            str(self.ffmpeg),
            "-y",
            "-v",
            "error",
            "-stats",
            "-i",
            str(raw_path),
            "-filter_complex",
            filter_complex,
            "-map",
            "[v]",
            "-map",
            "[a]",
            *video_codec_args,
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            str(final_part),
        ]
        on_progress(
            Progress(lesson=lesson, stage="converting", percent=0.0, message="1.5배속 변환 중")
        )
        converted_duration = total_sec / self.speed if total_sec is not None else None
        self._run_ffmpeg(convert_cmd, converted_duration, "converting", lesson, on_progress, cancel)
        final_part.replace(final_path)

        if not self.keep_original:
            raw_path.unlink(missing_ok=True)

    # -- 경로 계산 -----------------------------------------------------

    def _final_path(self, course: Course, lesson: Lesson) -> Path:
        safe_course = sanitize_filename(course.title, f"course_{course.course_id}")
        safe_section = sanitize_filename(lesson.section_title, f"section_{lesson.section_index}")
        safe_title = sanitize_filename(lesson.title, f"lesson_{lesson.lesson_id}")
        section_dir = (
            self.output_dir / safe_course / f"{lesson.section_index + 1:02d}_{safe_section}"
        )
        return section_dir / f"{lesson.index_in_section + 1:02d}_{safe_title}.mp4"

    def _raw_path(self, course: Course, lesson: Lesson, final_path: Path) -> Path:
        if self.keep_original:
            return final_path.parent / "원본" / final_path.name
        return final_path.parent / f".raw_{lesson.lesson_id}_{final_path.name}"

    def _cleanup_partials(self, course: Course, lesson: Lesson, final_path: Path) -> None:
        _part_path(final_path).unlink(missing_ok=True)
        raw_path = self._raw_path(course, lesson, final_path)
        _part_path(raw_path).unlink(missing_ok=True)
        if not self.keep_original:
            raw_path.unlink(missing_ok=True)

    # -- ffmpeg 실행 -----------------------------------------------------

    def _run_ffmpeg(
        self,
        cmd: list[str],
        total_sec: float | None,
        stage: str,
        lesson: Lesson,
        on_progress: Callable[[Progress], None],
        cancel: threading.Event,
    ) -> None:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            creationflags=NO_WINDOW,
        )
        try:
            buf = ""
            while True:
                ch = proc.stderr.read(1)
                if cancel.is_set():
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                    raise _Cancelled()
                if ch == "":
                    break
                if ch in ("\r", "\n"):
                    if buf:
                        self._report_ffmpeg_line(buf, total_sec, stage, lesson, on_progress)
                    buf = ""
                else:
                    buf += ch
            if buf:
                self._report_ffmpeg_line(buf, total_sec, stage, lesson, on_progress)
            proc.wait()
        finally:
            if proc.stderr:
                proc.stderr.close()

        if cancel.is_set():
            raise _Cancelled()
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg 실행 실패 (종료 코드 {proc.returncode})")

    _STAGE_LABELS = {"downloading": "내려받는 중", "converting": "변환 중"}

    @classmethod
    def _report_ffmpeg_line(
        cls,
        line: str,
        total_sec: float | None,
        stage: str,
        lesson: Lesson,
        on_progress: Callable[[Progress], None],
    ) -> None:
        match = _TIME_RE.search(line)
        if not match:
            return
        hours, minutes, seconds = match.groups()
        elapsed = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
        if total_sec:
            percent = max(0.0, min(100.0, elapsed / total_sec * 100))
            on_progress(Progress(lesson=lesson, stage=stage, percent=percent, message=""))
        else:
            # 총 길이를 알 수 없는 경우: 멈춘 것처럼 보이지 않도록 경과 시간을 알려준다.
            label = cls._STAGE_LABELS.get(stage, stage)
            message = f"{label} ({_format_elapsed(elapsed)})"
            on_progress(Progress(lesson=lesson, stage=stage, percent=0.0, message=message))

    def _probe_stream_duration(self, m3u8_url: str) -> float | None:
        try:
            result = subprocess.run(
                [
                    str(self.ffprobe),
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=nw=1:nk=1",
                    m3u8_url,
                ],
                capture_output=True,
                text=True,
                timeout=30,
                creationflags=NO_WINDOW,
            )
            value = result.stdout.strip()
            return float(value) if value else None
        except Exception:
            return None

    def _read_bitrate(self, path: Path) -> int:
        for args in (
            ["-select_streams", "v:0", "-show_entries", "stream=bit_rate"],
            ["-show_entries", "format=bit_rate"],
        ):
            try:
                result = subprocess.run(
                    [
                        str(self.ffprobe),
                        "-v",
                        "error",
                        *args,
                        "-of",
                        "default=nw=1:nk=1",
                        str(path),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    creationflags=NO_WINDOW,
                )
                value = result.stdout.strip()
                if value.isdigit():
                    return max(_MIN_BITRATE, min(_MAX_BITRATE, int(value)))
            except Exception:
                continue
        return _MIN_BITRATE * 4  # ffprobe로 읽지 못한 경우의 기본값

    def _check_videotoolbox(self) -> bool:
        if platform.system() != "Darwin":
            return False
        try:
            result = subprocess.run(
                [str(self.ffmpeg), "-hide_banner", "-encoders"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            return "h264_videotoolbox" in result.stdout
        except Exception:
            return False

    def _build_filter_complex(self) -> str:
        factors = _atempo_factors(self.speed)
        audio_filter = ",".join(f"atempo={factor}" for factor in factors)
        return f"[0:v]setpts=PTS/{self.speed}[v];[0:a]{audio_filter}[a]"


class _SkippedNoVideo(Exception):
    """get_video_uid가 None을 반환해 영상 없이 건너뛸 때 사용하는 내부 신호."""


def _part_path(path: Path) -> Path:
    """중단된 파일이 완성본으로 오인되지 않도록 확장자 앞에 .part를 끼워넣는다.

    (예: foo.mp4 -> foo.part.mp4. ffmpeg가 출력 포맷을 확장자로 추론하므로
    foo.mp4.part처럼 끝에 붙이면 muxer를 찾지 못해 실패한다.)
    """
    return path.with_name(f"{path.stem}.part{path.suffix}")


def _format_elapsed(seconds: float) -> str:
    """경과 시간을 '3분 12초' 형태의 사람이 읽기 쉬운 문자열로 변환한다."""
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}시간 {minutes}분 {secs}초"
    if minutes:
        return f"{minutes}분 {secs}초"
    return f"{secs}초"


def _atempo_factors(speed: float) -> list[float]:
    """atempo는 0.5~2.0만 지원하므로 범위를 벗어나면 체인으로 분해한다."""
    if 0.5 <= speed <= 2.0:
        return [speed]
    factors: list[float] = []
    remaining = speed
    if remaining > 2.0:
        while remaining > 2.0:
            factors.append(2.0)
            remaining /= 2.0
    else:
        while remaining < 0.5:
            factors.append(0.5)
            remaining /= 0.5
    factors.append(remaining)
    return factors
