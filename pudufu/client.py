"""pudufu.co.kr 웹사이트와 통신하는 클라이언트.

로그인, 내 강의 목록 조회, 강의 목차 조회, 영상 UID 추출을 담당한다.
모두 requests + BeautifulSoup 기반의 SSR HTML 스크래핑이다.
"""

from __future__ import annotations

import re

import requests
from bs4 import BeautifulSoup

from pudufu.models import Course, Lesson

BASE_URL = "https://pudufu.co.kr"
LOGIN_URL = f"{BASE_URL}/login/validate_login/user?before_url="
MYPDF_URL = f"{BASE_URL}/home/pdf_mypdf"

# "이어보기" 링크의 href는 두 가지 패턴이 모두 존재한다.
LECTURE_HREF_RE = re.compile(r"/(?:home/pdf_lecture|lecture)/(\d+)/(\d+)")
VIDEO_UID_RE = re.compile(r"cloudflarestream\.com/([a-f0-9]{32})")


class LoginError(Exception):
    """로그인 실패 시 발생한다."""


class PuduFuClient:
    def __init__(self, timeout: int = 30) -> None:
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "Mozilla/5.0"})

    def login(self, email: str, password: str) -> None:
        self.session.post(
            LOGIN_URL,
            data={"email": email, "password": password},
            timeout=self.timeout,
        )
        resp = self.session.get(MYPDF_URL, timeout=self.timeout)
        if "My콘텐츠" not in resp.text:
            raise LoginError("로그인 실패: 이메일 또는 비밀번호를 확인해주세요.")

    def list_my_courses(self) -> list[Course]:
        resp = self.session.get(MYPDF_URL, timeout=self.timeout)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        courses: dict[str, Course] = {}
        for a in soup.find_all("a", href=True):
            match = LECTURE_HREF_RE.search(a["href"])
            if not match:
                continue
            # "보러가기" 등 진행률이 없는 링크는 제외하고, "이어보기 N% 수강" 링크만 사용한다.
            if "이어보기" not in a.get_text(strip=True):
                continue
            course_id, lesson_id = match.group(1), match.group(2)
            if course_id in courses:
                continue
            title = self._find_course_title(a) or f"course_{course_id}"
            courses[course_id] = Course(
                course_id=course_id, title=title, entry_lesson_id=lesson_id
            )
        return list(courses.values())

    @staticmethod
    def _find_course_title(anchor) -> str | None:
        """강의 카드 조상 요소를 거슬러 올라가며 제목(p.title)을 찾는다."""
        node = anchor
        for _ in range(6):
            node = node.parent
            if node is None or not hasattr(node, "select_one"):
                break
            title_el = node.select_one("p.title")
            if title_el:
                return title_el.get_text(strip=True)
        return None

    def list_lessons(self, course: Course) -> list[Lesson]:
        url = f"{BASE_URL}/lecture/{course.course_id}/{course.entry_lesson_id}"
        resp = self.session.get(url, timeout=self.timeout)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        lessons: list[Lesson] = []
        global_index = 0
        for section_index, section in enumerate(soup.select("div.vod-section")):
            title_el = section.select_one(".vod-section__title")
            section_title = title_el.get_text(strip=True) if title_el else ""

            lesson_container = section.select_one(".vod-section__lessons") or section
            for index_in_section, lesson_el in enumerate(
                lesson_container.select(".vod-lesson[data-lesson-id]")
            ):
                lesson_id = lesson_el["data-lesson-id"]
                lesson_title_el = lesson_el.select_one(".vod-lesson__title")
                lesson_title = (
                    lesson_title_el.get_text(strip=True) if lesson_title_el else ""
                )
                duration_el = lesson_el.select_one(".vod-lesson__duration")
                duration_sec = (
                    self._parse_duration(duration_el.get_text(strip=True))
                    if duration_el
                    else None
                )
                lessons.append(
                    Lesson(
                        lesson_id=lesson_id,
                        title=lesson_title,
                        duration_sec=duration_sec,
                        section_index=section_index,
                        section_title=section_title,
                        index_in_section=index_in_section,
                        global_index=global_index,
                    )
                )
                global_index += 1
        return lessons

    @staticmethod
    def _parse_duration(text: str) -> int | None:
        parts = text.strip().split(":")
        try:
            parts_int = [int(p) for p in parts]
        except ValueError:
            return None
        if len(parts_int) == 2:
            minutes, seconds = parts_int
            return minutes * 60 + seconds
        if len(parts_int) == 3:
            hours, minutes, seconds = parts_int
            return hours * 3600 + minutes * 60 + seconds
        return None

    def get_video_uid(self, course_id: str, lesson_id: str) -> str | None:
        url = f"{BASE_URL}/lecture/{course_id}/{lesson_id}"
        resp = self.session.get(url, timeout=self.timeout)
        resp.raise_for_status()
        match = VIDEO_UID_RE.search(resp.text)
        return match.group(1) if match else None
