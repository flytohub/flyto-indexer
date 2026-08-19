# Flyto2 Indexer Roadmap

## Now

- Make the impact-to-verify loop undeniable through pinned real-repository
  cases, public receipts, and reproducible CI evidence.
- Turn false positives, false negatives, missing relationships, and slow scans
  reported by users or coding agents into minimal regression fixtures.
- Keep each language claim bounded by its actual positive/negative corpus and
  disclose indexing-only support separately from gated semantic depth.
- Preserve fast local-only defaults and the 20-tool MCP surface while evidence
  and adoption mature.
- Keep cross-AI continuity compact and local; expand it only from a reproduced
  handoff failure, not by adding session documents or dashboard features.
- Keep security-research triage strong within the lean lane: extend built-in
  and YAML taint rules and triage precision from reproduced cases, and use
  selective LSP on the shortlist. Do not add whole-program type-resolved
  dataflow here — it belongs above the MCP, not inside it.

## Next

- Add a second real case only when it demonstrates a distinct failure mode,
  such as a frontend/backend contract miss or framework-generated route.
- Promote a language from indexing-only or positive-only evidence only after a
  reviewed negative corpus proves its false-positive boundary.
- Publish trend reports for accuracy, latency, quality debt, and real-case
  closure without collecting source code or prompts.
- Improve workspace-level verification when user feedback provides a concrete
  mixed-language failure that the existing graph cannot explain.

## Later

- Add asymmetric signature adapters for proof receipts where organizations do
  not want shared HMAC trust keys.
- Add deeper language server call hierarchy coverage where local LSPs are
  available.

## Out of scope for this repository

- Whole-program, type-resolved taint dataflow (the CodeQL / Pysa class of
  recall). It would break the zero-dependency, 20-tool surface. Type resolution
  belongs in a layer above the MCP (a service or the consuming product), invoked
  selectively, not baked into the engine.
- Any autonomous "find → fix → submit" loop. Downstream products
  (`flyto-code` / `flyto-engine`) own disclosure, and every step stays
  human-gated — see the security-triage handoff. The indexer only ever produces
  ranked leads.
