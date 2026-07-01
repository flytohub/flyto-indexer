from pathlib import Path

import pytest


SLOW_BY_FILE = {
    "test_incremental_fuzz.py": ("slow", "fuzz"),
    "test_lsp_stress.py": ("slow", "stress"),
    "test_mcp_integration.py": ("slow", "integration"),
    "test_race_conditions.py": ("slow", "stress"),
}


def pytest_collection_modifyitems(items):
    for item in items:
        markers = SLOW_BY_FILE.get(Path(str(item.fspath)).name)
        if not markers:
            continue
        for marker in markers:
            item.add_marker(getattr(pytest.mark, marker))
