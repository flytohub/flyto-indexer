# A reproducible impact case on a real full-stack repository

This case uses the public, MIT-licensed
[`fastapi/full-stack-fastapi-template`](https://github.com/fastapi/full-stack-fastapi-template)
repository at tag `0.10.0` and commit
`d40de23896d27d15c17a7bf9649123fd167a0aa8`. The source, target, expected
relationships, and result fingerprint are pinned. It is not a customer story.

## The change question

Assume an agent needs to change the contract of `render_email_template`.
A direct text search is a reasonable first step:

```bash
git grep -n -w render_email_template -- backend frontend
```

It returns four lines, all in `backend/app/utils.py`: the definition and three
direct calls. That is accurate, but it does not show which request handlers sit
above those calls.

After indexing the same commit, a depth-two impact query finds seven affected
functions across four files. Four request handlers are outside the file found
by text search:

- `recover_password`
- `recover_password_html_content`
- `create_user`
- `test_email`

The useful difference is not “search is bad.” Search found the literal name.
The graph turned the direct matches into a review surface before an edit.

## Reproduce it

From the Flyto2 Indexer repository:

```bash
python scripts/reproduce_impact_case.py --check-snapshot
```

The command performs these steps:

1. clones the public repository at tag `0.10.0`;
2. rejects the checkout unless its commit is the pinned SHA;
3. runs the direct `git grep` comparison;
4. builds a fresh local index;
5. runs impact analysis to depth two;
6. verifies the expected transitive handlers and the committed receipt.

The current machine-readable result is
[`docs/evidence/fastapi-full-stack-0.10.0.json`](evidence/fastapi-full-stack-0.10.0.json).
The [Public proof workflow](https://github.com/flytohub/flyto-indexer/actions/workflows/public-proof.yml)
repeats the case and publishes its receipt as a GitHub Actions artifact.

## What this proves

It proves that, for this pinned source and target, Flyto2 Indexer discovers
static transitive impact outside the files returned by a literal text search.

It does not prove runtime correctness, universal framework precision, or that
every graph edge in every language is equally strong. Runtime behavior still
requires the repository's own unit, integration, browser, service, and
deployment tests. See the [language evidence matrix](LANGUAGE_EVIDENCE.md) for
the supported precision boundaries.
