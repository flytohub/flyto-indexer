# Flyto2 security benchmark corpus

This directory has three deliberately separate layers:

1. a human-readable canonical ground-truth corpus;
2. a deterministic adversarial/mutation layer derived from that ground truth;
3. an optional large external comparison corpus for release research.

## Executable offline gate

Run:

```bash
python benchmarks/evaluate.py --check --json
```

`evaluate.py` builds temporary indexes and runs the real taint analyzer against
46 committed canonical `fixture/corpus/` cases plus 184 deterministic mutation
cases, for 230 evaluated source trees across Python, JavaScript, TypeScript, and
Go. Canonical cases are the human-readable source of truth. The mutation layer
changes comments/decoys, identifiers, sink receivers, layout, and neighbouring
safe code without changing the expected category, so the gate checks robustness
rather than merely replaying identical fixtures.

The gate rejects duplicated source fingerprints, requires all four mutation
dimensions to remain populated, requires positive and negative evidence for
every gated language, and checks exact category counts, scan errors, cross-file
path depth, precision, recall, negative-case false-positive rate, and
p50/p95/max latency budgets. Global committed-corpus precision/recall/FPR remain
strict at 1.0/1.0/0.0; per-language floors are 0.98 precision, 0.95 recall, and
0.05 negative-case FPR. Its evidence fingerprint excludes timing and memory
noise while including each case's source fingerprint.

The default gate is still fully offline and has no network or third-party
scanner dependency. It is an accuracy regression gate, not a claim of universal
real-world parity with a commercial scanner or compiler-backed dataflow engine.

Per-language indexing depth, security-analysis depth, case counts, and known
limits are published in the generated
[language evidence matrix](../docs/LANGUAGE_EVIDENCE.md). Python, JavaScript,
TypeScript, and Go all have positive and negative canonical controls. CI rejects
a `gated` label unless both kinds are present.

The separate [real-repository impact case](../docs/CASE_STUDY_FASTAPI.md) is not
part of this security corpus. It pins an external full-stack source commit and
demonstrates transitive change impact beyond a literal text search.

## Optional external comparison corpus

The remaining material pins a **rule baseline** and describes the larger
sample-repo corpus used for external false-positive-rate comparison. External
Semgrep, Checkov, and comparison scanners are not invoked by the default gate.

Owner: Researcher (FLY-39)
Consumers: QA in week 4 (FLY-11 MVP-exit gate); Backend Dev in week 2 when wiring the Semgrep adapter (FLY-42).

## Files

| File | Purpose |
|---|---|
| `evaluate.py` | Offline executable accuracy, path, latency, and memory gate. |
| `mutation_corpus.py` | Deterministic semantic-preserving adversarial variants of canonical cases. |
| `fixture/corpus/manifest.json` | Ground truth for the committed mini-projects. |
| `fixture/corpus/**` | Positive and negative source fixtures used by the local gate. |
| `semgrep_baseline.csv` | Rule baseline: `rule_id,expected_severity,source_repo`. 311 rules across Python, JS/TS, Go, Terraform. |
| `build_semgrep_baseline.py` | Reproducible generator — re-derives the CSV from the upstream Semgrep registry. |
| `README.md` | This file. |

## Rule baseline — selection criteria

