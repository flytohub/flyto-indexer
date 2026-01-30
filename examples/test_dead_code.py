#!/usr/bin/env python3
"""
測試死碼偵測
"""

import sys
from pathlib import Path

# 設定路徑
project_root = Path(__file__).parent.parent
src_path = project_root / "src"
sys.path.insert(0, str(src_path))

from analyzer.dead_code import DeadCodeDetector

# 測試專案
FLYTOHUB_ROOT = Path("/Library/其他專案/flytohub")

def main():
    projects = [
        "flyto-core",
        "flyto-cloud",
    ]

    for project_name in projects:
        project_path = FLYTOHUB_ROOT / project_name
        if not project_path.exists():
            continue

        print(f"\n\n{'#' * 70}")
        print(f"# PROJECT: {project_name}")
        print(f"{'#' * 70}")

        detector = DeadCodeDetector(project_path)
        report = detector.analyze()
        detector.print_report(report)


if __name__ == "__main__":
    main()
