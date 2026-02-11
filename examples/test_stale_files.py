#!/usr/bin/env python3
"""Test stale file detection."""

import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

from analyzer.stale_files import StaleFileDetector

FLYTOHUB_ROOT = Path("/path/to/your/projects")

def main():
    # Only test flyto-cloud (has complete git history)
    project_path = FLYTOHUB_ROOT / "flyto-cloud"

    print(f"\n{'#' * 70}")
    print(f"# Stale Files Analysis: flyto-cloud")
    print(f"{'#' * 70}")

    detector = StaleFileDetector(
        project_path,
        stale_days=90,  # Not modified for 3 months
    )
    report = detector.analyze()
    detector.print_report(report)


if __name__ == "__main__":
    main()
