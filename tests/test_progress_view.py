"""다운로드 진행 화면의 중단·복귀 흐름을 검증한다."""

from __future__ import annotations

import threading
from types import SimpleNamespace

from pudufu.models import Course, Summary
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
