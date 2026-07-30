<div align="center">
  <h1>Flyto2 Indexer</h1>
  <p><strong>Know what breaks. Prove the fix.</strong></p>
  <p>
    <a href="https://github.com/flytohub/flyto-indexer/actions"><img src="https://github.com/flytohub/flyto-indexer/workflows/CI/badge.svg" alt="CI"></a>
    <a href="https://pypi.org/project/flyto-indexer/"><img src="https://img.shields.io/pypi/v/flyto-indexer.svg" alt="PyPI"></a>
    <a href="https://github.com/flytohub/flyto-indexer/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-blue.svg" alt="License"></a>
    <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11%2B-blue.svg" alt="Python 3.11+"></a>
  </p>
  <p>
    <a href="#installation-and-first-result">Quick start</a> ·
    <a href="#before-and-after-rename-one-shared-function">See it in action</a> ·
    <a href="docs/README.md">Documentation</a>
  </p>
</div>

Your coding agent can edit a repository in seconds. The expensive mistakes come
later: a missed caller, a stale contract, an ignored repository rule, or
“done” declared after the nearest test passes.

Flyto2 Indexer gives any MCP-capable coding agent a local map before it edits
and an evidence gate before it stops.

- **Map the change:** see callers, dependents, tests, APIs, and cross-project
  impact before touching code.
- **Keep the intent:** carry repository rules, requirements, and decisions into
  the actual diff.
- **Prove the result:** close lint, tests, security, documentation, and change
  conformance before the agent says it is finished.

No API key. No model lock-in. No source upload.

## Who It Is For

Flyto2 Indexer is most useful when:

- you use AI on an existing codebase that no one holds entirely in their head;
- a change can cross packages, services, or repositories;
- several developers or coding agents must follow the same rules;
- private, regulated, or air-gapped source must stay local.

It is not another code generator, IDE, or hosted dashboard. Keep the tools you
already trust; Flyto2 Indexer gives them a shared change map and finish gate.

## Installation And First Result

```bash
pip install flyto-indexer
flyto-index setup .
flyto-index verify . --strict
```

`setup` builds a local index and configures supported MCP clients. Then ask your
agent:

```text
impact(target="validateOrder", change_type="rename")
```

```text
7 call sites · 3 projects · 2 test files
Risk: high
Manual review: 1 unresolved dynamic reference
```

A text search finds the name. Flyto2 Indexer shows the change surface.

## The Problems It Removes

| Pain | What Flyto2 Indexer changes |
| --- | --- |
| “I changed one function and something unrelated broke.” | Shows callers, dependents, likely tests, and unresolved references before the edit. |
| “The agent ignored our repository rules.” | Loads the instructions that apply to the target and blocks contradictory or stale guidance. |
| “The ticket said five things; the diff only did three.” | Links requirements to planned steps, changed paths, and proof. |
| “Tests passed, but the change still was not ready.” | Verifies the index, impact, security checks, documentation, policy, package state, and working tree together. |
| “Frontend and backend drifted apart.” | Compares calls, routes, and contracts so missing connections are visible. |
| “Our scanner is so noisy that nobody trusts it.” | Keeps evidence local, reports confidence and provenance, and supports baselines for accepted debt. |
| “A large repository overwhelms the agent.” | Returns bounded, relevant context instead of dumping the whole codebase. |

## Before And After: Rename One Shared Function

Suppose `validateOrder` must be renamed. This is an illustrative walkthrough
using the sample result above, not a customer case study.

**Before:** Search for the name, edit the obvious files, run the nearest test,
and discover missed callers or dynamic references later.

**After:** `impact` makes the seven call sites, three projects, two likely test
files, and one manual-review item visible before the edit. The agent turns that
list into a scoped plan, runs the relevant proof, and keeps the unresolved item
visible until someone reviews it.

**Practical difference:** hidden work becomes an explicit checklist before it
becomes a regression.

## Why It Fits Your Existing Workflow

Flyto2 Indexer complements the tools you already use:

| Keep using | What it already does well | What Flyto2 Indexer adds |
| --- | --- | --- |
| Your coding agent | Understands requests and applies edits | A local change map, scoped rules, and a finish gate |
| IDE search or `grep` | Finds names and direct references quickly | Transitive impact, cross-project links, likely tests, and unresolved gaps |
| Linters and test suites | Catch the failures they are configured to detect | Proof that the requested work, changed paths, and required checks still agree |
| CI | Repeats commands on every change | One regression-aware repository and workspace verdict |

You do not need to replace your model or development workflow. Try it on one
risky refactor first.

## Usage

Keep daily work in one closed loop:

```text
find → understand impact → plan → pass the gate → edit → validate → verify
```

The public tool names are short:

```text
search → impact → task(plan) → task(gate) → edit → task(validate) → verify
```

- `search` finds the relevant code and concepts.
- `impact` shows what a change can affect.
- `task` keeps decisions, project rules, requirements, and proof connected.
- `verify` checks whether the repository is actually ready to finish or merge.

