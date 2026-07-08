# Flyto2 Warroom Release Readiness

This document separates code readiness from account/provider readiness. A local
or public CE release can be built only when both sides are green.

## Code Gates

The public CE release tree must pass:

- `python3 scripts/audit-ce-boundary.py .`
- `python3 install/scripts/audit-release-tree.py .`
- frontend build, i18n hardcoded audit, visual-system audit, and focused UI
  interaction tests
- Docker build-boundary audit and multi-arch image verification when publishing
  images
- demo seed workspace audit covering code, container, cloud, external,
  evidence, and AutoFix
- `python3 install/scripts/provider-readiness.py --scope public_release`
  records paid/account gates and returns `CODE_READY_PROVIDER_BLOCKED` until
  the account owner marks the required providers ready.

## Account And Provider Gates

These gates cannot be fixed by source code alone. They are release blockers
until the account owner resolves them:

- GitHub Actions billing/startup lock: required checks cannot be treated as
  release evidence while workflows fail to start.
- Docker Hub authentication, repository visibility, and image-push permission:
  public images must be pushed by an account allowed to publish the declared
  repository and tags.
- Domain, website, and support-contact ownership: public docs may link to
  Flyto2 properties, but production claims require the corresponding service to
  be reachable.
- Enterprise license, bridge, and airgap package distribution: CE can expose the
  protocol and locked UI state, but premium execution requires signed entitlement and signed evidence.

## Release Verdict

If any account/provider gate is blocked, the correct verdict is
`CODE_READY_PROVIDER_BLOCKED`, not `READY_FOR_RELEASE`.

Provider-blocked releases may still be useful for local validation, public
documentation review, and dry-run image builds. They must not be described as
fully released, remotely CI-green, or commercially available until the external
gate is resolved and fresh evidence is captured.

The paid/provider ledger is maintained in `docs/paid-prerequisites.md`.
