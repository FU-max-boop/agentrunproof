# Paid design-partner pilot

AgentRunProof is testing whether teams will pay for a bounded service that detects regressions in
one deterministic OpenAI Agents `Runner` contract across two exact SDK versions. This is a fixed
managed-service experiment built around the released open-source package. It is not a new hosted
product and does not guarantee compatibility.

## Founding-pilot offer

- **Price:** USD 300, paid before work begins.
- **Duration:** 14 calendar days after the written scope is accepted, payment clears, and the
  agreed customer prerequisites are available.
- **Capacity:** three founding design partners.
- **Engineering cap:** eight hours across the pilot, including one failure investigation capped at
  two hours.
- **Scope:** one repository, one sanitized standard-text `Runner` workflow, and two different
  preverified SDK baselines chosen from 0.20.0, 0.21.0, and 0.22.0.
- **Execution:** an Ubuntu 24.04 GitHub Actions job owned and paid for by the customer. The default
  delivery is a patch that the customer applies, so no private repository access is required.
- **Renewal:** none automatically. After the pilot, the team may choose a USD 200/month
  continuation capped at three engineering hours for the same repository, one scheduled
  compatibility matrix, one monthly SDK upgrade review, and one regression triage capped at 90
  minutes.

Payment proves demand only after both sides accept the exact workflow and access boundary. An
intake issue or call is not an order, and no repository access or implementation work starts
before payment.

The pre-payment scope review is limited to public or customer-approved synthetic facts. If the
agreed invariant cannot be represented deterministically within the first two paid engineering
hours, the customer may choose a full refund and no artifact, or approve a revised written scope.
Time blocked on missing customer fixtures or CI availability pauses the 14-day calendar and does
not expand the eight-hour cap.

## What the pilot delivers

1. A written contract for one observable workflow boundary, such as session replay, function-tool
   linkage, approval/resume, guardrail durability, or stream/non-stream parity.
2. A provider-free synthetic scenario that exercises the real `Runner` without production model
   calls or customer payloads.
3. A baseline result for two different preverified SDK baselines, including the exact environment
   and a content-addressed certificate when the scenario fits certificate v1.
4. A focused pull-request and manual-dispatch CI job in the customer's repository. An optional
   nightly smoke can detect customer-code or environment drift against the two fixed pins; it does
   not discover or adopt a new SDK release. The team owns the job, logs, artifacts, and retention
   policy.
5. One investigation of a failing compatibility boundary, with a minimal reproducer or a clear
   statement that the failure is application-specific, unsupported, or not yet attributable.
6. A closing report that records what was run, what passed or failed, limitations, and the exact
   commands needed to keep or remove the check.

The pilot begins with a short scope review. Days 1–3 define and falsify the synthetic contract;
days 4–7 install the baseline and candidate matrix; the remaining period observes triggered or
optional fixed-pin drift runs and handles one bounded failure investigation. If the workflow
cannot be represented safely or deterministically, the pilot is declined before payment.

## Data and access boundary

- Do not put private code, credentials, prompts, production traces, customer data, or certificates
  in a public intake issue.
- Delivery is a customer-applied patch and written commands. Private repository access is not part
  of the founding pilot.
- Work uses only public material or a synthetic reproducer that the customer explicitly confirms
  is safe to process with the maintainer's development tools, including external AI tools. Private
  repository content, production prompts, customer data, credentials, traces, and private
  certificates are not provided for processing.
- No customer finding or artifact is reported upstream or published without separate written
  approval.
- Synthetic local tools replace production side effects. The pilot does not execute payments,
  messages, destructive tools, or other production actions.
- Private certificates remain in customer-owned CI unless the customer reviews and deliberately
  publishes a synthetic record.
- AgentRunProof's private profile reduces raw payload serialization but is not encryption. Its
  deterministic hashes may reveal equality and may be guessed for low-entropy values.
- The open-source package and its dependencies still execute as third-party code. Customers keep
  their normal dependency, action, and secret-review controls.

## Not included

- model-quality evaluation, prompt optimization, an LLM judge, or token-level scoring;
- a hosted dashboard, multi-tenant storage, uptime SLA, or 24/7 incident response;
- a security, privacy, compliance, or penetration-testing audit;
- production interception or sandboxing of arbitrary tool side effects;
- generic handoff contracts, Realtime, Voice, Sandbox, MCP wire-protocol, or non-OpenAI
  agent-framework compatibility;
- SDK versions other than the preverified 0.20.0, 0.21.0, and 0.22.0 baselines;
- a guarantee that every SDK or application defect will be detected or fixed upstream.

## Good fit

The pilot is intended for a team that already has a standard text `Runner` workflow on Python
3.10–3.14, can run Ubuntu 24.04 GitHub Actions, and can name one costly runtime invariant. Examples
include a tool that must run exactly once after resume, a guardrail whose rejected result must not
reappear, or equivalent post-run observations across streaming and non-streaming execution.

It is not a fit for a team seeking general AI evaluation, a new agent implementation, a production
observability platform, or free exploratory consulting.

## Apply without sharing secrets

Use the public
[design-partner intake](https://github.com/FU-max-boop/agentrunproof/issues/new?template=design-partner-pilot.yml)
to provide only public or synthetic facts: SDK version, CI platform, the invariant in one sentence,
and a safe contact route. Do not attach private artifacts. If the public scope appears suitable,
payment and delivery coordination are arranged privately through the safe contact route. Private
repository access is not part of this offer.

## Experiment success criterion

This page is an offer, not proof of a market. The recurring experiment passes only when an
unaffiliated team pays the fixed pilot price before work, completes the delivery, and prepays at
least one month of the USD 200 continuation. A completed one-off pilot without a paid continuation
validates only the project-service sale, not recurring demand. Stars, repository traffic,
downloads, interviews, free pilots, and verbal interest are useful diagnostics but not the primary
result.
