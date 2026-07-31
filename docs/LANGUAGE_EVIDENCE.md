# Language evidence

Flyto2 Indexer does not claim identical precision across every supported language.
This page separates built-in indexing coverage from security-analysis depth and
committed benchmark evidence. The source of truth is
[`benchmarks/language-evidence.json`](../benchmarks/language-evidence.json); CI
rejects claims that are stronger than the checked-in corpus.

## Capability and proof matrix

| Language | Indexing | Relationship analysis | Security analysis | Committed cases | Evidence level |
| --- | --- | --- | --- | ---: | --- |
| Python | AST definitions, imports, calls, decorators, and routes | Resolved call graph with optional LSP and SCIP precision | AST taint with assignment, interpolation, call, and sanitizer tracking | 5 (3 positive / 2 negative) | gated |
| JavaScript | Token-aware structural extraction with import and call edges | Static graph plus optional LSP and SCIP precision | Pattern-based taint fallback for common source-to-sink shapes | 2 (2 positive / 0 negative) | positive-only |
| TypeScript | Token-aware functions, classes, types, imports, calls, and routes | Static graph plus optional LSP and SCIP precision | Pattern-based taint fallback for common source-to-sink shapes | 2 (1 positive / 1 negative) | gated |
| Vue | SFC script and template-aware component, import, and API-call extraction | Component and call relationships with static fallback | JavaScript and TypeScript pattern fallback inside extracted scripts | 0 (0 positive / 0 negative) | indexing-only |
| Go | Native definitions, receivers, imports, and call edges | Static graph plus optional LSP and SCIP precision | Pattern-based taint fallback for common source-to-sink shapes | 4 (2 positive / 2 negative) | gated |
| Rust | Native functions, impl methods, traits, enums, use paths, and calls | Static graph plus optional LSP and SCIP precision | Repository rules and generic pattern checks; no Rust taint parity claim | 0 (0 positive / 0 negative) | indexing-only |
| Java | Native classes, interfaces, methods, enums, imports, and calls | Static graph plus optional LSP and SCIP precision | Repository rules and generic pattern checks; no Java taint parity claim | 0 (0 positive / 0 negative) | indexing-only |
| Dart / Flutter | Native widgets, classes, constructors, methods, getters, functions, and imports | Static dependency graph | Repository rules and generic pattern checks; no Dart taint parity claim | 0 (0 positive / 0 negative) | indexing-only |
| C / C++ | Dependency-free functions, typedef structs, includes, and call edges | Static dependency graph with optional SCIP precision | Repository rules and generic pattern checks; no C/C++ taint parity claim | 0 (0 positive / 0 negative) | indexing-only |

`gated` means the offline release corpus contains both positive and negative
cases for that language. `positive-only` is narrower evidence. `indexing-only`
means the parser is tested by the main test suite, but no standalone accuracy
corpus is claimed.

## Known limits

- **Python:** Dynamic imports, monkey patching, and runtime dependency injection can require manual review.
- **JavaScript:** The committed corpus has positive cases but no JavaScript negative control yet.
- **TypeScript:** Generated clients, path aliases, and dynamic framework wiring may need LSP, SCIP, or manual review.
- **Vue:** No committed standalone Vue accuracy corpus is claimed.
- **Go:** Interface dispatch, reflection, and generated code can require manual review.
- **Rust:** No committed Rust accuracy corpus is claimed.
- **Java:** No committed Java accuracy corpus is claimed.
- **Dart / Flutter:** No committed Dart or Flutter accuracy corpus is claimed.
- **C / C++:** Macros, templates, conditional compilation, and build flags can require compiler-backed evidence.

The matrix describes static evidence. Runtime behavior still belongs to project-owned
browser, service, integration, race, container, and deployment tests.

## Verify the claims

```bash
python benchmarks/evaluate.py --check --json
python scripts/check_language_evidence.py --check
```
