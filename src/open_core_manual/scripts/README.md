# Release Scripts

These scripts protect the public CE mirror:

- `audit-ce-boundary.py` blocks private code, implicit telemetry, enterprise
  image references, and missing moat documentation.
- `audit-github-protection.py` checks public repository governance files.
- `audit-positioning.py` keeps README, Docker Hub copy, CE/Enterprise matrix,
  roadmap, AutoFix, and benchmark docs aligned to the open-core Warroom story.
- `export-upstream-patches.py` exports public PR changes as upstream patch
  bundles so accepted community work can flow back into Flyto2.
- `install/scripts/provider-readiness.py` records paid/account provider
  blockers without using credentials or calling external provider APIs.

Run `make audit` before changing release-sensitive files.
