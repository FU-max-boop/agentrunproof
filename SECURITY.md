# Security policy

## Supported versions

Security fixes are made against the latest released AgentRunProof minor version.
Older evidence remains immutable, but users should reproduce a finding with the
latest release before reporting it when possible.

## Reporting a vulnerability

Use GitHub's private vulnerability-reporting form for vulnerabilities in
AgentRunProof itself. Please do not open a public issue for a credential leak,
path escape, certificate-verification bypass, unsafe evidence publication, or
other issue that could put users at risk.

Include the affected version, a minimal reproduction, impact, and any proposed
mitigation. Remove API keys, production traces, private certificates, and user
data from the report.

SDK vulnerabilities that do not depend on AgentRunProof should be reported to
the OpenAI Agents SDK maintainers through their
[documented security channel](https://github.com/openai/openai-agents-python/security/policy).

AgentRunProof executes user-provided Python scenarios and is not a sandbox. A
scenario intentionally running arbitrary code is not itself a vulnerability;
an undocumented privilege boundary bypass or unsafe default may be.