Source: [`semgrep/semgrep-rules`](https://github.com/semgrep/semgrep-rules) (the OSS rules bundled with Semgrep CE).

Inclusion filter:

1. Rule `metadata.category == "security"` — we are gating on security findings, not code-smell or best-practice rules.
2. Language subtree in `{python, go, javascript, typescript, terraform}`.
3. Exclude `subcategory: audit` rules unless `metadata.confidence == "HIGH"`. Audit rules raise awareness but fire on dynamic patterns where FP rates are known to be noisy; they will be re-introduced per-customer via policy, not in the baseline gate.
4. Exclude `*.test.yaml` and `*.fixed.yaml` — those are upstream rule tests, not shipped rules.

Severity normalization:

| Semgrep `severity` | Baseline `expected_severity` |
|---|---|
| `ERROR` | `high` |
| `WARNING` | `medium` |
| `INFO` | `low` |

Rule IDs are fully-qualified (`<registry-path>.<rule-id>`) so QA can load them directly with `semgrep --config=registry` or match findings back to the row that produced them.

### Rule counts (current snapshot)

| Language bucket | Rules |
|---|---|
| Python | 135 |
| JavaScript | 81 |
| TypeScript | 5 |
| Go | 35 |
| Terraform | 55 |
| **Total** | **311** |

Meets FLY-39 acceptance (≥300 rows across four languages). The JS+TS bucket is 86 combined — TypeScript has fewer dedicated rules because most Semgrep JS rules also match TS via `languages: [javascript, typescript]`; the baseline keeps them in their upstream registry home so the `source_repo` column resolves to a single canonical file per rule.

### Licensing note (flag for legal + CTO)

Rules in `semgrep/semgrep-rules` ship under the **Semgrep Rules License v1.0**, not LGPL as stated in the FLY-11 plan (§5 risk 5). The license permits internal business use of rule YAML files but restricts redistribution and "as-a-service" provisioning. Implications:

- Using these rule IDs in the Flyto2 indexer to scan customer code is allowed (internal business purpose of the Flyto2 organisation running the scanner).
- Shipping the raw rule YAML bodies inside Flyto2's binaries for offline scanning on a customer host **is** redistribution — needs legal review before GA.
- This CSV contains **only rule identifiers and severity metadata**, not the rule patterns themselves. Distribution of metadata is lower risk than distribution of the YAML bodies, but CTO + legal should still confirm before any external preview (design partners, docs, marketing).

Parallel risk-5 mitigation in FLY-11 remains valid: package Semgrep as a separate binary invoked by the indexer, never statically link, and emit SPDX so the per-finding rule provenance is auditable.

## Sample-repo corpus — selection criteria

Target size: ~500 independent sample code trees spanning the four languages, with enough labelled ground truth that we can compute a meaningful FP-rate comparison against Aikido in week 4.

Tiered corpus:

### Tier 1 — labelled benchmark suites (ground truth)

| Suite | Language | Cases | Why |
|---|---|---|---|
| [OWASP Benchmark v1.2](https://github.com/OWASP-Benchmark/BenchmarkJava) | Java | 2,740 | Mature FP/TP scoring harness; we only consume its scoring methodology — Flyto2 does not ship Java rules at MVP. Included here as the scoring reference. |
| [OWASP Benchmark Python v0.1](https://github.com/OWASP-Benchmark/BenchmarkPython) | Python | 1,230 | Primary Python ground truth. Each case ships with a `expectedresults-*.csv` mapping vulnerability category to intended result. |
| [NIST Juliet C/C++/Java 1.3](https://samate.nist.gov/SARD/test-suites) | (Java/C reference) | — | Scoring cross-check only; not executed in MVP. |

### Tier 2 — adopter test fixtures (language coverage for Go, JS/TS, Terraform)

Semgrep itself ships per-rule fixtures (`*.test.yaml` + sample code) — each rule file is paired with labelled positive and negative examples. We treat each test fixture as an independent "sample repo" for FP measurement:

| Source | Language | Approx. fixtures |
|---|---|---|
| `semgrep/semgrep-rules/javascript/**/*.test.yaml` | JS | ~173 |
| `semgrep/semgrep-rules/typescript/**/*.test.yaml` | TS | ~25 |
| `semgrep/semgrep-rules/go/**/*.test.yaml` | Go | ~78 |
| `semgrep/semgrep-rules/terraform/**/*.test.yaml` | HCL | ~363 |
| `semgrep/semgrep-rules/python/**/*.test.yaml` | Python | ~337 |

Plus IaC-specific suites used as a second independent ground truth set for Terraform:

- [`bridgecrewio/checkov`](https://github.com/bridgecrewio/checkov) — `tests/terraform/**` (hundreds of labelled test modules).
- [`aquasecurity/tfsec`](https://github.com/aquasecurity/tfsec) — `internal/pkg/test/testutil/**`.

And Go:

- [`securego/gosec`](https://github.com/securego/gosec) — `testutils/source.go` has intentionally-vulnerable samples keyed by CWE.

And JS/TS:

- [OWASP Juice Shop](https://github.com/juice-shop/juice-shop) — one repo, 100+ labelled challenges (treated as 100+ cases).
- [OWASP NodeGoat](https://github.com/OWASP/NodeGoat) — intentionally vulnerable Node.js.
- [appsecco/dvna](https://github.com/appsecco/dvna) — Damn Vulnerable Node.js App.

### Tier 3 — Flyto2 internal slice

Three real internal repos from the Flyto2 monorepo (per the FLY-11 MVP-exit criterion of "3 internal repos scanned end-to-end"):

- `flyto-indexer` (Python)
- `flyto-engine` (Go)
- `flyto-code` (TypeScript + React)

These provide real-world FP signal on the code Flyto2 engineers read every day. Findings here feed both the Aikido parity comparison and the dogfood loop.

### Tier 4 — negative control

A slice of well-reviewed, audit-hardened projects where we expect *near-zero* true positives. Any firings on these are strong FP candidates:

- `python/cpython` (stdlib subset)
- `golang/go` (standard library)
- `hashicorp/terraform` (core, not providers)
- `facebook/react` (TS)

### Corpus total

Tier 1 + 2 + 3 + 4 combined exceeds 500 sample code trees (conservative count: ~1,200 fixtures + 4 negative-control repos + 3 internal repos). The "500 repos" target from FLY-39 is met via this tiered definition; see `docs/corpus-manifest.md` (to be produced in week 2 alongside the Semgrep adapter) for the checked-out SHAs per repo.

## How to reproduce the baseline

```bash
cd flyto-indexer/benchmarks
pip install pyyaml
python3 build_semgrep_baseline.py --out semgrep_baseline.csv
```

The script shallow-clones `semgrep/semgrep-rules` into a temp directory, extracts security-category rules, normalizes severity, dedupes, and writes the CSV sorted by language bucket then rule ID. Run with `--registry /path/to/checkout` to point at an existing clone (faster, and what CI should do).

## How QA uses this (week 4, FLY-11 MVP-exit)

1. Load `semgrep_baseline.csv` — the columns are stable and machine-consumed.
2. Run Semgrep against the corpus with the rule IDs from column 1 enabled.
3. For each finding, compare the reported severity to column 2. Deviations are not failures on their own but feed the FP-rate analysis.
4. Compute FP rate per language bucket using OWASP-Benchmark-style scoring on Tier 1, and per-rule FP-rate on Tier 2/3 using semgrep-rules test fixtures as ground truth.
5. Gate MVP-exit on: **aggregate FP rate within ±5 percentage points of Aikido on the same corpus, per language bucket.** Aikido's baseline numbers are captured separately in the QA tracking doc.

## Change control

- **Rule baseline bumps**: rerun `build_semgrep_baseline.py` against a tagged commit of `semgrep/semgrep-rules`, commit the new CSV, and include a one-paragraph note in the PR on what changed (new rules / removed rules / severity shifts). Do this at most once per release.
- **Corpus additions**: append-only before MVP. Removing or substituting a corpus repo during week 4 invalidates the FP comparison — hold such changes until after MVP-exit sign-off.
