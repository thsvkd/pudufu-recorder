"""ffmpeg 를 사용할 수 없을 때 보여주는 안내 화면."""

from __future__ import annotations

from typing import TYPE_CHECKING

import flet as ft

if TYPE_CHECKING:
    from ui.app import App


def build_ffmpeg_missing_view(app: App) -> ft.Control:
    return ft.Container(
        content=ft.Column(
            [
                ft.Icon(ft.Icons.WARNING_AMBER, size=40, color=ft.Colors.ERROR),
                ft.Text("ffmpeg를 사용할 수 없습니다", size=18, weight=ft.FontWeight.BOLD),
                ft.Text(
                    "영상을 1.5배속으로 변환하려면 ffmpeg가 필요합니다.\n"
                    "터미널을 열고 아래 명령어를 실행해 설치한 뒤 앱을 다시 시작해주세요.",
                    text_align=ft.TextAlign.CENTER,
                    color=ft.Colors.ON_SURFACE_VARIANT,
                ),
                ft.Container(
                    content=ft.Text(
                        "brew install ffmpeg", selectable=True, font_family="monospace"
                    ),
                    padding=12,
                    border_radius=8,
                    bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
                ),
                ft.ElevatedButton(
                    "다시 확인",
                    icon=ft.Icons.REFRESH,
                    on_click=lambda e: app.page.run_task(app.start),
                ),
            ],
            alignment=ft.MainAxisAlignment.CENTER,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=16,
        ),
        alignment=ft.Alignment.CENTER,
        expand=True,
        padding=32,
    )
