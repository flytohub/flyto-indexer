# Security Model

## Assets And Inputs

The protected assets are analyzed source code, local index contents, policy
files, credentials present in the environment, and the integrity of findings.
Repository files, Git diffs, generated indexes, YAML policy, MCP arguments, and
HTTP request bodies are untrusted inputs.

## Security Properties

- Core analysis is local and does not require a hosted control plane.
- Static scanners inspect text and syntax trees without importing target code.
- YAML uses safe parsing and malformed enforcement policy fails closed.
- Generated indexes are separated from source and checked for accidental
  commits.
- Secret findings redact sensitive values where results are serialized.
- MCP and HTTP schemas constrain accepted inputs and report structured errors.
- Optional LSP processes and optional LLM audit are explicit extensions, not
  prerequisites for local verification.

## Risks And Controls

| Risk | Control |
|---|---|
| Malicious source triggers execution | Static parsing; target modules are not imported by scanners |
| Path traversal or unintended repository scope | Resolve and validate explicit project paths before access |
| Regex denial of service | Pattern validation and bounded scanning |
| Secret disclosure in output | Detection, redaction, and no credentials in examples |
| Stale or forged index data | Freshness, graph-integrity, and full-scan verification checks |
| Policy silently ignored | YAML corpus tests, package smoke test, and fail-closed parsing |
| Tool misuse through MCP | Tool annotations, schemas, rate limits, and caller confirmation for writes |
| Supply-chain drift | Locked CI actions, build checks, SBOM support, and package metadata verification |

## Operator Responsibilities

Run the server with the least filesystem access required, keep `.flyto-index/`
out of source control, review generated reports before sharing, and do not scan
customer repositories on an untrusted machine. Configure optional API keys only
through process secrets. Report vulnerabilities to `security@flyto2.com` as
described in [SECURITY.md](../SECURITY.md).

## Non-Goals

The indexer does not sandbox arbitrary build commands, replace a malware
scanner, prove the absence of vulnerabilities, or authorize code changes. CI
and calling agents decide whether evidence is sufficient for a release.
