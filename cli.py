"""GUI 없이 pudufu 코어를 검증하기 위한 CLI.

.env의 PUDUFU_ID/PUDUFU_PW로 로그인한 뒤 강의 목록/목차 조회 또는
실제 다운로드+1.5배속 변환을 수행한다.
"""

from __future__ import annotations

import argparse
import sys
import threading
from pathlib import Path

from dotenv import load_dotenv
import os

from pudufu.client import LoginError, PuduFuClient
from pudufu.ffmpeg_tool import find_ffmpeg, install_ffmpeg
from pudufu.models import Course, Progress
from pudufu.recorder import Recorder

_print_lock = threading.Lock()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="프드프 강의 1.5배속 다운로더 (CLI)")
    parser.add_argument("--course-id", help="처리할 강의 ID")
    parser.add_argument("--limit", type=int, default=None, help="처리할 최대 강의 수(영상 있는 강의 기준)")
    parser.add_argument("--speed", type=float, default=1.5, help="배속 (기본 1.5)")
    parser.add_argument("--output", default="./pudufu_downloads", help="출력 디렉터리")
    parser.add_argument("--keep-original", action="store_true", help="원본 파일 보관")
    parser.add_argument("--workers", type=int, default=2, help="동시 처리 강의 수")
    parser.add_argument("--list", action="store_true", help="강의 목록만 출력하고 종료")
    return parser.parse_args()


def print_progress(progress: Progress) -> None:
    with _print_lock:
        print(
            f"\r[{progress.stage:<11}] {progress.lesson.title[:30]:<30} "
            f"{progress.percent:5.1f}% {progress.message}",
            end="",
            flush=True,
        )
        if progress.stage in ("done", "skipped", "error"):
            print()


def main() -> int:
    args = parse_args()
    load_dotenv()

    email = os.environ.get("PUDUFU_ID")
    password = os.environ.get("PUDUFU_PW")
    if not email or not password:
        print(".env에 PUDUFU_ID / PUDUFU_PW가 설정되어 있지 않습니다.", file=sys.stderr)
        return 1

    client = PuduFuClient()
    try:
        client.login(email, password)
    except LoginError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    courses = client.list_my_courses()

    if args.list:
        print(f"내 강의 목록 ({len(courses)}개)")
        for course in courses:
            print(f"  - [{course.course_id}] {course.title}")

        if args.course_id:
            course = _find_course(courses, args.course_id)
            if course is None:
                print(f"courseId={args.course_id} 강의를 찾을 수 없습니다.", file=sys.stderr)
                return 1
            lessons = client.list_lessons(course)
            print(f"\n[{course.course_id}] {course.title} - 강의 {len(lessons)}개")
            last_section = None
            for lesson in lessons:
                if lesson.section_index != last_section:
                    print(f"  {lesson.section_title}")
                    last_section = lesson.section_index
                duration = f"{lesson.duration_sec}초" if lesson.duration_sec is not None else "재생시간 정보 없음"
                print(f"    - {lesson.title} ({duration})")
        return 0

    if not args.course_id:
        print("--course-id가 필요합니다. (--list로 목록을 먼저 확인하세요)", file=sys.stderr)
        return 1

    course = _find_course(courses, args.course_id)
    if course is None:
        print(f"courseId={args.course_id} 강의를 찾을 수 없습니다.", file=sys.stderr)
        return 1

    lessons = client.list_lessons(course)
    # duration_sec은 사이트가 표시한 재생시간일 뿐 영상 유무와 무관하므로
    # 여기서 걸러내지 않는다. 영상이 없는 강의는 Recorder가 처리 중 판정해 건너뛴다.
    target_lessons = lessons if args.limit is None else lessons[: args.limit]

    print(f"[{course.course_id}] {course.title} - 처리 대상 {len(target_lessons)}개")

    ffmpeg_paths = find_ffmpeg()
    if ffmpeg_paths is None:
        print("ffmpeg를 찾을 수 없어 다운로드를 시도합니다...")
        ffmpeg_paths = install_ffmpeg(
            on_progress=lambda ratio: print(f"\rffmpeg 다운로드 중... {ratio * 100:5.1f}%", end="", flush=True)
        )
        print()
    ffmpeg, ffprobe = ffmpeg_paths

    recorder = Recorder(
        client=client,
        ffmpeg=ffmpeg,
        ffprobe=ffprobe,
        output_dir=Path(args.output),
        speed=args.speed,
        keep_original=args.keep_original,
        workers=args.workers,
    )

    cancel = threading.Event()
    try:
        summary = recorder.run(course, target_lessons, print_progress, cancel)
    except KeyboardInterrupt:
        cancel.set()
        print("\n취소되었습니다.", file=sys.stderr)
        return 130

    print(f"\n완료: {summary.done}, 건너뜀: {summary.skipped}, 실패: {summary.failed}")
    for title, message in summary.errors:
        print(f"  - [실패] {title}: {message}")

    return 0 if summary.failed == 0 else 1


def _find_course(courses: list[Course], course_id: str) -> Course | None:
    for course in courses:
        if course.course_id == course_id:
            return course
    return None


if __name__ == "__main__":
    raise SystemExit(main())
