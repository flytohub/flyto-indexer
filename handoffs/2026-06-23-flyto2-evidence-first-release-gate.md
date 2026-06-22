# Flyto2 evidence-first release gate

Date: 2026-06-23 Asia/Taipei

Summary:

- Reframed Flyto2 repo health scores as minimum hygiene signals, not product
  readiness proof.
- Added `config/flyto2/evidence-gates.json` to define product-line,
  deployment, security, visibility, and operations gates.
- Added `--evidence-gates` to `flyto2-release-packet` so alternate gate
  manifests can be supplied without code changes.
- Added release packet fields:
  - `health_signal`
  - `score_limitations`
  - `evidence_gates`
  - `confidence_basis`
  - `not_proven`
- Changed production verdict behavior: missing P0/P1 evidence now blocks
  production even when repo health scores are high.
- Changed non-core health regression behavior: non-core repos below target are
  product-gate warnings unless an evidence gate depends on them.

Why:

- Health scores are useful for triage and hygiene, but they cannot prove user
  workflows, enterprise deployment readiness, security control effectiveness,
  commercial positioning, or AI/search visibility.

Verification:

```text
/opt/homebrew/bin/python3.11 -m pytest tests/test_flyto2_release_packet.py tests/test_flyto2_product_gate.py -q
ruff check src/flyto2_release_packet.py src/flyto2_product_gate.py src/cli.py tests/test_flyto2_release_packet.py tests/test_flyto2_product_gate.py
```

Residual risk:

- The default evidence gates still rely on release artifact presence and
  freshness. Future work should deepen each artifact into machine-readable
  assertions, for example scenario counts, pass/fail command summaries, and
  deployment-mode-specific acceptance criteria.
