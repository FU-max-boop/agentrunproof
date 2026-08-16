# Contributing to AgentRunProof

AgentRunProof welcomes focused contributions that make OpenAI Agents runtime
behavior easier to reproduce, compare, and verify without a model provider.

## Good contributions

- a deterministic scenario for a public SDK runtime contract;
- a minimized regression for streaming, sessions, tools, or resumable state;
- stronger certificate validation or tamper tests;
- compatibility fixes for a released, supported SDK version;
- documentation that makes a result easier to reproduce or interpret.

Built-in scenarios must be provider-free and deterministic. Do not add API keys,
private user data, hidden benchmark material, or task-specific workarounds.

## Before writing a scenario

Open a scenario-proposal issue first when the change introduces a new runtime
contract or certificate field. Describe:

1. the public SDK behavior being checked;
2. the smallest observable counterexample;
3. the expected invariant and failure reason;
4. the SDK versions or source commits involved; and
5. why an ordinary application test is insufficient.

Small documentation and clearly isolated bug fixes may go directly to a pull
request.

## Local setup

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[test,dev]"
pytest
ruff check .
ruff format --check .
mypy src
```

Before submitting, also build and inspect the distributions:

```bash
python -m build
python -m twine check --strict dist/*
```

## Pull requests

Keep each pull request narrow. Include the failing observation or contract,
tests for both success and failure paths, and the exact commands you ran. State
anything you could not verify.

Changes to certificate schemas, normalizers, invariant identifiers, canonical
evidence, supported dependency ranges, or publishing workflows require an
explicit compatibility and release-impact note. Never rewrite immutable
historical evidence in place.

By contributing, you agree that your contribution is licensed under the MIT
License used by this repository.
