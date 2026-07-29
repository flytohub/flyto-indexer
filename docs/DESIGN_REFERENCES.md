# Design References

Flyto2 Indexer borrows successful mechanics, not product bulk.

| Source | What works | What Flyto2 keeps |
| --- | --- | --- |
| [GitHub Spec Kit](https://github.com/github/spec-kit) | A visible spec → plan → tasks → implementation path | Requirement-to-plan-to-diff traceability |
| [OpenSpec](https://github.com/Fission-AI/OpenSpec) | Plain Markdown, concrete scenarios, brownfield-first deltas | A bounded parser for existing specs; no second project tree |
| [Gemini CLI](https://github.com/google-gemini/gemini-cli) | Clear value statement, fast start, path-scoped project context | JIT Rules with scope, precedence, conflict checks, and fingerprints |
| [Serena](https://github.com/oraios/serena) | A sharp symbol-level promise and semantic refactor workflow | Identity, ambiguity, unresolved-reference, and update-site preflight |

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

## README rule

The main README answers four questions in order:

1. What does this do?
2. Can I try it now?
3. What makes it different?
4. Where are the details?

Technical inventories belong in the feature and generated references, not in
the opening pitch.
