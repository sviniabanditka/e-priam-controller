"""Cybex e-Priam BLE controller — entrypoint."""

from __future__ import annotations

import argparse
from pathlib import Path

from priam_tui import PriamApp


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Cybex e-Priam TUI")
    ap.add_argument(
        "--log-file",
        type=Path,
        default=None,
        help="Append raw notifications and events to this file.",
    )
    return ap.parse_args()


def main() -> None:
    args = parse_args()
    PriamApp(log_file=args.log_file).run()


if __name__ == "__main__":
    main()
