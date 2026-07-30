# Design References

Flyto2 Indexer borrows successful mechanics, not product bulk.

| Source | What works | What Flyto2 keeps |
| --- | --- | --- |
| [GitHub Spec Kit](https://github.com/github/spec-kit) | A visible spec → plan → tasks → implementation path | Requirement-to-plan-to-diff traceability |
| [OpenSpec](https://github.com/Fission-AI/OpenSpec) | Plain Markdown, concrete scenarios, brownfield-first deltas | A bounded parser for existing specs; no second project tree |
| [Gemini CLI](https://github.com/google-gemini/gemini-cli) | Clear value statement, fast start, path-scoped project context | JIT Rules with scope, precedence, conflict checks, and fingerprints |
| [Serena](https://github.com/oraios/serena) | A sharp symbol-level promise and semantic refactor workflow | Identity, ambiguity, unresolved-reference, and update-site preflight |
| [Grill Me](https://github.com/mattpocock/skills/tree/main/skills/grill-me) and [Grill With Docs](https://github.com/mattpocock/skills/tree/main/skills/grill-with-docs) | One high-value question at a time, explicit challenge, and optional document-grounded interrogation | Decision Grill with repository facts, recommendations, freeze, and proof; no model-specific skill dependency |
| [Superpowers](https://github.com/obra/superpowers) and [Get Shit Done](https://github.com/gsd-build/get-shit-done) | Small plans, explicit verification, resumable state, and disciplined subtask boundaries | Responsibility-based atomic guidance and change-aware documentation, advisory by default |
| [Aider](https://github.com/Aider-AI/aider) | Public, reproducible coding benchmarks make capability claims falsifiable | A committed offline accuracy/latency gate with deterministic evidence fingerprints |
| [Semgrep](https://github.com/semgrep/semgrep) and [CodeQL](https://github.com/github/codeql) | Structured static-analysis findings, data-flow evidence, baselines, and SARIF integration | AST-first Python flow, bounded cross-file proof, stable finding IDs, finding-level regression comparison, and SARIF partial fingerprints |
| [Sonar Clean as You Code](https://docs.sonarsource.com/sonarqube-server/10.7/core-concepts/clean-as-you-code/overview) | Gate new debt without pretending a brownfield repository is already clean | Absolute safety floors plus a reviewed baseline that rejects newly-worse health metrics |
| [Ruff](https://github.com/astral-sh/ruff) | A broad capability surface stays usable through a common interface, caching, and measured speed | One cached dispatch registry, no new runtime dependency, bounded results, and explicit p95 budgets |
| [ast-grep](https://github.com/ast-grep/ast-grep), [dependency-cruiser](https://github.com/sverweij/dependency-cruiser), and [ArchUnit](https://github.com/TNG/ArchUnit) | Structural matching and enforceable dependency/architecture constraints | Language-aware scanning plus repository-owned advisory/guarded/strict rules; no mandatory universal architecture |
| [Model Context Protocol](https://github.com/modelcontextprotocol/modelcontextprotocol) and its [Python SDK](https://github.com/modelcontextprotocol/python-sdk) | Standard cancellation, bounded request lifecycles, and Streamable HTTP | Interruptible calls, explicit deadlines, safe replay rules, active-child self-healing, and observable p50/p95 latency |

## Evidence gates adopted

- `benchmarks/evaluate.py` runs committed positive and negative cases through
  the real index and taint analyzer. It measures exact category counts,
  precision, recall, false-positive rate, cross-file path depth, latency, and
  peak memory without cloning repositories or calling a hosted service.
- Verification finding IDs exclude line numbers and raw source bodies. The
  same ID is used by JSON baselines and SARIF, so line-only edits do not create
  churn while genuinely new findings still regress.
- The stdio server reads input on a blocking reader thread and executes tools
  on the main thread, where POSIX timers can interrupt a stuck call. It handles
  cancellation notifications while work is active and does not poll or spin.
- The HTTP bridge exposes rolling latency and concurrency evidence, restarts a
  corrupt or timed-out child, and replays only declared read-only requests
  after protocol failure. Cancellation and deadline failures are not replayed.

## Extension budget

Every addition must preserve:

- 20 public smart tools;
- four `task` actions;
- local, offline core analysis;
- no model binding or hosted dependency;
- bounded output and file discovery;
- no editing, committing, or IDE responsibilities.

The feature activates only when relevant instructions, specs, or change types
exist. Otherwise it adds a small empty contract, not another workflow.

External benchmark suites, hosted rule registries, model orchestration,
automatic edits, and hard-coded atomic architecture are intentionally outside
the default path. They can be integrated by callers, but the general-purpose
indexer remains fast, local, and policy-driven.

## README rule

The main README answers four questions in order:

1. What does this do?
2. Can I try it now?
3. What makes it different?
4. Where are the details?

Technical inventories belong in the feature and generated references, not in
the opening pitch.
