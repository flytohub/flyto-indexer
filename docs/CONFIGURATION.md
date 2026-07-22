# Configuration

Flyto2 Indexer has three configuration layers: scanner defaults, repository
policy, and process environment. Command arguments override relevant defaults
for a single invocation.

## Scanner Defaults

`config/default.yaml` defines languages, ignored paths, maximum file size,
output directory, summary limits, and impact/context bounds. Treat the shipped
file as package defaults. Put repository-specific enforcement in
`.flyto-rules.yaml` rather than editing installed package data.

## Repository Policy

Create `.flyto-rules.yaml` in the target repository:

```yaml
architecture:
  - rule: UI code stays in the frontend package
    glob_deny:
      - "backend/**/components/*.tsx"

layers:
  - name: domain
    paths: ["src/domain/**"]
    may_import: []
  - name: api
    paths: ["src/api/**"]
    may_import: ["domain"]

taint:
  sources:
    - pattern: "request.args.get"
      language: python
      taint_type: user_input
  sinks:
    - pattern: "cursor.execute("
      vuln_type: sql_injection
      severity: high
```

Policy files are YAML parsed with `safe_load`; malformed content fails closed.
The built-in corpus under `config/rules/` supplies default complexity,
security, secret, license, Docker, IaC, scoring, and ignore rules.

## Generated State

The default `.flyto-index/` directory contains generated indexes and must stay
out of source control. `FLYTO_INDEX_DIR` can point readers and writers to a
different local directory. `FLYTO_AUTO_REINDEX` controls automatic refresh
behavior where supported.

## LSP Enrichment

`FLYTO_LSP_ENABLED` controls optional local language-server enrichment and
`FLYTO_LSP_TIMEOUT` bounds calls. Missing language servers reduce precision to
the deterministic parser or text fallback; they do not require network access.

## Optional LLM Audit

`OPENAI_API_KEY` is read only by the optional LLM auditor. Core indexing,
search, impact, policy, security scans, and verification do not need it. Never
write the key to a repository file or generated report.

The generated [configuration reference](reference/configuration.md) lists all
detected environment readers, flattened defaults, and the complete built-in
rule corpus.
