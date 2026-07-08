# AutoFix Whitepaper: Evidence-Backed Remediation Loop

AutoFix in Flyto2 is not "AI changed files and the alert disappeared." The
closed loop is evidence-backed remediation: a finding must carry provenance,
scope, proposed change, approval, verification, and rollback context.

AutoFix is downstream of the BYO offensive validation platform. Flyto2 first
turns existing tool findings into verified attack paths, pentest evidence, and
red-team scenarios; remediation comes after the validation layer proves or
refutes the risk.

The operating model is detect, triage, remediate, verify, audit, and rerun. A
finding is not closed because a tool says so; it is closed when the relevant
evidence loop proves the state changed or records why it cannot be safely fixed.

## Core Model

1. **Finding intake** records the source, surface, affected asset, confidence,
   severity, reachability, and tenant/org boundary.
2. **Attack-path classification** separates isolated findings from plausible
   exploit chains, safe validation tasks, and red-team scenario candidates.
3. **Fix classification** separates deterministic rules, repo/code/IaC patches,
   container-definition patches, live connector actions, external workflow
   tasks, and AI proposals.
4. **Preview** shows the exact patch or action plan before it can be accepted.
5. **Gate authority** stays deterministic. AI may propose, but it cannot be the final authorization gate for risky actions.
6. **Operator acceptance** records who accepted, which evidence was available,
   what capability allowed the action, and which rollback path exists.
7. **Execution** creates a PR, patch, workflow task, or signed Enterprise Bridge
   job depending on the surface.
8. **Verification** reruns the relevant scanner, replay, connector check, or
   evidence probe.
9. **Audit** stores the result as a timeline event and evidence pack.

## CE Scope

CE should support deterministic remediation loops and visible evidence for:

- code and IaC findings
- dependency and package metadata updates
- container-definition findings such as Dockerfile or manifest fixes
- external findings that can be marked fixed, reopened, or false positive with
  evidence
- local verification results and report artifacts

## Enterprise Scope

Enterprise may add:

- AI proposal generation and review
- live cloud/container/runtime/VM remediation
- approval workflows, promotion, rollback, and support attestation
- managed runner execution
- commercial threat intelligence correlation

## Required Gates

Every AutoFix path must answer these questions before execution:

| Gate | Required answer |
| --- | --- |
| Source | Which scanner, connector, feed, or human created the finding? |
| Scope | Which org, project, repo, asset, container, cloud account, or domain owns it? |
| Confidence | Is the finding verified, inferred, stale, refuted, or false positive? |
| Capability | Which edition, plan, role, and action permission allows the action? |
| Action type | Is this a code patch, definition patch, live connector action, or external task? |
| Rollback | How is the change reversed or reopened? |
| Verification | What evidence proves the fix worked? |

## False Positive Handling

False positives are not noise to hide. They are decisions that need evidence:

- who marked it false positive
- what observation refuted it
- whether the scanner should be tuned
- whether the finding should reopen if new evidence appears

## Non-Claims

Flyto2 does not claim 100% AutoFix success. A safe product should reject or gate
fixes when evidence, permission, connector state, or rollback confidence is
insufficient.
