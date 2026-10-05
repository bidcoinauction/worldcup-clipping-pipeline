"""Console-script entry points (installed via pyproject.toml).

Thin wrappers around the existing ``scripts/...`` mains so the installed CLI
mirrors the repository commands. ``python scripts/...`` continues to work.
"""

from __future__ import annotations


def main_init() -> int:
    from scripts.init_project import main
    return main()


def main_doctor() -> int:
    from scripts.doctor import main
    return main()


def main_console() -> int:
    from scripts.console import main
    return main()


def main_release_check() -> int:
    from scripts.release_check import main
    return main()


def main_smoke() -> int:
    from scripts.smoke_real_match import main
    return main()