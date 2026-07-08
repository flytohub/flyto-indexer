# Demo Seed Workspace

The demo seed workspace gives a first-time CE user one command that creates a
local Warroom workspace and attaches an evidence pack covering the core product
loop:

- BYO finding intake
- attack path validation
- safe offensive validation
- code
- container
- cloud
- external
- evidence
- autofix

The product story is deliberately narrow:

```text
Findings -> Attack Paths -> Offensive Validation -> Evidence -> Remediation
```

Bring your own tools. Flyto2 turns their findings into verified attack paths,
pentest evidence, and red-team scenarios.

The seed is intentionally honest. It does not pretend to run live cloud,
container, or runtime remediation. It labels each item as CE evidence,
definition evidence, or Enterprise-gated action.

## Dry Run

```sh
python3 install/scripts/seed-demo-workspace.py --dry-run
```

The dry run validates `install/demo-workspace.json`, checks that every required
surface exists, and renders the evidence note without contacting the engine.

## Seed A Running CE Stack

Start CE first:

```sh
python3 install/scripts/setup-ce.py --email admin@example.com
make preflight
make verify-images
make ce-up
```

Then seed the workspace:

```sh
python3 install/scripts/seed-demo-workspace.py --email admin@example.com
```

The script:

1. logs in through `/api/v1/auth/local/login`
2. creates a workspace named `Flyto2 Warroom CE Demo`
3. writes the demo evidence pack as a Warroom note/resource
4. prints the local UI URL

## Expected Result

Open `http://localhost:8088`, sign in with the same local admin account, and
select the demo workspace. The evidence pack should be visible as local
workspace content and should describe how code, container, cloud, external,
evidence, AutoFix, BYO imports, attack paths, and safe validation connect into
one closed loop.

## Future Native Seeding

When the engine exposes a supported seed endpoint for native code/container/
cloud/external tables, this script should upgrade from evidence-pack import to
native table population. Until that endpoint exists, the script stays within the
public API contract and avoids private database writes.
