# Docker stage identity and image inventory

The adoption accuracy review identified `FROM base` as a false untagged-image
finding after `FROM node:22-bookworm-slim AS base`. The image inventory repeated
the same semantic error in a different parser. Both now consume one shared model
in `src/dockerfile_model.py`; neither scanner hides findings with a repo-specific
allowlist. Stage aliases are scoped to their file and prior declarations.

Tests include the four-stage shape, platform flags, digest plus tag, registry
ports, lowercase directives, continuations, quoted heredocs and false heredoc
operators inside shell strings. USER inheritance follows prior stages while a
new external base resets what was observed. An unknown external base USER is not
claimed to be root. Dynamic ARG expansion is intentionally not evaluated.

Verification: 15 focused tests passed; full local suite 2679 passed / 4 skipped /
8 existing CI deselections. Run normal CI and regenerate references. Consumers
must synchronize `flyto-engine/flyto-indexer-pkg` and pin the merged indexer SHA
before deploying. Existing report snapshots are immutable; corrected reports
require a fresh analysis with the new policy/analyzer identity.
