#!/usr/bin/env python3
"""
Test code quality analysis.

1. Complexity analysis - find overly complex functions
2. Test coverage - find modules without tests
"""

import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

from analyzer.complexity import ComplexityAnalyzer
from analyzer.coverage import CoverageAnalyzer

FLYTOHUB_ROOT = Path("/path/to/your/projects")


def test_complexity(project_name: str):
    """Test complexity analysis"""
    project_path = FLYTOHUB_ROOT / project_name

    if not project_path.exists():
        print(f"Project not found: {project_name}")
        return

    print(f"\n{'#' * 70}")
    print(f"# Complexity Analysis: {project_name}")
    print(f"{'#' * 70}")

    analyzer = ComplexityAnalyzer(
        project_path,
        max_lines=50,
        max_depth=4,
        max_params=5,
        max_branches=10,
    )
    report = analyzer.analyze()
    analyzer.print_report(report)

    return report


def test_coverage(project_name: str):
    """Test coverage analysis"""
    project_path = FLYTOHUB_ROOT / project_name

    if not project_path.exists():
        print(f"Project not found: {project_name}")
        return

    print(f"\n{'#' * 70}")
    print(f"# Test Coverage Analysis: {project_name}")
    print(f"{'#' * 70}")

    analyzer = CoverageAnalyzer(project_path)
    report = analyzer.analyze()
    analyzer.print_report(report)

    return report


def main():
    projects = ["flyto-core", "flyto-cloud"]

    for project in projects:
        test_complexity(project)
        test_coverage(project)


if __name__ == "__main__":
    main()
