# Security Policy

## Supported versions

| Version | Supported |
| --- | --- |
| 2.0.x | Yes |
| 2.0 pre-releases (`2.0.0b1`, `2.0.0rc1`) | No: upgrade to 2.0.x |
| 1.x | No |

## Reporting a vulnerability

Please report security problems privately. Do not open a public issue with details.

1. On GitHub, open the repository's **Security** tab and choose **Report a vulnerability**.
2. If that option is not shown, open an issue titled "Security contact request", with no details. The maintainer will reply with a private channel.

Please include:
- the affected version or commit;
- the steps to reproduce;
- what an attacker could do.

Never include a real API key, even an expired one.

What to expect:
- an acknowledgement within 7 days;
- an assessment, and a plan or fix, as soon as the issue is understood.

Fixes are released as a patch version and noted in [`CHANGELOG.md`](CHANGELOG.md). With your agreement, the report is credited.

## Scope

In scope:
- the `campaign_forge` package, the CLI, the web app and the HTTP API in this repository;
- the public demo at https://campaign-forge.streamlit.app/.

Out of scope:
- vulnerabilities in model providers (Groq, Gemini, Mistral, Cloudflare), in Streamlit Community Cloud, or in third-party dependencies. Report those to their maintainers. `pip-audit` runs in CI against the pinned dependencies.
- prompt injection that only changes the wording of generated marketing text. No defence makes a model immune, and the design limits what a successful injection can do (see below).
- denial of service against the free-tier demo. It is capped by design.

## How the project is secured

The threat model, its controls and their status are in [`docs/SECURITY.md`](docs/SECURITY.md). In short:

- **Keys.**
  - Keys are held as secrets, are never logged, and are redacted from errors, run records, exports and memory.
  - A visitor's key in the public demo lives only in their browser session's memory.
- **Agents cannot act in the world.** They have no tools that publish, send, pay or delete. Generated code is never executed.
- **Untrusted text is data.** Briefs, uploads, fetched pages and stored memory are framed as data. Outputs are schema-validated. A 14-case injection dataset runs in CI.
- **Outbound requests.** User-supplied URLs are checked against private and metadata address ranges on every redirect.
- **The API is closed by default.** It requires API keys, gives each key its own tenant, and enforces rate limits.
