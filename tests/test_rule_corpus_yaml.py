from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_every_built_in_rule_file_is_valid_yaml():
    paths = sorted((ROOT / "config" / "rules").glob("*.yaml"))
    assert paths

    for path in paths:
        parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert isinstance(parsed, dict), path
