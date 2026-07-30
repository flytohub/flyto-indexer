import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from secret_scanner import scan_secrets


def test_secret_scanner_skips_virtualenv_variants(tmp_path: Path):
    vendor_file = tmp_path / "pkg" / ".venv311" / "lib" / "python3.11" / "site-packages" / "demo.py"
    vendor_file.parent.mkdir(parents=True)
    vendor_file.write_text('secret_key = "abcdefghijklmnopqrstuvwxyz123456"\n', encoding="utf-8")

    result = scan_secrets(tmp_path)

    assert result.total_findings == 0


def test_secret_scanner_skips_mock_utility_paths(tmp_path: Path):
    mock_file = tmp_path / "src" / "@mock-utils" / "api" / "authApi.ts"
    mock_file.parent.mkdir(parents=True)
    mock_file.write_text('const secretKey = "abcdefghijklmnopqrstuvwxyz123456";\n', encoding="utf-8")

    result = scan_secrets(tmp_path)

    assert result.total_findings == 0


def test_secret_scanner_skips_nested_gitleaks_config(tmp_path: Path):
    config = tmp_path / "repo" / ".gitleaks.toml"
    config.parent.mkdir()
    config.write_text('regex = "AIzaabcdefghijklmnopqrstuvwxyz123456789"\n', encoding="utf-8")

    result = scan_secrets(tmp_path)

    assert result.total_findings == 0


def test_secret_scanner_keeps_tracked_env_files(monkeypatch, tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text('PASSWORD="abcdefghijklmnopqrstuvwxyz123456"\n', encoding="utf-8")
    monkeypatch.setattr("secret_scanner._is_tracked_by_git", lambda path: True)

    result = scan_secrets(tmp_path)

    assert result.total_findings == 1


def test_secret_scanner_skips_untracked_env_files(monkeypatch, tmp_path: Path):
    env_file = tmp_path / ".env.local"
    env_file.write_text('SECRET_KEY="abcdefghijklmnopqrstuvwxyz123456"\n', encoding="utf-8")
    monkeypatch.setattr("secret_scanner._is_tracked_by_git", lambda path: False)

    result = scan_secrets(tmp_path)

    assert result.total_findings == 0


def test_secret_scanner_skips_public_firebase_client_config(tmp_path: Path):
    firebase = tmp_path / "lib" / "firebase.ts"
    firebase.parent.mkdir()
    firebase.write_text("export const config = { apiKey: 'AIzaabcdefghijklmnopqrstuvwxyz123456789' };\n", encoding="utf-8")
    flutter = tmp_path / "lib" / "firebase_options.dart"
    flutter.write_text("apiKey: 'AIzaabcdefghijklmnopqrstuvwxyz123456789',\n", encoding="utf-8")

    result = scan_secrets(tmp_path)

    assert result.total_findings == 0


def test_secret_scanner_skips_security_rule_labels(tmp_path: Path):
    source = tmp_path / "security.py"
    source.write_text(
        'VulnerabilityType.HARDCODED_SECRET: "Move secrets to environment variables or secret manager.",\n',
        encoding="utf-8",
    )

    result = scan_secrets(tmp_path)

    assert result.total_findings == 0


def test_secret_scanner_skips_private_key_pattern_descriptions(tmp_path: Path):
    audit = tmp_path / "SECURITY_AUDIT_2026-06-05_PASS2_fixgaps.json"
    audit.write_text(
        '"fix": "Extend _VALUE_PATTERNS: -----BEGIN ...PRIVATE KEY----- and cookie values.",\n',
        encoding="utf-8",
    )

    result = scan_secrets(tmp_path)

    assert result.total_findings == 0


def test_secret_scanner_keeps_actual_private_key_headers(tmp_path: Path):
    source = tmp_path / "settings.py"
    source.write_text('PRIVATE_KEY = "-----BEGIN PRIVATE KEY-----"\n', encoding="utf-8")

    result = scan_secrets(tmp_path)

    assert result.total_findings == 1
    assert result.findings[0].pattern == "private_key"
    evidence = result.findings[0].to_dict()
    assert evidence["schema"] == "finding-evidence.v1"
    assert evidence["confidence"]["level"] == "high"
    assert evidence["suppression"]["status"] == "active"
