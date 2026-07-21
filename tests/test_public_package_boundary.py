from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]

FORBIDDEN_PATHS = (
    "src/flyto2_product_gate.py",
    "src/flyto2_release_packet.py",
    "src/flyto2_open_core.py",
    "scripts/write_continuous_release_evidence.py",
    "scripts/write_product_verification_evidence.py",
)

FORBIDDEN_COMMANDS = (
    "flyto2-product-gate",
    "flyto2-release-packet",
    "flyto2-open-core-audit",
    "flyto2-open-core-export",
    "flyto2-memory-bootstrap",
)


def test_public_tree_excludes_product_release_policy():
    present = [relative for relative in FORBIDDEN_PATHS if (ROOT / relative).exists()]
    product_config = ROOT / "config" / "flyto2"
    if product_config.exists() and any(product_config.iterdir()):
        present.append("config/flyto2/*")
    assert present == []


def test_public_cli_excludes_product_release_commands():
    result = subprocess.run(
        [sys.executable, "-m", "src.cli", "--help"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    leaked = [command for command in FORBIDDEN_COMMANDS if command in result.stdout]
    assert leaked == []
