# Tasks

## Open

- Add reusable workspace verification examples that do not encode private
  product topology.
- Add stricter generated-artifact ignore checks for packaged source scans.
- Add docs examples for custom layer, taint, and capability rules.

## Watch

- Any dependency that introduces a network requirement into local verify.
- Any test fixture that could accidentally include sensitive customer code.
- Any generated index path that appears in git status.

## Done

- Bootstrapped project memory skeleton and lint gate.
- Removed product release policy from the public package boundary.
- Added observable runtime version and verified local installation.
