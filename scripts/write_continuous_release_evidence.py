#!/usr/bin/env python3
"""Write Flyto2 continuous-release evidence digests.

This helper does not replace product-specific tests or browser smokes. It turns
the current release-packet source evidence into fresh, timestamped digest files
so the release gate can distinguish missing proof from proof that contains real
P0/P1 findings.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.flyto2_release_packet import (  # noqa: E402
    DEFAULT_EVIDENCE_GATES,
    DEFAULT_MANIFEST,
    ReleasePacketOptions,
    format_release_packet,
    parse_run_start,
    run_release_packet,
)

DEFAULT_HEALTH_REPORT = REPO_ROOT / "config" / "flyto2" / "health-baseline-2026-06-21.json"

DIGEST_FILES = {
    "architecture_dependency_map": ["architecture-map.md"],
    "billing_entitlement_audit": ["billing-entitlement.md"],
    "rbac_tenant_isolation_audit": ["rbac-tenant-isolation.md"],
    "product_state_machine_audit": ["state-machine.md"],
    "enterprise_airgap_open_core_audit": ["enterprise-airgap.md"],
    "geo_aeo_seo_ai_crawler_audit": ["geo-ai-crawler.md"],
    "i18n_multilingual_audit": ["i18n.md"],
    "security_performance_cicd_audit": ["security-performance.md"],
    "e2e_browser_smoke_matrix": ["browser-smoke.json", "browser-smoke.md"],
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _deliverable_by_id(packet: dict[str, Any], deliverable_id: str) -> dict[str, Any]:
    for item in packet.get("deliverables", []):
        if item.get("id") == deliverable_id:
            return item
    return {"id": deliverable_id, "status": "missing"}


def _source_summary(deliverable: dict[str, Any]) -> dict[str, Any]:
    evidence = deliverable.get("evidence") if isinstance(deliverable.get("evidence"), list) else []
    missing = deliverable.get("missing_evidence")
    if not isinstance(missing, list):
        missing = []
    return {
        "required_count": len(evidence),
        "present_count": len(evidence) - len(missing),
        "missing": missing,
    }


def _fresh_summary(deliverable: dict[str, Any]) -> list[dict[str, Any]]:
    fresh = deliverable.get("fresh_evidence")
    if not isinstance(fresh, list):
        return []
    return [
        {
            "path": item.get("path"),
            "exists": item.get("exists"),
            "fresh": item.get("fresh"),
            "reason": item.get("reason", ""),
            "contract_finding_counts": item.get("contract_finding_counts", {}),
            "contract_blocking_findings": item.get("contract_blocking_findings", []),
        }
        for item in fresh
        if isinstance(item, dict)
    ]


def _markdown_digest(packet: dict[str, Any], deliverable: dict[str, Any], generated_at: str) -> str:
    source = _source_summary(deliverable)
    fresh = _fresh_summary(deliverable)
    lines = [
        f"# {deliverable.get('title', deliverable.get('id', 'Release evidence'))}",
        "",
        f"Generated at: `{generated_at}`",
        f"Release-packet status at write time: `{deliverable.get('status', 'unknown')}`",
        "",
        "## Source Evidence",
        "",
        f"- Required files: `{source['required_count']}`",
        f"- Present files: `{source['present_count']}`",
    ]
    if source["missing"]:
        lines.append(f"- Missing files: `{', '.join(source['missing'])}`")
    else:
        lines.append("- Missing files: `none`")
    if fresh:
        lines.extend(["", "## Fresh Evidence Inputs", ""])
        for item in fresh:
            lines.append(
                f"- `{item.get('path')}`: exists={item.get('exists')}, "
                f"fresh={item.get('fresh')}, reason=`{item.get('reason', '') or 'none'}`"
            )
            if item.get("contract_blocking_findings"):
                lines.append(f"  blocking findings: `{item['contract_blocking_findings']}`")
    lines.extend(
        [
            "",
            "## Score Limitations",
            "",
        ]
    )
    for limitation in packet.get("score_limitations", []):
        lines.append(f"- {limitation}")
    lines.append("")
    return "\n".join(lines)


def _write_workspace_matrix(packet: dict[str, Any], output_dir: Path, generated_at: str) -> None:
    inventory = packet.get("inventory") if isinstance(packet.get("inventory"), dict) else {}
    matrix = {
        "generated_at": generated_at,
        "repo_count": len(inventory),
        "manifest_repo_count": packet.get("manifest_repo_count"),
        "product_gate_verdict": packet.get("product_gate_verdict"),
        "release_packet_verdict": packet.get("verdict"),
        "repos": inventory,
    }
    _write_json(output_dir / "workspace-matrix.json", matrix)

    lines = [
        "# Flyto2 Workspace Matrix",
        "",
        f"Generated at: `{generated_at}`",
        f"Repos discovered: `{len(inventory)}` / manifest `{packet.get('manifest_repo_count')}`",
        f"Product gate verdict: `{packet.get('product_gate_verdict')}`",
        f"Release packet verdict at write time: `{packet.get('verdict')}`",
        "",
        "| Repo | Status | Branch | Dirty | Core | Role | Product Lines |",
        "| --- | --- | --- | ---: | --- | --- | --- |",
    ]
    for name, repo in sorted(inventory.items()):
        lines.append(
            "| "
            + " | ".join(
                [
                    name,
                    str(repo.get("status", "")),
                    str(repo.get("branch", "")),
                    str(len(repo.get("dirty_files") or [])),
                    "yes" if repo.get("core") else "no",
                    str(repo.get("role", "")),
                    ", ".join(repo.get("product_lines") or []),
                ]
            )
            + " |"
        )
    lines.append("")
    _write_text(output_dir / "workspace-matrix.md", "\n".join(lines))


def _write_browser_smoke(packet: dict[str, Any], output_dir: Path, generated_at: str) -> None:
    deliverable = _deliverable_by_id(packet, "e2e_browser_smoke_matrix")
    data = {
        "generated_at": generated_at,
        "deliverable": "e2e_browser_smoke_matrix",
        "status": deliverable.get("status"),
        "source_evidence": _source_summary(deliverable),
        "fresh_evidence_inputs": _fresh_summary(deliverable),
        "residual": "Digest only. It does not replace rerunning authenticated browser smoke.",
    }
    _write_json(output_dir / "browser-smoke.json", data)
    _write_text(output_dir / "browser-smoke.md", _markdown_digest(packet, deliverable, generated_at))


def write_digests(
    *,
    workspace: Path,
    output_dir: Path,
    manifest_path: Path,
    evidence_gate_path: Path,
    health_report_path: Path | None,
    run_start: datetime | None,
    generated_at: str,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    packet = run_release_packet(
        ReleasePacketOptions(
            workspace=workspace,
            manifest_path=manifest_path,
            evidence_gate_path=evidence_gate_path,
            health_report_path=health_report_path,
            fresh_evidence_dir=output_dir,
            require_fresh=False,
            run_start=run_start,
        )
    )

    _write_workspace_matrix(packet, output_dir, generated_at)
    for deliverable_id, files in DIGEST_FILES.items():
        if deliverable_id == "e2e_browser_smoke_matrix":
            _write_browser_smoke(packet, output_dir, generated_at)
            continue
        deliverable = _deliverable_by_id(packet, deliverable_id)
        for filename in files:
            _write_text(output_dir / filename, _markdown_digest(packet, deliverable, generated_at))

    _write_json(output_dir / "release-packet.json", {**packet, "generated_at": generated_at})
    _write_text(output_dir / "release-packet.md", format_release_packet(packet) + "\n")
    return packet


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--evidence-gates", type=Path, default=DEFAULT_EVIDENCE_GATES)
    parser.add_argument("--health-report", type=Path, default=DEFAULT_HEALTH_REPORT)
    parser.add_argument("--no-health-report", action="store_true")
    parser.add_argument("--run-start")
    parser.add_argument("--generated-at", default=_now_iso())
    args = parser.parse_args()

    health_report = None if args.no_health_report else args.health_report
    packet = write_digests(
        workspace=args.workspace,
        output_dir=args.output_dir,
        manifest_path=args.manifest,
        evidence_gate_path=args.evidence_gates,
        health_report_path=health_report,
        run_start=parse_run_start(args.run_start),
        generated_at=args.generated_at,
    )
    print(f"Continuous release evidence written: {args.output_dir}")
    print(f"Release packet verdict at write time: {packet.get('verdict')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
