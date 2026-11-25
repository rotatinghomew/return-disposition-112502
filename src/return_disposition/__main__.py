"""命令行入口：python -m return_disposition [资料目录]"""

from __future__ import annotations

import sys
from pathlib import Path

from .pipeline import format_report, run


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    root = Path(args[0]) if args else Path("data")
    report = run(root)
    print(format_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
