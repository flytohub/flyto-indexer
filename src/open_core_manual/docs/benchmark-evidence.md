# Benchmark And Evidence Methodology

Flyto2 should compete on measurable evidence, not unsupported accuracy claims.
This page defines how public benchmark and evidence claims must be made.

Benchmark language must support the same BYO offensive validation loop used by
the product: Findings -> Attack Paths -> Offensive Validation -> Evidence -> Remediation.
Public claims should make that loop inspectable instead of presenting a naked
score.

## What Can Be Claimed

- A scanner result has source, timestamp, target, evidence, and confidence.
- A BYO finding can be traced back to the originating tool, import contract,
  asset, and normalized evidence.
- A finding can be verified fixed, reopened, or marked false positive.
- An attack path can show which imported findings were chained and which safe
  validation step proved or refuted the chain.
- A remediation loop can be rerun and attached to a timeline.
- A benchmark is available only when measured sample size and methodology are
  present.
- A report should distinguish live results from seeded/demo data.

## What Must Not Be Claimed

- guaranteed coverage
- 100% AutoFix success
- benchmark leadership without independent evidence
- full replacement of Aikido or any scanner
- live cloud/container/runtime remediation when only repo definition evidence
  was scanned

## Accuracy Evidence

For each surface, evidence should include:

| Surface | Required evidence |
| --- | --- |
| Code | scanner version, repo/ref, package extraction, reachability, diff or PR, rerun result |
| Container | image reference or Dockerfile source, SBOM, CVE source, runtime/definition label |
| Cloud | connector/account scope, policy source, IAM evaluation, read/write permission gate |
| External | seed ownership, discovery source, verification probe, false-positive decision |
| Automated Security Testing | authorization record, target, replay timeline, screenshot, DOM/network artifact |
| AutoFix | preview, acceptance, execution result, verification evidence, rollback path |

## False Positive Handling

False positives should be first-class:

1. Store the analyst or automated verifier decision.
2. Attach the refuting observation.
3. Suppress or tune only the matching pattern.
4. Reopen when fresh evidence contradicts the decision.
5. Include the decision in reports and audit export.

## Verification Loop

The minimum loop is:

```text
finding -> triage -> fix or false-positive decision -> rerun -> evidence pack -> timeline -> report
```

For Enterprise Bridge jobs, the returned evidence must be signed and bound to
org, action, artifact, and timestamp.

## Benchmark Readiness

A benchmark page or public claim is ready only when it includes:

- test corpus or customer-approved anonymized sample definition
- sample size
- scanner versions
- date range
- false-positive handling policy
- verification method
- limitations

Until then, Flyto2 should publish methodology and evidence examples, not a
fabricated percentage.
