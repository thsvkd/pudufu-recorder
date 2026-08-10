"""프드프 강의 1.5배속 다운로더 - GUI 엔트리 포인트.

실행: .venv/bin/python main.py
데모 모드(코어 없이 화면만 확인): PUDUFU_UI_DEMO=1 .venv/bin/python main.py
"""

from __future__ import annotations

import os

import flet as ft
from dotenv import load_dotenv

from ui.app import App

load_dotenv()


def main(page: ft.Page) -> None:
    demo = os.environ.get("PUDUFU_UI_DEMO") == "1"
    App(page, demo=demo)


if __name__ == "__main__":
    ft.run(main)
