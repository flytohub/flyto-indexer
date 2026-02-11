#!/usr/bin/env python3
"""
Test dead code detection.
"""

import sys
from pathlib import Path

# Set up paths
project_root = Path(__file__).parent.parent
src_path = project_root / "src"
sys.path.insert(0, str(src_path))

from analyzer.dead_code import DeadCodeDetector

# Test projects
FLYTOHUB_ROOT = Path("/path/to/your/projects")

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
