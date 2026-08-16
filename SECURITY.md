# Security policy

## Supported versions

Security fixes are made against the latest released AgentRunProof minor version.
Older evidence remains immutable, but users should reproduce a finding with the
latest release before reporting it when possible.

## Reporting a vulnerability

Use GitHub's
[private vulnerability-reporting form](https://github.com/FU-max-boop/agentrunproof/security/advisories/new)
for vulnerabilities in AgentRunProof itself. Please do not open a public issue
for a credential leak,
path escape, certificate-verification bypass, unsafe evidence publication, or
other issue that could put users at risk.

Include the affected version, a minimal reproduction, impact, and any proposed
mitigation. Remove API keys, production traces, private certificates, and user
data from the report.

## Response targets

We aim to acknowledge an initial report within 14 calendar days. For a
validated medium-or-higher-severity vulnerability, we aim to make a fix or
documented mitigation publicly available within 60 calendar days of validation.

These are response targets, not guarantees. Complex investigations, coordinated
disclosures, or fixes that depend on an upstream project may require more time;
in those cases, we will aim to share status updates privately with the reporter.

SDK vulnerabilities that do not depend on AgentRunProof should be reported to
the OpenAI Agents SDK maintainers through their
[documented security channel](https://github.com/openai/openai-agents-python/security/policy).

AgentRunProof executes user-provided Python scenarios and is not a sandbox. A
scenario intentionally running arbitrary code is not itself a vulnerability;
an undocumented privilege boundary bypass or unsafe default may be.
