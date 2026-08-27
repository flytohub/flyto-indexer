# Git ignore scan boundary

## Status

Implemented locally for independent host verification.

## Scope

- Added a shared local Git ignore filter with deterministic input order,
  bounded path/byte batches, NUL-delimited input/output, and a fixed timeout.
- Applied it to SecurityScanner discovery, all TaintAnalyzer filesystem
  traversals and cached caller reads, and incremental directory hashes.
- Git's index-aware default retains tracked ignored files. Non-Git projects and
  Git execution or protocol failures retain candidates allowed by the existing
  built-in exclusions.
- Added focused coverage for untracked and tracked ignored files, spaces and
  newlines, surrogateescaped path bytes, malformed output, timeouts and bad
  return codes, count/byte batching, oversized-input fallback, all three
  scanner surfaces, and taint phases.

The governing host owns the final source-controlled checks and audit.
