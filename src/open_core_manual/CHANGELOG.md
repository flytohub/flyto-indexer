# Changelog

## 2026-07-01

- Added multi-arch Docker publishing for official CE images. Release scripts
  now publish and verify `linux/amd64` plus `linux/arm64` manifest lists instead
  of ARM64-only tags.
- Added a Docker build boundary audit so official image publishing checks for
  broad final-stage source copies and denied secret/private markers before
  pushing public tags.
- Closed the public Warroom CE release loop with a tracked frontend
  `.env.example`, feature matrix, public roadmap, AutoFix whitepaper,
  benchmark/evidence methodology, and demo seed workspace.
- Added `install/scripts/seed-demo-workspace.py` plus
  `install/demo-workspace.json` so CE users can validate or seed a local
  workspace that explains code/container/cloud/external/evidence/autofix.
- Extended release and CE-boundary audits so missing docs, seed assets, or CE
  env examples fail closed.
- Added `verify-fast` and wired CI to run the generated Warroom root verify
  loop.
