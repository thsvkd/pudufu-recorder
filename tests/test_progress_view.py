"""다운로드 진행 화면의 중단·복귀 흐름을 검증한다."""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

from pudufu.models import Course, Lesson, Progress, Summary
from ui.progress_view import ProgressScreen


class _FakePage:
    def __init__(self) -> None:
        self.updated = []

    def update(self, *controls) -> None:  # type: ignore[no-untyped-def]
        self.updated.extend(controls)


class _FakeApp:
    def __init__(self) -> None:
        self.views = []

    def show_view(self, view) -> None:  # type: ignore[no-untyped-def]
        self.views.append(view)


def _screen() -> ProgressScreen:
    screen = ProgressScreen.__new__(ProgressScreen)
    screen.page = _FakePage()
    screen.app = _FakeApp()
    screen.course = Course(course_id="1", title="테스트 강의", entry_lesson_id="10")
    screen.previous_view = SimpleNamespace(name="강의 선택 화면")
    screen.cancel_event = threading.Event()
    screen.cancel_button = SimpleNamespace(disabled=False, text="중단하고 돌아가기")
    screen.return_after_cancel = False
    screen.last_summary = None
    return screen


def test_cancel_requests_return_after_recorder_stops() -> None:
    screen = _screen()

    screen._on_cancel_click(None)

    assert screen.cancel_event.is_set()
    assert screen.return_after_cancel is True
    assert screen.cancel_button.disabled is True
    assert screen.cancel_button.text == "중단 후 돌아가는 중..."
    assert screen.page.updated == [screen.cancel_button]
    assert screen.app.views == []


def test_cancelled_download_returns_only_after_finished() -> None:
    screen = _screen()
    screen.return_after_cancel = True
    summary = Summary(done=1, skipped=0, failed=0)

    screen._on_finished(summary)

    assert screen.last_summary is summary
    assert screen.app.views == [screen.previous_view]


def test_back_button_returns_to_lesson_selection() -> None:
    screen = _screen()

    screen._on_back_click(None)

    assert screen.app.views == [screen.previous_view]


def test_error_reason_is_shown_on_lesson_row() -> None:
    screen = _screen()
    lesson = Lesson(
        lesson_id="10",
        title="테스트 회차",
        duration_sec=None,
        section_index=0,
        section_title="파트",
        index_in_section=0,
        global_index=0,
    )
    badge = SimpleNamespace(bgcolor=None)
    badge_text = SimpleNamespace(value="대기", color=None)
    bar = SimpleNamespace(value=0.0)
    message_text = SimpleNamespace(value="", visible=False)
    screen.row_controls = {
        lesson.lesson_id: {
            "badge": badge,
            "badge_text": badge_text,
            "bar": bar,
            "message": message_text,
        }
    }
    screen.finished_ids = set()
    screen.total = 1
    screen.overall_text = SimpleNamespace(value="0/1 완료")
    screen.overall_bar = SimpleNamespace(value=0.0)
    screen.eta_text = SimpleNamespace(value="")
    screen.start_time = time.monotonic()

    screen._apply_progress(
        Progress(
            lesson=lesson,
            stage="error",
            percent=0.0,
            message="다운로드 가능한 영상을 찾지 못했습니다.",
        )
    )

    assert message_text.value == "다운로드 가능한 영상을 찾지 못했습니다."
    assert message_text.visible is True
    assert message_text in screen.page.updated
