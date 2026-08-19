#!/usr/bin/env python3
"""Write Flyto2 public-site verification evidence.

The default mode performs live DNS/TLS/HTTP probes and fails closed when browser
render proof is not supplied. Use --fixture-pass only for unit tests and local
contract validation fixtures.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import socket
import ssl
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

CONTRACT = "flyto2.public_site_verification.v1"
ROUTES = [
    "/",
    "/robots.txt",
    "/sitemap.xml",
    "/llms.txt",
    "/llms-full.txt",
    "/pricing/",
    "/security/",
    "/enterprise/",
    "/airgap/",
    "/open-source/",
    "/compare/",
    "/api-docs/",
    "/trust/",
    "/docs/",
    "/blog/",
    "/changelog/",
]

SEO_SIGNALS = [
    "title",
    "meta_description",
    "canonical",
    "open_graph",
    "structured_data",
    "llms_txt",
    "sitemap",
    "robots",
    "server_rendered_content",
]

CRITICAL_PATHS = {"/", "/robots.txt", "/sitemap.xml", "/llms.txt", "/llms-full.txt"}

CRAWLER_USER_AGENTS = [
    "Mozilla/5.0 Flyto2ReleaseProbe/1.0",
    "Googlebot",
    "Bingbot",
    "GPTBot",
    "ChatGPT-User",
    "OAI-SearchBot",
    "ClaudeBot",
    "Claude-SearchBot",
    "Claude-User",
    "Claude-Web",
    "PerplexityBot",
    "Perplexity-User",
]

TRAINING_USER_AGENTS = {
    "GPTBot",
    "ClaudeBot",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_url(base_url: str, route: str) -> str:
    return urljoin(base_url.rstrip("/") + "/", route.lstrip("/"))


def probe_dns(host: str, family: socket.AddressFamily, timeout: float) -> dict[str, Any]:
    socket.setdefaulttimeout(timeout)
    family_name = "ipv6" if family == socket.AF_INET6 else "ipv4"
    try:
        infos = socket.getaddrinfo(host, 443, family, socket.SOCK_STREAM)
    except OSError as exc:
        return {"host": host, "family": family_name, "ok": False, "error": str(exc)}
    addresses = sorted({info[4][0] for info in infos})
    return {"host": host, "family": family_name, "ok": bool(addresses), "addresses": addresses}


def probe_tls(host: str, timeout: float) -> dict[str, Any]:
    try:
        context = ssl.create_default_context()
        # create_default_context() still permits TLS 1.0/1.1 on older runtimes;
        # this evidence script must not report a handshake it would refuse.
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        with socket.create_connection((host, 443), timeout=timeout) as sock, context.wrap_socket(
            sock,
            server_hostname=host,
        ) as tls:
            cert = tls.getpeercert()
            not_after = cert.get("notAfter", "") if isinstance(cert, dict) else ""
            return {
                "host": host,
                "ok": True,
                "protocol": tls.version(),
                "cipher": tls.cipher()[0] if tls.cipher() else "",
                "not_after": not_after,
            }
    except OSError as exc:
        return {"host": host, "ok": False, "error": str(exc)}
    except ssl.SSLError as exc:
        return {"host": host, "ok": False, "error": str(exc)}


def extract_html_signals(body: str) -> dict[str, bool]:
    normalized = body.lower()
    return {
        "title": "<title" in normalized,
        "meta_description": "name=\"description\"" in normalized or "name='description'" in normalized,
        "canonical": "rel=\"canonical\"" in normalized or "rel='canonical'" in normalized,
        "open_graph": "property=\"og:" in normalized or "property='og:" in normalized,
        "structured_data": "application/ld+json" in normalized,
        "server_rendered_content": len(normalized.strip()) > 300,
    }


def probe_route(url: str, user_agent: str, timeout: float) -> dict[str, Any]:
    request = Request(url, headers={"User-Agent": user_agent, "Accept": "text/html,*/*"})
    try:
        with urlopen(request, timeout=timeout) as response:
            status = int(response.getcode())
            body = response.read(200_000).decode("utf-8", errors="replace")
            return {
                "url": url,
                "path": urlparse(url).path or "/",
                "user_agent": user_agent,
                "status": status,
                "final_status": status,
                "final_url": response.geturl(),
                "ok": 200 <= status < 400,
                "html_signals": extract_html_signals(body),
                "body_sample": body[:4096],
            }
    except HTTPError as exc:
        body = ""
        try:
            body = exc.read(4096).decode("utf-8", errors="replace")
        except Exception:
            body = ""
        return {
            "url": url,
            "path": urlparse(url).path or "/",
            "user_agent": user_agent,
            "status": int(exc.code),
            "final_status": int(exc.code),
            "ok": False,
            "error": str(exc),
            "body_sample": body,
        }
    except TimeoutError as exc:
        return {
            "url": url,
            "path": urlparse(url).path or "/",
            "user_agent": user_agent,
            "ok": False,
            "timed_out": True,
            "error": str(exc) or "timeout",
        }
    except (URLError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        text = str(reason)
        return {
            "url": url,
            "path": urlparse(url).path or "/",
            "user_agent": user_agent,
            "ok": False,
            "timed_out": "timed out" in text.lower(),
            "error": text,
        }


def build_seo_geo_matrix(route_matrix: list[dict[str, Any]]) -> dict[str, bool]:
    by_path = {item.get("path"): item for item in route_matrix if item.get("user_agent") == CRAWLER_USER_AGENTS[0]}
    homepage = by_path.get("/") or {}
    signals = homepage.get("html_signals") if isinstance(homepage.get("html_signals"), dict) else {}
    return {
        "title": signals.get("title") is True,
        "meta_description": signals.get("meta_description") is True,
        "canonical": signals.get("canonical") is True,
        "open_graph": signals.get("open_graph") is True,
        "structured_data": signals.get("structured_data") is True,
        "llms_txt": by_path.get("/llms.txt", {}).get("ok") is True,
        "sitemap": by_path.get("/sitemap.xml", {}).get("ok") is True,
        "robots": by_path.get("/robots.txt", {}).get("ok") is True,
        "server_rendered_content": signals.get("server_rendered_content") is True,
    }


def finding(severity: str, code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"severity": severity, "code": code, "message": message, **extra}


def evaluate(data: dict[str, Any]) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    for item in data["dns_matrix"]:
        if item.get("ok") is False:
            findings.append(finding("P0", "dns_unresolved", f"DNS probe failed for {item.get('host')}", evidence=item))
    for item in data["tls_matrix"]:
        if item.get("ok") is False:
            findings.append(finding("P0", "tls_unavailable", f"TLS probe failed for {item.get('host')}", evidence=item))
    for item in data["route_matrix"]:
        path = str(item.get("path") or "/")
        if item.get("ok") is False or item.get("timed_out") or item.get("error"):
            if item.get("user_agent") in TRAINING_USER_AGENTS:
                continue
            if item.get("user_agent") in CRAWLER_USER_AGENTS[1:]:
                findings.append(finding("P1", "ai_crawler_blocked", f"AI/search crawler route unavailable: {item.get('user_agent')} {path}", evidence=item))
            else:
                severity = "P0" if path in CRITICAL_PATHS else "P1"
                findings.append(finding(severity, "public_route_unavailable", f"Public route unavailable: {path}", evidence=item))
    for route in ROUTES:
        if not any(item.get("path") == route.rstrip("/") or item.get("path") == route for item in data["route_matrix"]):
            severity = "P0" if route in CRITICAL_PATHS else "P1"
            findings.append(finding(severity, "missing_route_observation", f"Required route was not observed: {route}"))
    for item in data["browser_matrix"]:
        if item.get("ok") is not True:
            findings.append(finding("P0", "browser_render_unverified", "Browser render proof is missing or failed", evidence=item))
    for signal, ok in data["seo_geo_matrix"].items():
        if ok is False:
            findings.append(finding("P1", "seo_geo_signal_missing", f"SEO/GEO signal missing: {signal}"))

    data["findings"] = findings
    data["p0_findings"] = sum(1 for item in findings if item["severity"] == "P0")
    data["p1_findings"] = sum(1 for item in findings if item["severity"] == "P1")
    data["ok"] = data["p0_findings"] == 0
    total_routes = len(ROUTES)
    ok_routes = len({
        item.get("path")
        for item in data["route_matrix"]
        if item.get("user_agent") == CRAWLER_USER_AGENTS[0] and item.get("ok") is True
    })
    data["scores"] = {
        "public_route_readiness": round(ok_routes / total_routes, 3),
        "seo_geo_readiness": round(sum(1 for ok in data["seo_geo_matrix"].values() if ok) / len(SEO_SIGNALS), 3),
        "browser_render_readiness": 1.0 if all(item.get("ok") is True for item in data["browser_matrix"]) else 0.0,
    }
    return data


def fixture_pass(base_url: str, generated_at: str) -> dict[str, Any]:
    route_matrix = [
        {"url": canonical_url(base_url, route), "path": route, "user_agent": CRAWLER_USER_AGENTS[0], "status": 200, "final_status": 200, "ok": True}
        for route in ROUTES
    ]
    data = {
        "contract": CONTRACT,
        "generated_at": generated_at,
        "target": base_url.rstrip("/"),
        "evidence_mode": "fixture_pass",
        "dns_matrix": [
            {"host": "flyto2.com", "family": "ipv4", "ok": True, "addresses": ["203.0.113.10"]},
            {"host": "flyto2.com", "family": "ipv6", "ok": True, "addresses": ["2001:db8::10"]},
        ],
        "tls_matrix": [{"host": "flyto2.com", "ok": True, "protocol": "TLSv1.3"}],
        "route_matrix": route_matrix,
        "browser_matrix": [{"path": "/", "status": "ok", "ok": True, "evidence": "fixture"}],
        "seo_geo_matrix": dict.fromkeys(SEO_SIGNALS, True),
    }
    return evaluate(data)


def live_probe(base_url: str, generated_at: str, timeout: float, browser_status: str) -> dict[str, Any]:
    parsed = urlparse(base_url)
    host = parsed.hostname or "flyto2.com"
    hosts = [host]
    if not host.startswith("www."):
        hosts.append(f"www.{host}")
    dns_matrix = []
    for probe_host in hosts:
        dns_matrix.append(probe_dns(probe_host, socket.AF_INET, timeout))
        dns_matrix.append(probe_dns(probe_host, socket.AF_INET6, timeout))
    tls_matrix = [probe_tls(probe_host, timeout) for probe_host in hosts]

    route_matrix = [
        probe_route(canonical_url(base_url, route), CRAWLER_USER_AGENTS[0], timeout)
        for route in ROUTES
    ]
    for ua in CRAWLER_USER_AGENTS[1:]:
        route_matrix.append(probe_route(canonical_url(base_url, "/"), ua, timeout))

    browser_ok = browser_status == "ok"
    browser_matrix = [{"path": "/", "status": browser_status, "ok": browser_ok}]

    data = {
        "contract": CONTRACT,
        "generated_at": generated_at,
        "target": base_url.rstrip("/"),
        "evidence_mode": "live_probe",
        "dns_matrix": dns_matrix,
        "tls_matrix": tls_matrix,
        "route_matrix": route_matrix,
        "browser_matrix": browser_matrix,
        "seo_geo_matrix": build_seo_geo_matrix(route_matrix),
    }
    return evaluate(data)


def write_markdown(data: dict[str, Any], path: Path) -> None:
    lines = [
        "# Flyto2 Public Site Verification",
        "",
        f"- Contract: `{data['contract']}`",
        f"- Generated at: `{data['generated_at']}`",
        f"- Target: `{data['target']}`",
        f"- Mode: `{data.get('evidence_mode', '')}`",
        f"- P0 findings: `{data['p0_findings']}`",
        f"- P1 findings: `{data['p1_findings']}`",
        "",
        "## Scores",
    ]
    for key, value in data["scores"].items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Findings"])
    if data["findings"]:
        for item in data["findings"]:
            lines.append(f"- `{item['severity']}` `{item['code']}`: {item['message']}")
    else:
        lines.append("- No findings.")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--base-url", default="https://flyto2.com")
    parser.add_argument("--generated-at", default=now_iso())
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--browser-status", choices=["ok", "timeout", "error", "not_run"], default="not_run")
    parser.add_argument("--fixture-pass", action="store_true")
    args = parser.parse_args(argv)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    data = fixture_pass(args.base_url, args.generated_at) if args.fixture_pass else live_probe(
        args.base_url,
        args.generated_at,
        args.timeout,
        args.browser_status,
    )
    json_path = args.output_dir / "public-site-verification.json"
    json_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_markdown(data, args.output_dir / "public-site-verification.md")
    return 0 if data["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
