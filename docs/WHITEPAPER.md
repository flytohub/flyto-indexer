# Flyto2 Indexer Technical Whitepaper

## Abstract

AI-assisted development fails when a model sees a plausible file but not the
contracts around it. Flyto2 Indexer converts a local repository or workspace
into an evidence graph: files, symbols, imports, calls, APIs, package contracts,
tests, policy, and change history. Search locates likely code; impact analysis
explains what depends on it; verification proves that required checks closed.

## Design Goals

1. Local-first operation for private and airgapped source.
2. Deterministic evidence before optional semantic or LLM enrichment.
3. Cross-language contracts without executing analyzed applications.
4. Bounded context suitable for humans, CI, and AI coding agents.
5. Fail-closed policy and observable uncertainty.

## Evidence Pipeline

```text
filesystem + Git + policy
          |
          v
language scanners and manifest readers
          |
          v
files + symbols + routes + imports + calls + packages
          |
          v
local index and reverse dependency graph
          |
          +--> search and context
          +--> impact and contract drift
          +--> quality and security analyzers
          `--> verification evidence
```

Each derived edge retains source location or confidence. Typed parser and LSP
evidence outrank broad textual fallback. Fallback is still useful for
framework files where conventional parsers cannot fully resolve a component,
but it must not overwrite a stronger method-aware or symbol-aware edge.

## Index Model

The index records project identity, files, symbols, dependency direction,
routes, API calls, and generated summaries. Reverse indexes make caller and
blast-radius queries fast. Incremental mode hashes source state and refreshes
changed inputs; full mode rebuilds the graph when reproducibility matters.

The index is disposable generated state. Source files, package manifests, API
contracts, and repository policy remain authoritative.

## Risk And Closure

Impact analysis begins with a symbol or Git diff and expands references,
transitive callers, cross-project edges, co-change history, and likely tests.
The result expresses risk and evidence rather than permission to edit.

Verification then composes independent checks. A scanner crash or malformed
policy is not converted into a clean score. Strict mode promotes unresolved
warnings to failure, making uncertainty visible in CI.

## Extensibility

Language scanners implement syntax-specific extraction while emitting shared
models. Built-in YAML provides security and quality rules. Repository-owned
YAML adds local architecture and taint policy. CLI, MCP, Python, and localhost
HTTP surfaces consume the same core data rather than maintaining separate
business logic.

## Privacy And Deployment

Default indexing and verification need no network service. Optional LSPs run as
local child processes. The optional LLM auditor is separately configured and
is the only documented path that reads `OPENAI_API_KEY`; operators decide
whether repository data may cross that provider boundary.

## Limitations

Dynamic imports, reflection, generated code, macro systems, runtime routing,
and unavailable language servers can reduce edge precision. Text fallback can
create false positives. A passing indexer gate proves its named static checks,
not application behavior, authorization correctness, or production health.

## Reproducibility

The source-backed [reference](reference/README.md), documentation manifest,
tests, build smoke, and strict self-verification tie claims to implementation.
CI rejects drift in generated references and version metadata so a release
cannot silently advertise a stale interface.