When a gate fails, it explains what is missing. Complete those actions and run
the same gate again. A failed gate pauses the unsafe step; it does not abandon
the task.

## When The Task Is Still Vague

Use the optional Decision Grill before planning. It resolves facts from the
repository first, then asks one high-value question at a time. Once the
important decisions are settled, it freezes them into the plan so the final
diff can be checked against what was agreed.

```text
task(action="grill", grill_action="start", description="Add robot adapter")
task(action="grill", grill_action="freeze", grill_session_id="grill_...")
task(action="plan", grill_session_id="grill_...", ...)
```

If there is no real product or architecture choice to make, skip Grill and go
straight to `task(plan)`.

## The Main Questions It Can Answer

| Tool | Question |
| --- | --- |
| `search` | Where is the relevant code? |
| `impact` | What could break if this changes? |
| `task` | Are the decisions, rules, requirements, and proof complete? |
| `audit` | Where are the most important quality and security risks? |
| `structure` | How is this project connected? |
| `verify` | Is this repository ready to finish or merge? |

Focused checks also cover secrets, unsafe data flow, dependencies, licenses,
software inventory, architecture boundaries, documentation, pull-request risk,
and multi-repository verification. The exact tool contracts live in the
[generated MCP reference](docs/reference/mcp-tools.md).

## What “Verified” Means

`verify` combines checks that are usually scattered across several commands:

- Is the local index current and internally consistent?
- Can the requested context and impact path be reproduced?
- Did secret, unsafe-data-flow, and repository-policy checks pass?
- Are documentation, package metadata, and generated files in sync?
- Is the working tree clean enough for the requested gate?

It does not pretend static analysis proves runtime behavior. Browser, service,
integration, and deployment checks can remain project-owned proof commands and
be attached to the task contract.

## CLI

```bash
flyto-index scan . --full
flyto-index impact useAuth --path .
flyto-index context --path . --query "auth routes query keys"
flyto-index task plan --description "Refactor auth" --target src/auth.py
flyto-index verify . --strict
flyto-index verify-workspace . --changed-only --base origin/main
```

The same guarded `task` workflow is available through the CLI when an MCP
client has a stale long-running process.

## CI

```yaml
- run: pip install flyto-indexer
- run: flyto-index scan . --full
- run: flyto-index verify . --strict
- run: flyto-index check . --threshold medium --base main
```

Projects can keep an accepted baseline and fail only newly worse findings:

```bash
flyto-index verify . \
  --baseline .flyto-baselines/flyto-indexer.json \
  --regression-only
```

## Designed To Stay Lean

- The public MCP surface stays at 20 tools instead of growing one tool per
  scanner.
- Normal analysis is local and works without a hosted service.
- Generated data stays under `.flyto-index/` and can be deleted at any time.
- Flyto2 Indexer reports evidence; it does not auto-edit, auto-commit, or take
  over the coding agent.
- Optional precision adapters remain optional. The default install keeps one
  runtime dependency.
- Large results are bounded and pageable so they do not flood the agent's
  context.

For clients that need a persistent local connection, an optional loopback-only
HTTP bridge can keep one MCP process warm and restart it after a failure. See
the [MCP guide](docs/MCP.md).

## Languages

Built-in indexing covers Python, TypeScript and JavaScript, Vue, Go, Rust,
Java, Dart, and C/C++. Local language servers and SCIP data can improve
reference precision when available; the built-in index remains the fallback.

## Evidence, Privacy, And Limits

Target repositories are treated as untrusted input. Static checks do not
intentionally import or execute the code being analyzed. Findings include
confidence and trace evidence without retaining raw secrets.

The committed offline benchmark covers positive, negative, sanitized, and
cross-file cases across Python, JavaScript, TypeScript, and Go. It gates
accuracy, false positives, scan errors, and latency on every release. See the
[reproducible benchmark](benchmarks/README.md) and
[security model](docs/SECURITY_MODEL.md).

## Documentation

- [Choose a guide by your pain](docs/README.md)
- [Problems and capabilities](docs/FEATURES.md)
- [CLI guide](docs/CLI.md)
- [MCP setup and runtime](docs/MCP.md)
- [Configuration](docs/CONFIGURATION.md)
- [Verification](docs/VERIFICATION.md)
- [Technical whitepaper](docs/WHITEPAPER.md)
- [Generated references](docs/reference/)

The [design references](docs/DESIGN_REFERENCES.md) explain what was borrowed
from Spec Kit, OpenSpec, Gemini CLI, Serena, Grillme, and other projects—and
what was deliberately left out to avoid bloat.

## Contributing

```bash
python -m ruff check src tests
python -m pytest
python benchmarks/evaluate.py --check
flyto-index verify . --strict
```

Security reports: `security@flyto2.com`.

## License

[Apache License 2.0](LICENSE). See [NOTICE](NOTICE).

<!-- mcp-name: io.github.flytohub/flyto-indexer -->
