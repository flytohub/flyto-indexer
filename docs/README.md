# Flyto2 Indexer Documentation

Start with the problem you are trying to stop. You do not need to understand
the index format, parser stack, or every scanner before Flyto2 Indexer is useful.

## Choose By Pain

| If this is the problem | Start here |
| --- | --- |
| “I want to know whether this fits my workflow before installing.” | Read [who it is for](../README.md#who-it-is-for) and [why it fits existing tools](../README.md#why-it-fits-your-existing-workflow). |
| “I am afraid this change will break code I cannot see.” | Run the [first setup](../README.md#installation-and-first-result), then use `impact`. |
| “The AI agent keeps missing project rules or requirements.” | Read the [task workflow](FEATURES.md#the-agent-missed-a-rule-or-requirement). |
| “The tests are green, but I still do not trust the change.” | Use the [verification guide](VERIFICATION.md). |
| “Frontend calls and backend routes keep drifting.” | See [contract drift](FEATURES.md#the-frontend-and-backend-drifted-apart). |
| “Security and quality reports are too noisy.” | See [evidence-first audits](FEATURES.md#the-audit-is-too-noisy-to-trust). |
| “The AI keeps repeating the same mistake or scanner complaint.” | Use the [local feedback loop](FEEDBACK.md). |
| “Several repositories change together.” | See [multi-repository work](FEATURES.md#one-change-crosses-several-repositories). |
| “I need the AI to clarify decisions before writing code.” | Use the [Decision Grill protocol](GRILL_TESTING.md). |
| “I just need a command or exact argument.” | Go straight to the [generated reference](reference/README.md). |

## Setup And Daily Use

| Need | Guide |
| --- | --- |
| Decide whether it belongs in your workflow | [Audience and fit](../README.md#who-it-is-for) |
| Install, scan, and see the first result | [Root README](../README.md#installation-and-first-result) |
| Understand the problems it solves | [Feature guide](FEATURES.md) |
| Use the command line | [CLI guide](CLI.md) |
| Connect an AI coding client | [MCP guide](MCP.md) |
| Configure policies and optional integrations | [Configuration](CONFIGURATION.md) |
| Add local or CI finish gates | [Verification](VERIFICATION.md) |
| Turn repeated AI misses into an improvement backlog | [Development feedback](FEEDBACK.md) |

## Trust And Evidence

| Question | Document |
| --- | --- |
| What leaves my machine? | [Security model](SECURITY_MODEL.md) |
| Are scanner claims reproducible? | [Benchmark corpus](../benchmarks/README.md) |
| Does it work on a real repository? | [Pinned FastAPI impact case](CASE_STUDY_FASTAPI.md) |
| Is precision identical across languages? | [Language evidence and limits](LANGUAGE_EVIDENCE.md) |
| How was Decision Grill tested? | [Test protocol](GRILL_TESTING.md) and [dated closure report](GRILL_TEST_REPORT_2026-07-29.md) |
| Why was the product designed this way? | [Design references](DESIGN_REFERENCES.md) |
| How does it work internally? | [Technical whitepaper](WHITEPAPER.md) |

## Exact Reference

The [generated reference index](reference/README.md) is the exhaustive,
source-backed catalog for CLI commands, MCP tools, local HTTP operations,
configuration, built-in rules, and Python interfaces.

Regenerate it after changing a documented interface:

```bash
python3 scripts/generate-reference.py
python3 scripts/generate-reference.py --check
```

Do not edit files under `docs/reference/` by hand.

## Maintainer Memory

- [Project boundary](../PROJECT.md)
- [Architecture](../ARCHITECTURE.md)
- [Current state](../STATE.md)
- [Roadmap](../ROADMAP.md)
- [Decisions](../DECISIONS.md)
- [Changelog](../CHANGELOG.md)
- [Contributor guide](../CONTRIBUTING.md)
- [Documentation coverage manifest](documentation-manifest.json)
