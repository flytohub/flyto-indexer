# Flyto Cloud Bundle Producer

Warroom can hand automation test recipes to Flyto Cloud without sharing private
runtime implementation or credentials. The handoff artifact is a signed
`flyto-bundle.yaml` plus hashed local assets.

## Contract

The manifest kind is `flyto.warroom.bundle.v1`.

Required fields:

| Field | Requirement |
| --- | --- |
| `producer` | Public producer name, for example `flyto-warroom-ce-fixture` |
| `bundle_id` | `flyto2-warroom-smoke` |
| `created_at` | UTC timestamp |
| `assets` | Relative asset paths under the bundle root |
| `hashes` | SHA-256 for every asset path |
| `signature` | `hmac-sha256:<digest>` over the canonical manifest without `signature` |
| `required_runtime_args` | Names of args supplied only at execution time |
| `secrets_policy` | Must be `runtime_args_only` |
| `cloud_target` | Cloud folder placement hints |
| `recipes` | Recipe/scenario metadata Cloud promotes into private templates |

Cloud validation rejects missing signatures, hash mismatch, symlinks, absolute
paths, `..` traversal, stored secret fields, and unsupported bundle IDs. A dropped bundle is never executed directly.

## Produce A Fixture Bundle

Set a shared import signing secret outside the repo:

```sh
export FLYTO_WARROOM_BUNDLE_HMAC_SECRET='replace-with-shared-import-secret'
```

Build the public smoke bundle:

```sh
python3 install/scripts/build-cloud-bundle-fixture.py \
  --output /tmp/flyto-warroom-cloud-bundle
```

The output folder contains:

```text
flyto-bundle.yaml
recipes/flyto2-ui-smoke.yaml
recipes/flyto2-ui-login-smoke.yaml
recipes/warroom-deterministic-audit.yaml
```

## Import Into Cloud

On Flyto Cloud, point the inbox to the bundle folder or its parent folder:

```sh
export FLYTO_WARROOM_IMPORT_DIR=/tmp/flyto-warroom-cloud-bundle
export FLYTO_WARROOM_BUNDLE_HMAC_SECRET='replace-with-shared-import-secret'
```

Cloud then follows the closed loop:

1. Scan the inbox.
2. Verify the manifest signature, asset hashes, schema, and path safety.
3. Show the bundle as pending in the Cloud UI.
4. Let a Cloud user approve it.
5. Promote scenarios into private templates with `trigger_type=mcp`.
6. Expose approved scenarios through the Cloud MCP stdio/HTTP server.
7. Execute from Codex or Claude with runtime-only args.
8. Persist execution and evidence in Cloud.

## Security Rules

- Do not write usernames, passwords, tokens, cookies, PATs, authorization
  headers, or session values into manifests or recipe assets.
- Put credential names in `required_runtime_args`; the caller supplies values at
  execution time.
- Do not share password databases between Warroom and Cloud.
- Use a short-lived or rotated HMAC import secret per environment.
- For Enterprise Bridge jobs, use signed short-lived bridge tokens and signed
  evidence results instead of reusing this import secret for runtime calls.
