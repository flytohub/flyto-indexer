# Public Packages

This directory contains the allowlisted public packages exported into Flyto2
Warroom CE:

- `flyto-code`: Warroom cockpit frontend.
- `flyto-contracts`: public OpenAPI, capability, evidence, scanner, runner,
  and product-verification contracts.

`flyto-core`, `flyto-indexer`, and `flyto-i18n` are **external open-source
dependencies** and are no longer vendored here — install them from their public
registries (see the root `DEPENDENCIES.md`). Copying their already-published
source into this mirror only duplicated code and caused regeneration churn
without being used at runtime.

Private engine internals, commercial datasets, live remediation workers,
billing mutation, and Enterprise deployment code are not exported here.
