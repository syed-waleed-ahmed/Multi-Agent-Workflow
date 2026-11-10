# Security Model

This document covers the threat model and planned security controls for Campaign Forge 2.0, and records which controls exist today in v1.1.

- **Status:** PLANNED (Session 0). Security work is spread across the roadmap rather than left to the end: the primitives arrive in Session 1, the tool sandboxing in Session 4, and the public-demo hardening in Session 9.
- **Not this file:** the repository-root `SECURITY.md`, which explains how to report a vulnerability. That file is created in Session 10.

---

## 1. Assets

| Asset | Why it matters |
| --- | --- |
| Provider API keys (owner and visitor) | Direct quota and financial exposure |
| The owner's free-tier quota | A public demo can be drained by abuse |
| User inputs (briefs, brand data, uploaded files) | May be confidential; some free providers may use prompts to improve their products |
| Stored memory (brand profiles, campaign history) | Business data, and it must stay isolated per user and organisation |
| Integrity of outputs | Fabricated sources or injected instructions damage trust |
| The hosting environment | The internal network, metadata endpoints and the filesystem |

## 2. Threat actors and entry points

- **Actors:** anonymous public-demo visitors; authenticated API clients; content authors whose web pages are fetched as research material (indirect injection); the supply chain (dependencies).
- **Entry points:** Streamlit forms and file uploads; FastAPI endpoints; CLI arguments and brief files; fetched URLs and search results; stored memory read back into prompts; workflow definition files.

## 3. Threats and controls

| ID | Threat | Control | Session | Status (after S1) |
| --- | --- | --- | --- | --- |
| T1 | **Direct prompt injection** through brief fields | Untrusted text goes into prompts inside delimited, labeled data blocks with a "data, not instructions" policy in the system prompt. Outputs are validated against schemas. Deterministic canary checks run in evals. Agents have no tools that can act in the world. | S1, S9 | **Partial**: framing and policy in every v1 agent (S1); hardening in S9 |
| T2 | **Indirect prompt injection** through fetched pages, files or memory | Same framing, plus provenance labels. Tool output can never change the workflow graph, tool permissions or budgets; the planner reads only validated brief data. | S4, S5 | **Implemented** (S4, S5): fetched, user and memory content (brand profiles) is framed as data; tools cannot change workflows or budgets. |
| T3 | **Fabricated sources or data** | Citations must reference evidence IDs created by tools, and a validator rejects unknown IDs. Numbers are computed by code. Assumptions mode is clearly labeled. | S3, S4 | **Implemented** (S4): citations are evidence ids validated against retrieved evidence; artifacts embed what they cite; `citations_verified` check; named-competitors-only guard; fact checker. |
| T4 | **SSRF** through user-supplied URLs | `http(s)` only. DNS-resolve and block private, loopback, link-local and metadata ranges, and re-check after every redirect. Cap the number of redirects. No credentials or cookies. | S4 | **Implemented** (S4): `security/url_safety.py` checks scheme, credentials, ports and every resolved address, including on every redirect. Residual risk: DNS rebinding between the check and the connection (S9). |
| T5 | **Resource exhaustion** (huge inputs, long pages, endless loops) | Length-bounded models (v1). Per-fetch size, time and content-type caps. Per-run budgets for calls, tokens, time and revisions. No unbounded loops (ADR-002). | S1, S2, S4 | Partial: input bounds (v1) and the run `Budget` (S1); fetch caps in S4 |
| T6 | **Quota draining** of the public demo | Demo mode is the default. Live mode has per-session run and token caps, a daily global cap, a concurrency limit, and a bring-your-own-key option. | S9 | n/a |
| T7 | **Secret leakage** in logs, errors, exports or the UI | A redaction filter on the package logger (key patterns, plus the actual configured key values). Safe user-facing error messages. Keys are never written to memory, checkpoints or exports. `.env` is git-ignored (v1). | S1, S9 | Partial: redaction filter, keys held as `SecretStr`, redacted provider errors (S1); every string written to memory is redacted first, verified by reading every table and index (S5) |
| T8 | **Visitor key misuse** | Visitor keys live only in Streamlit session state: never logged, never persisted, and cleared when the session ends. HTTPS only (platform-provided). | S9 | n/a |
| T9 | **Cross-tenant data access** | Every repository call needs a `Scope(org_id, user_id)`; there is no unscoped query path; tests cover isolation. The public demo stores no visitor data on the server (ADR-008). | S5 | **Implemented** (S5) for memory and the SQLite run store: every query filters on the scope; `delete_scope` and `memory forget --yes` remove a tenant's data; tests cover isolation across users and organisations, on both search paths |
| T10 | **Code execution** through generated output | The system never executes model-generated Python, shell, SQL or JavaScript. Workflow conditions are declarative predicates, never `eval`. SQL is parameterised. Exported HTML is escaped. | All | Satisfied, enforced by the architecture test (S1) |
| T11 | **Unapproved external actions** | No publishing, messaging, payment or destructive tools exist. If one is ever added, it must be marked `side_effects: external` and run behind an approval gate the engine enforces. | S2 | Satisfied: no such actions exist, and engine approval gates are in place (S2) |
| T12 | **Malicious workflow definitions** | Definitions are validated: known agents only, an acyclic graph, budgets within platform maximums, and tools within each agent's allowlist. | S2 | **Implemented** (S2): structural and registry validation of every definition |
| T13 | **Vulnerable dependencies** | `pip-audit` in CI; minimal core dependencies; optional extras isolate the heavy ones. | S9 | Missing |
| T14 | **Harmful or non-compliant marketing content** | `safety_reviewer` flags (health, finance and environmental claims, discriminatory targeting). The critic rubric includes claim substantiation. | S3, S8 | Partial (S3): the critic rubric checks on-brief and tone. Injection-echo and grounding checks are critical. `safety_reviewer` arrives in S8. |
| T15 | **Privacy exposure on free providers** | Visible data-use notice per provider. The demo discourages confidential input. Groq is the default for the public live mode (ADR-019). | S6, S9 | n/a |

