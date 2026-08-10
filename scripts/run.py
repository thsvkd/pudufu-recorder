#!/usr/bin/env python3
"""앱을 실행한다. 어느 플랫폼에서도 동작한다.

인자 없이 실행하거나 ``--gui``를 붙이면 GUI 창 모드로 실행한다(로그인·강의 선택을 창에서
하므로 인자가 필요 없다). 인자를 주면 CLI 모드로, 그 인자를 그대로 ``cli.py``에 전달한다.

이 프로젝트에는 ``[project.scripts]`` 콘솔 스크립트가 없어(원본 naver-post-crawler와 다른
점이다) 진입점 모듈을 직접 실행한다.

사용 예:
    python scripts/run.py                              # GUI 창
    python scripts/run.py --gui                        # GUI 창(명시적)
    python scripts/run.py --list                       # CLI: 내 강의 목록
    python scripts/run.py --course-id 686 --speed 1.5  # CLI: 강의 전체 받기
"""

from __future__ import annotations

import sys

from _common import require_uv, run


def main() -> int:
    require_uv()
    args = sys.argv[1:]

    # 인자 없이 실행하거나 --gui면 GUI 창 모드.
    if not args or "--gui" in args:
        return run(["uv", "run", "python", "main.py"])

    # 인자가 있으면 CLI 모드 — 옵션을 그대로 전달한다.
    return run(["uv", "run", "python", "cli.py", *args])


if __name__ == "__main__":
    raise SystemExit(main())
