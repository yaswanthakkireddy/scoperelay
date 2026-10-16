# Security reporting and support status

ScopeRelay is a synthetic, local-first engineering reference. It has no supported public Live AI service, external communication path, or protected-action release. Do not submit real customer records, credentials, or payment information. The static replay is historical; offline cases are deterministic.

## Coordinated private disclosure

The owner has authorized **yaswanthkumarakkireddy@gmail.com** as the private security-reporting contact. Email suspected vulnerabilities there rather than posting exploit details, secrets or private data in a public issue, discussion or screenshot. Include affected commit/version, impact, synthetic-only reproduction steps and minimal redacted evidence; do not send real customer data or credentials. This address is owner-supplied; mailbox monitoring and receipt have not been independently tested.

The maintainer should acknowledge privately, reproduce in an isolated local environment, coordinate remediation and disclosure timing with the reporter, and publish details only after a mutually coordinated fix or appropriate notice. No response-time guarantee or security certification is offered. Testing live provider infrastructure or protected effects is not authorized by this reporting policy.

[GitHub private vulnerability reporting](https://docs.github.com/en/code-security/how-tos/report-and-fix-vulnerabilities/report-privately) is a **separate post-publication configuration action**. An administrator should enable it on the eventual public repository, verify the reporting button from a non-admin account and assign a maintainer to monitor it. This `SECURITY.md` does not turn that feature on.

See the [trust-boundary model](docs/security/TRUST_BOUNDARY_AND_THREAT_MODEL.md), [canonical threat model](docs/security/THREAT_MODEL.md), and [local Live engineering backlog](docs/delivery/LOCAL_LIVE_ENGINEERING_BACKLOG.md). The coherent-rollback expected failure independently blocks protected-action release. This is not an independent professional security audit.