## 4. Prompt-injection strategy

No defense makes an LLM immune to injection. The design therefore aims to make a successful injection **low-impact** and **detectable**:

1. **Least capability.** Agents produce text and structured data only. No tool an agent can call publishes, sends, pays or deletes.
2. **Separation.** Instructions come only from agent specs. User, tool and memory content is wrapped as labeled data.
3. **Validation.** Every output must match its schema and rules (for example, required sections, citation IDs that exist, platform length limits).
4. **Review.** The critic and safety reviewer score outputs; outputs below threshold are revised or flagged.
5. **Measurement.** The eval corpus grows from one injection case to a dedicated injection dataset (direct, indirect through fetched content, and through memory), with deterministic canaries. Results are tracked as numbers, not claims.

## 5. Public demo controls (Session 9)

All values are configurable; the defaults are tuned in Session 9.

| Control | Planned default |
| --- | --- |
| Default mode | Demo (simulated; no provider calls) |
| Live runs per browser session | 3 |
| Tokens per live run | 20,000 |
| LLM calls per live run | 20 |
| Concurrent live runs per app instance | 2 |
| Daily live-run cap (owner key) | Set so the owner key stays within its free quota |
| Input size | v1 brief bounds; uploads capped by size and type |
| Visitor key handling | Session memory only; masked input; never logged |

Caps held in process memory reset when the app restarts. That is acceptable for a demo, and the limitation is documented.

## 6. Human approval policy

- **Approval gates exist in the engine** from Session 2 (`WAITING_FOR_APPROVAL`). They are used for plan review and optional strategy sign-off.
- **External effects always need approval.** Any future step with external side effects (publishing, messaging, financial or destructive actions) requires explicit approval. Configuration cannot turn this requirement off.
- **No such step is on the roadmap.**

## 7. Security testing

- `tests/security/` (from Session 1): redaction, framing of untrusted content and the architecture rules are in place. SSRF blocking (S4), definition validation (S2) and tenant isolation (S5) are added as those features land.
- An injection eval dataset (Session 8). Live runs are opt-in; CI uses offline canaries.
- `pip-audit` in CI (Session 9).
- An architecture test that forbids `eval`/`exec`/`compile`, `os.system`, and imports of `subprocess`/`pickle`/`marshal` in the package (in place since Session 1).
