#!/usr/bin/env python3
from __future__ import annotations

import argparse
import getpass
import json
from pathlib import Path
import sys
from urllib import error, request


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BUNDLE = ROOT / "install/demo-workspace.json"
REQUIRED_SURFACES = {
    "byo",
    "attack_path",
    "validation",
    "code",
    "container",
    "cloud",
    "external",
    "evidence",
    "autofix",
}


def load_bundle(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    surfaces = {item.get("id") for item in payload.get("surfaces", [])}
    missing = sorted(REQUIRED_SURFACES - surfaces)
    if missing:
        raise SystemExit("demo bundle missing surfaces: " + ", ".join(missing))
    if len(payload.get("evidence_pack", [])) < 5:
        raise SystemExit("demo bundle must include at least five evidence entries")
    return payload


def http_json(method: str, url: str, payload: dict | None = None, token: str = "") -> dict:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = "Bearer " + token
    req = request.Request(url, data=body, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise SystemExit(f"{method} {url} failed: HTTP {exc.code}: {detail}") from exc
    except error.URLError as exc:
        raise SystemExit(f"{method} {url} failed: {exc.reason}") from exc


def render_markdown(bundle: dict) -> str:
    lines = [
        "# Flyto2 Warroom CE Demo Evidence Pack",
        "",
        bundle["description"],
        "",
        "## Surfaces",
        "",
    ]
    for item in bundle["surfaces"]:
        lines.extend([
            f"### {item['title']}",
            "",
            f"- CE: {item['ce_path']}",
            f"- Enterprise: {item['enterprise_path']}",
            "",
        ])
    lines.extend([
        "## BYO Offensive Validation Loop",
        "",
        "Bring your own tools. Flyto2 turns their findings into verified attack paths, pentest evidence, and red-team scenarios.",
        "",
        "```text",
        "Findings -> Attack Paths -> Offensive Validation -> Evidence -> Remediation",
        "```",
        "",
        "## Evidence Pack",
        "",
    ])
    for ev in bundle["evidence_pack"]:
        lines.append(f"- `{ev['id']}` [{ev['surface']}] {ev['kind']}: {ev['claim']} ({ev['status']})")
    lines.extend([
        "",
        "## Claim Guardrails",
        "",
        "This demo does not claim guaranteed coverage, 100% AutoFix success, full replacement of any scanner, or replacement of the customer's existing security stack.",
        "It demonstrates BYO offensive validation, evidence-backed remediation, and the CE/Enterprise boundary.",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed the Flyto2 Warroom CE demo workspace")
    parser.add_argument("--engine-url", default="http://localhost:8080")
    parser.add_argument("--email", default="")
    parser.add_argument("--password-stdin", action="store_true")
    parser.add_argument("--bundle", default=str(DEFAULT_BUNDLE))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    bundle = load_bundle(Path(args.bundle))
    markdown = render_markdown(bundle)
    if args.dry_run:
        print("ok: demo bundle is valid")
        print("surfaces: " + ", ".join(sorted(REQUIRED_SURFACES)))
        print("markdown_bytes: " + str(len(markdown.encode("utf-8"))))
        return 0

    email = args.email or input("Local admin email: ").strip()
    if not email:
        raise SystemExit("email is required")
    if args.password_stdin:
        password = sys.stdin.readline().rstrip("\n")
    else:
        password = getpass.getpass("Local admin password: ")
    if not password:
        raise SystemExit("password is required")

    base = args.engine_url.rstrip("/")
    login = http_json("POST", base + "/api/v1/auth/local/login", {"email": email, "password": password})
    token = login.get("accessToken") or login.get("access_token")
    if not token:
        raise SystemExit("local login succeeded but no access token was returned")

    workspace = http_json("POST", base + "/api/v1/workspaces", {"name": bundle["name"]}, token)
    workspace_id = workspace.get("id")
    if not workspace_id:
        raise SystemExit("workspace create returned no id")

    note = http_json(
        "POST",
        base + "/api/v1/notes",
        {
            "workspaceId": workspace_id,
            "title": "Flyto2 Warroom CE Demo Evidence Pack",
            "content": markdown,
        },
        token,
    )
    print("ok: seeded demo workspace")
    print("workspace_id: " + workspace_id)
    if note.get("resource", {}).get("id"):
        print("evidence_resource_id: " + note["resource"]["id"])
    print("open: http://localhost:8088")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
