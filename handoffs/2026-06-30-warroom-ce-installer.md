# Flyto2 Warroom CE Installer Handoff

## Summary

`flyto2-open-core-export` now generates a Flyto2 Warroom CE release tree in
addition to whitelisted source packages and the generated `flyto-contracts`
protocol package.

Generated release files include:

- `install/docker-compose.ce.yml`
- `install/docker-compose.ee-sim.yml`
- `install/.env.ce.example`
- `install/.env.ee-sim.example`
- `install/Makefile`
- `install/scripts/build-local-images.sh`
- `install/scripts/mint-ee-sim-jwt.py`
- `install/scripts/audit-release-tree.py`
- `docs/local-install.md`
- `docs/enterprise-simulation.md`
- `docs/code-protection.md`
- `packages/flyto-code/**` frontend source with generated public metadata.

## Boundaries

- The CE compose references only public CE image coordinates.
- Private EE image coordinates are not stored in the open-core manifest or
  exported manifest.
- `flyto-code` is exported as CE frontend source, but raw `.env*`, build
  outputs, reports, node_modules, and personal local dev auth examples are
  blocked from the release tree.
- `flyto-contracts` remains protocol-only: OpenAPI, capabilities, schemas,
  examples, conformance helper, and SDK stubs.
- Raw engine `cmd/**`, Go `internal/**`, and private handler trees must never
  appear in the generated release.
- Production community `local_jwt` auth is still not implemented in
  `flyto-engine`; CE local mode uses `FLYTO_DEV_AUTH=1` and docs call this out.
  Enterprise simulation uses the existing enterprise HS256 JWT path.

## Verification

Last focused commands run:

```sh
python -m json.tool config/flyto2/open-core-manifest.json
python -m pytest tests/test_flyto2_open_core.py tests/test_cli_commands.py -q
ruff check src/flyto2_open_core.py tests/test_flyto2_open_core.py src/cli.py
python -m src.cli flyto2-open-core-audit /Users/chester/flytohub --json
python -m src.cli flyto2-open-core-export /Users/chester/flytohub --output /tmp/flyto2-warroom-ce-<timestamp> --json
python /tmp/flyto2-warroom-ce-<timestamp>/install/scripts/audit-release-tree.py /tmp/flyto2-warroom-ce-<timestamp>
```

All were passing at handoff time.

## Local Operator Flow

For a maintainer with `/Users/chester/flytohub`:

```sh
python -m src.cli flyto2-open-core-export /Users/chester/flytohub --output /tmp/flyto2-warroom-ce
sh /tmp/flyto2-warroom-ce/install/scripts/build-local-images.sh /Users/chester/flytohub
cp /tmp/flyto2-warroom-ce/install/.env.ce.example /tmp/flyto2-warroom-ce/install/.env
make -C /tmp/flyto2-warroom-ce ce-up
```

Enterprise simulation:

```sh
cp /tmp/flyto2-warroom-ce/install/.env.ee-sim.example /tmp/flyto2-warroom-ce/install/.env.ee-sim
make -C /tmp/flyto2-warroom-ce ee-sim-up
python3 /tmp/flyto2-warroom-ce/install/scripts/mint-ee-sim-jwt.py --secret <local-32-plus-char-secret>
```

Put the token into browser `sessionStorage.jwt_access_token` as documented in
`docs/enterprise-simulation.md`.
