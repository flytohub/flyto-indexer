# Flyto2 Warroom Edition Profiles

Flyto2 Warroom uses the same delivery model as Flyto Cloud, but it keeps its
own identity store, database, evidence store, and runtime implementation. The
shared boundary is the public capability, license, evidence, and bundle
contract.

## Profile Contract

The shared contract is `flyto.editions.v1`:

| Field | Meaning |
| --- | --- |
| `product` | `warroom` |
| `edition` | `ce`, `saas`, `enterprise`, or `airgap` |
| `deployment` | `hosted`, `self_host`, `local_offline`, or `airgap` |
| `auth_mode` | `local_jwt`, `flyto_account`, `oidc`, `saml`, `ldap`, or `none_local_loopback` |
| `license_mode` | `none`, `subscription`, `enterprise_license`, or `offline_license` |
| `capabilities` | Explicit API, UI, MCP, bridge, and evidence capabilities |
| `pages` | Explicitly exposed routes; unknown pages are denied |
| `bridge_policy` | Cloud bridge, signed bundle, and evidence verification rules |

## Supported Profiles

| Profile | Edition | Deployment | Auth | License | Cloud Bridge |
| --- | --- | --- | --- | --- | --- |
| `warroom_saas` | SaaS | hosted | Flyto account / OIDC | subscription | managed, tenant-scoped |
| `warroom_ce` | CE | self_host or local_offline | local JWT | none | optional signed bundle export only |
| `warroom_enterprise_selfhost` | Enterprise | self_host | OIDC, SAML, LDAP, or local break-glass JWT | enterprise license | optional signed bridge |
| `warroom_enterprise_airgap` | Enterprise | airgap | enterprise IdP or local fallback | signed offline license | disabled; offline update bundles only |

## Identity Boundary

Warroom and Cloud do not share a password database.

- CE defaults to local JWT in each product.
- SaaS uses Flyto account or customer OIDC.
- Self-host Enterprise uses the customer IdP and short-lived signed bridge
  tokens when Cloud is enabled.
- Airgap uses the customer IdP or local fallback and never depends on SaaS.
- `auth_mode=none_local_loopback` is only valid for single-user loopback
  automation. It must not be enabled for team, self-host Enterprise, SaaS, or
  airgap deployments.

## Capability Gate Rules

Warroom UI routes, API actions, premium remediation actions, and bridge actions
must read the same capability snapshot. Route defaults are deny-by-default:
login, setup, health, and core local pages may be fallback-visible, but billing,
managed runner, marketplace seller, Enterprise admin, and bridge execution
routes must be explicitly exposed by the active profile.

Premium actions fail closed when any gate fails:

- missing or expired license
- unsupported edition or deployment
- denied role or missing action permission
- missing connector
- missing or invalid bridge signature
- cloud service unavailable
- unsigned or invalid evidence result
- tenant, org, or project mismatch

## Cloud Bundle Producer

Warroom can export automation recipes to Flyto Cloud as a signed
`flyto-bundle.yaml` dropped into a customer-controlled folder. Cloud only scans,
validates, and queues the bundle. It does not execute a dropped bundle until a
Cloud user approves it in the import inbox.

The producer contract is documented in `docs/cloud-bundle-producer.md`.
