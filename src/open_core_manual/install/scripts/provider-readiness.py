#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, asdict


READY_VALUES = {"1", "true", "yes", "ready", "ok"}


@dataclass(frozen=True)
class ProviderGate:
    gate_id: str
    title: str
    category: str
    required_for: tuple[str, ...]
    env: str
    paid_or_account_reason: str
    no_cost_maximum: str


GATES: tuple[ProviderGate, ...] = (
    ProviderGate(
        gate_id="github_actions_billing",
        title="GitHub Actions billing/startup",
        category="account_or_billing",
        required_for=("public_release", "saas", "enterprise_cloud"),
        env="FLYTO_PROVIDER_GITHUB_ACTIONS_READY",
        paid_or_account_reason="remote checks cannot be release evidence while workflows fail to start",
        no_cost_maximum="run local make verify, frontend builds, and indexer full-scan gates",
    ),
    ProviderGate(
        gate_id="docker_hub_publish",
        title="Docker Hub publish permission",
        category="account_or_billing",
        required_for=("public_release",),
        env="FLYTO_PROVIDER_DOCKER_HUB_READY",
        paid_or_account_reason="official CE images must be pushed by an account allowed to publish the declared repo and tags",
        no_cost_maximum="run Docker build-boundary audits and dry-run multi-arch publishing",
    ),
    ProviderGate(
        gate_id="domain_dns_tls",
        title="Domain, DNS, TLS, support contact",
        category="account_or_billing",
        required_for=("public_release", "saas"),
        env="FLYTO_PROVIDER_DOMAIN_READY",
        paid_or_account_reason="public docs and commercial claims need owned reachable endpoints",
        no_cost_maximum="keep links documented and avoid production availability claims",
    ),
    ProviderGate(
        gate_id="saas_runtime",
        title="SaaS runtime infrastructure",
        category="paid_infrastructure",
        required_for=("saas",),
        env="FLYTO_PROVIDER_SAAS_RUNTIME_READY",
        paid_or_account_reason="hosted engine, worker, frontend, database, object storage, and TLS need infrastructure",
        no_cost_maximum="use local Docker Compose and development tunnel workflows",
    ),
    ProviderGate(
        gate_id="durable_storage_backup",
        title="Durable storage and backup/restore",
        category="paid_infrastructure",
        required_for=("saas", "enterprise_cloud"),
        env="FLYTO_PROVIDER_BACKUP_READY",
        paid_or_account_reason="production evidence and restore drills need durable storage",
        no_cost_maximum="use local Postgres, seed data, and backup/restore dry-run documentation",
    ),
    ProviderGate(
        gate_id="enterprise_license_signing",
        title="Enterprise license signing",
        category="account_or_billing",
        required_for=("enterprise_cloud", "enterprise_airgap"),
        env="FLYTO_PROVIDER_LICENSE_SIGNING_READY",
        paid_or_account_reason="premium/on-prem execution requires signed entitlement and revocation policy",
        no_cost_maximum="use edition contracts, locked UI state, and local enterprise simulation",
    ),
    ProviderGate(
        gate_id="private_registry_airgap",
        title="Private registry or airgap image distribution",
        category="paid_infrastructure",
        required_for=("enterprise_airgap",),
        env="FLYTO_PROVIDER_PRIVATE_REGISTRY_READY",
        paid_or_account_reason="airgap/private deployments need controlled image provenance",
        no_cost_maximum="use CE public image flow and local private-tag dry-runs",
    ),
    ProviderGate(
        gate_id="commercial_threat_intel",
        title="Commercial threat-intelligence feeds",
        category="paid_data",
        required_for=("enterprise_cloud", "enterprise_airgap"),
        env="FLYTO_PROVIDER_THREAT_INTEL_READY",
        paid_or_account_reason="darkweb, stealer-log, phishing, malware, actor, and ransomware datasets are paid or long-running collection assets",
        no_cost_maximum="use public/feed-backed lookups and demo seed evidence",
    ),
    ProviderGate(
        gate_id="cloud_test_tenants",
        title="Cloud/provider test tenants",
        category="paid_infrastructure",
        required_for=("enterprise_cloud",),
        env="FLYTO_PROVIDER_CLOUD_TEST_TENANTS_READY",
        paid_or_account_reason="live cloud/container/runtime/VM remediation needs safe customer-like targets",
        no_cost_maximum="use contract tests, mock connectors, and local runner evidence",
    ),
    ProviderGate(
        gate_id="support_sla_legal_hold",
        title="Support SLA and legal hold process",
        category="account_or_billing",
        required_for=("enterprise_cloud", "enterprise_airgap"),
        env="FLYTO_PROVIDER_SUPPORT_SLA_READY",
        paid_or_account_reason="enterprise contracts need support obligations and retention/legal-hold proof",
        no_cost_maximum="use docs, audit export, and local retention simulation",
    ),
)


def ready(env: str) -> bool:
    if os.getenv("FLYTO_PROVIDER_ALL_READY", "").strip().lower() in READY_VALUES:
        return True
    return os.getenv(env, "").strip().lower() in READY_VALUES


def gates_for(scope: str) -> list[ProviderGate]:
    if scope == "ce_local":
        return []
    return [gate for gate in GATES if scope in gate.required_for]


def build_report(scope: str) -> dict[str, object]:
    gates = gates_for(scope)
    rows = []
    blockers = []
    for gate in gates:
        is_ready = ready(gate.env)
        row = asdict(gate)
        row["ready"] = is_ready
        row["status"] = "ready" if is_ready else "provider_blocked"
        rows.append(row)
        if not is_ready:
            blockers.append(row)

    if scope == "ce_local":
        verdict = "READY_FOR_LOCAL_CE"
    elif blockers:
        verdict = "CODE_READY_PROVIDER_BLOCKED"
    else:
        verdict = "READY_FOR_RELEASE"

    return {
        "schema": "flyto.warroom.provider-readiness.v1",
        "scope": scope,
        "verdict": verdict,
        "blocked_count": len(blockers),
        "gates": rows,
        "blockers": blockers,
        "no_cost_maximum": [
            "make audit",
            "make demo-seed-dry-run",
            "python3 install/scripts/verify-docker-images.py --dry-run",
            "frontend build, i18n hardcoded audit, and visual-system audit",
            "flyto-indexer verify --full-scan --strict",
        ],
    }


def print_text(report: dict[str, object]) -> None:
    print(f"verdict: {report['verdict']}")
    print(f"scope: {report['scope']}")
    print(f"blocked_count: {report['blocked_count']}")
    blockers = report["blockers"]
    if isinstance(blockers, list) and blockers:
        print("provider blockers:")
        for item in blockers:
            print(f"- {item['gate_id']}: {item['title']}")
            print(f"  reason: {item['paid_or_account_reason']}")
            print(f"  no_cost_maximum: {item['no_cost_maximum']}")
    print("no-cost maximum commands:")
    for cmd in report["no_cost_maximum"]:
        print(f"- {cmd}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check Flyto2 Warroom provider/payment readiness")
    parser.add_argument(
        "--scope",
        choices=("ce_local", "public_release", "saas", "enterprise_cloud", "enterprise_airgap"),
        default="public_release",
    )
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    parser.add_argument(
        "--allow-provider-blocked",
        action="store_true",
        help="return 0 when only account/provider gates are blocked",
    )
    args = parser.parse_args(argv)

    report = build_report(args.scope)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print_text(report)

    if report["verdict"] == "CODE_READY_PROVIDER_BLOCKED" and not args.allow_provider_blocked:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
