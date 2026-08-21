# Isolated, test-only CI adoption

Use this pattern when a downstream library wants one real OpenAI Agents SDK `Runner` contract
test without adding AgentRunProof to its runtime dependencies or project lockfile. The job installs a
released AgentRunProof version into an ephemeral `uv` environment, executes only the focused test,
and discards the environment when the job ends.

The checked-in, copyable test is
[`examples/ci_adoption/test_runner_contract.py`](../examples/ci_adoption/test_runner_contract.py).
It exercises both `Runner.run()` and `Runner.run_streamed()`, consumes a deterministic two-turn
model script, and verifies that one local tool is called exactly once. In a downstream repository,
replace `_build_tool()` with the library's adapter or wrapper while keeping the assertions around
the real `Runner` paths.

## Run it without changing the project environment

From a checkout of this repository, the following command runs the example against an exact SDK
release. `--isolated --no-project` tells `uv` to create a temporary environment and not discover the
checkout's `pyproject.toml`; every dependency needed by this focused test is supplied with
`--with`.

```bash
uv run --isolated --no-project --python 3.12 \
  --with "agentrunproof==0.2.0" \
  --with "openai-agents==0.20.0" \
  --with "pytest>=8,<10" \
  --with "pytest-asyncio>=0.24" \
  -- python -m pytest -q examples/ci_adoption/test_runner_contract.py
```

Change only the SDK pin to exercise the other packaged baseline:

```bash
uv run --isolated --no-project --python 3.12 \
  --with "agentrunproof==0.2.0" \
  --with "openai-agents==0.21.0" \
  --with "pytest>=8,<10" \
  --with "pytest-asyncio>=0.24" \
  -- python -m pytest -q examples/ci_adoption/test_runner_contract.py
```

The 0.3.0 source contract adds exact SDK 0.22.0 coverage. Pin `agentrunproof==0.3.0` downstream
only after its immutable release and PyPI pages exist. Before then, the source contract can be
tested without changing project metadata:

```bash
uv run --isolated --no-project --python 3.12 \
  --with-editable . \
  --with "openai-agents==0.22.0" \
  --with "pytest>=8,<10" \
  --with "pytest-asyncio>=0.24" \
  -- python -m pytest -q examples/ci_adoption/test_runner_contract.py
```

For a downstream test that imports a `src/`-layout package, add `--with-editable .` before `--`.
That installs the checkout only inside the same temporary environment; it still does not alter the
project's dependency metadata or lockfile. If the repository has optional dependencies needed by
the adapter, name the applicable extra, for example `--with-editable ".[openai]"`.

## Minimal GitHub Actions job

This job is deliberately separate from the downstream project's normal dependency installation.
It grants read-only repository permission, pins the action and tool revisions, and tests the two
exact SDK releases covered by the published AgentRunProof 0.2.0 contract.

```yaml
name: OpenAI Agents Runner contract

on:
  pull_request:
    paths:
      - "path/to/adapter/**"
      - "tests/test_openai_agents_runner.py"
  workflow_dispatch:

permissions:
  contents: read

jobs:
  runner-contract:
    runs-on: ubuntu-24.04
    strategy:
      fail-fast: false
      matrix:
        sdk-version: ["0.20.0", "0.21.0"]
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          persist-credentials: false
      - uses: actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97 # v7.0.0
        with:
          python-version: "3.12.13"
      - uses: astral-sh/setup-uv@20cfd1bf945f4377ade1205e4dbc17946fc9a30d # v10.0.1
        with:
          version: "0.12.5"
      - name: Exercise the adapter through the real Runner
        run: |
          uv run --isolated --no-project --python 3.12 \
            --with-editable . \
            --with "agentrunproof==0.2.0" \
            --with "openai-agents==${{ matrix.sdk-version }}" \
            --with "pytest>=8,<10" \
            --with "pytest-asyncio>=0.24" \
            -- python -m pytest -q tests/test_openai_agents_runner.py
```

Remove `--with-editable .` only when the focused test does not import the downstream package. Keep
the job focused: AgentRunProof is a test dependency in this environment, not a runtime dependency
of the package under test.

## What "provider-free" does and does not mean

The example's `DeterministicModel` supplies every model response locally, and `RunConfig` disables
SDK tracing. Consequently, the test sends no model-provider request and needs no API key.

This is **not** a process-level network sandbox:

- `uv` may contact configured package indexes while resolving and downloading dependencies;
- a downstream tool, hook, fixture, plugin, or imported package may still use the network; and
- enabling tracing may let an installed trace processor export data.

Repositories that require full network isolation should prefetch and hash-lock artifacts, then run
the test under their own egress-deny mechanism. Keep downstream tools synthetic and local unless
the test intentionally covers an integration boundary.

## Dependency and release trust

- Pin `agentrunproof==0.2.0` and an exact `openai-agents` release in the isolated job. Its supported
  range is `openai-agents>=0.20.0,<0.22`; 0.20.0 and 0.21.0 are the exact packaged CI baselines.
- The 0.3.0 source contract expands that declaration to `openai-agents>=0.20.0,<0.23` and adds an
  exact 0.22.0 packaged-wheel cell on every supported Python. Switch downstream pins and matrices
  after the final release, checksums, and release-bound evidence are public.
- The immutable [v0.2.0 GitHub release](https://github.com/FU-max-boop/agentrunproof/releases/tag/v0.2.0)
  publishes wheel and sdist SHA-256 values in `SHA256SUMS`. Its release workflow re-downloads those
  assets, compares them byte-for-byte with a rebuild from the tagged source, and smoke-tests the
  wheel before the same verified files are sent to PyPI through OIDC trusted publishing.
- The v0.2.0 wheel SHA-256 is
  `e393e98bf797cc10f07ea151ec6fffd3b1ebbb21307256309e08f11e97a27d51`; its sdist SHA-256 is
  `c0ad9c2aeb425cfcaff81b6daeee9c9ded069397dfc7a3319fda28f9842f2ace`.
- A repository with a lock or hash policy should resolve this isolated closure with its normal
  dependency-review process and commit the resulting test-only lock data. The short `uv run`
  pattern above intentionally does not create a lockfile.

The package and its transitive dependencies still remain third-party code executed in CI. These
controls make the scope and artifact identity reviewable; they do not replace the downstream
maintainer's dependency and supply-chain policy.
