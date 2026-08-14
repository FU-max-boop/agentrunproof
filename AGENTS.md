# AgentRunProof contributor guide

## Scope

Read `PROJECT_CHARTER.md` and `PLAN.md` before changing runtime behavior, certificate schemas, supported SDK versions, or release gates. Keep the implementation within the current charter milestone unless concrete evidence requires a scope change; record any such change in the plan's decision log first.

## Engineering rules

- Use only documented or intentionally public OpenAI Agents SDK imports in production code. Version adapters must be isolated and must fail with an actionable unsupported-version error.
- A missing observation is `NOT_RUN`, never `PASS`.
- Normalize volatile identifiers before comparing variants, while preserving invocation linkage and occurrence counts.
- Certificates and manifests use canonical UTF-8 JSON with sorted keys and no NaN/Infinity values.
- Redaction happens before any untrusted model/tool payload is written to disk. Unknown sensitive shapes fail closed.
- Do not execute network calls during built-in conformance scenarios or evidence verification.
- Keep generated evidence out of the source distribution unless its provenance and license are explicitly reviewed.

## Required checks

Before committing runtime or test changes, run:

```text
ruff check .
ruff format --check .
mypy src
pytest -q
python -m build
twine check dist/*
```

Also install the wheel and sdist into separate fresh environments and run the CLI smoke suite before a release.

## Evidence publication

Canonical public evidence must be generated from a clean Git commit. Never edit a signed-off certificate by hand. Publish payload files first and the bundle manifest last. The independent checker must validate exact schema, hashes, shared run identity, semantic expectations, source provenance, and the absence of dirty-tree overrides.

## External reports

Describe the narrow invariant violation and observed impact. Distinguish SDK runtime behavior from model quality and application behavior. Include the pinned SDK revision, minimal ordinary reproducer, AgentRunProof certificate, and limitations. Do not claim official recognition until a maintainer or official repository action supports that wording.
