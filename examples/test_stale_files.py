#!/usr/bin/env python3
"""測試陳舊檔案偵測"""

import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

from analyzer.stale_files import StaleFileDetector

FLYTOHUB_ROOT = Path("/Library/其他專案/flytohub")

def main():
    # 只測試 flyto-cloud（有完整 git 歷史）
    project_path = FLYTOHUB_ROOT / "flyto-cloud"

    print(f"\n{'#' * 70}")
    print(f"# Stale Files Analysis: flyto-cloud")
    print(f"{'#' * 70}")

    detector = StaleFileDetector(
        project_path,
        stale_days=90,  # 3 個月沒動過
    )
    report = detector.analyze()
    detector.print_report(report)


if __name__ == "__main__":
    main()
