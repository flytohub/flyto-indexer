# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in Flyto Indexer, please report it responsibly.

**Email:** security@flyto2.net

Please include:
- Description of the vulnerability
- Steps to reproduce
- Potential impact

We will acknowledge your report within 48 hours and aim to release a fix within 7 days for critical issues.

## Scope

Flyto Indexer runs locally and does not send any code to external services. The primary security concerns are:

- **Index file integrity** — `.flyto-index/` contains metadata about your codebase
- **MCP server access** — the stdio-based MCP server is only accessible to the local MCP client
- **HTTP API access** — if using the optional HTTP API, it binds to `localhost` by default

## Best Practices

- Add `.flyto-index/` to your `.gitignore` to avoid committing index data
- When using the HTTP API, do not expose it to public networks without authentication
- Keep Flyto Indexer updated to the latest version
