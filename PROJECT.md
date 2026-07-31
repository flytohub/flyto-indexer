# Flyto2 Indexer Project

## Mission

`flyto-indexer` provides local repository intelligence for Flyto2 development:
project profiles, impact analysis, dependency graphs, security scans, taint
rules, documentation checks, and verification gates.

## Product Role

The indexer is the audit backbone for large multi-repo work. It helps find
where logic conflicts across surfaces before code is changed, and it validates
that implementation, tests, docs, and architecture remain connected.

## Success Criteria

- Indexing and verification run locally without external services.
- Results are actionable for agents and CI.
- Repeated AI-development problems become local, privacy-preserving improvement
  evidence instead of disappearing in chat history.
- Project-specific rules can encode architecture, layer, taint, and release
  constraints.
- Enterprise customers can run the tool in private or airgapped environments
  against their own source.
