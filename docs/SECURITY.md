# Security Model

This document covers the threat model and planned security controls for Campaign Forge 2.0, and records which controls exist today in v1.1.

- **Status:** designed in Session 0 and implemented across Sessions 1-9: the primitives in Session 1, tool sandboxing in Session 4, tenant isolation in Session 5, the API controls in Session 7, and the public-demo hardening and live mode in Session 9. The threat table records each control's status.
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
| T1 | **Direct prompt injection** through brief fields | Untrusted text goes into prompts inside delimited, labeled data blocks with a "data, not instructions" policy in the system prompt. Outputs are validated against schemas. Deterministic canary checks run in evals. Agents have no tools that can act in the world. | S1, S9 | **Implemented** (S1, S9): framing and policy in every agent; in S9 the framing also removes invisible and bidirectional characters (including Unicode tag characters), bare marker phrases and chat-template tokens, and the canary check normalises text first. A 14-case injection dataset runs offline in CI. |
| T2 | **Indirect prompt injection** through fetched pages, files or memory | Same framing, plus provenance labels. Tool output can never change the workflow graph, tool permissions or budgets; the planner reads only validated brief data. | S4, S5 | **Implemented** (S4, S5): fetched, user and memory content (brand profiles) is framed as data; tools cannot change workflows or budgets. |
| T3 | **Fabricated sources or data** | Citations must reference evidence IDs created by tools, and a validator rejects unknown IDs. Numbers are computed by code. Assumptions mode is clearly labeled. | S3, S4 | **Implemented** (S4): citations are evidence ids validated against retrieved evidence; artifacts embed what they cite; `citations_verified` check; named-competitors-only guard; fact checker. |
| T4 | **SSRF** through user-supplied URLs | `http(s)` only. DNS-resolve and block private, loopback, link-local and metadata ranges, and re-check after every redirect. Cap the number of redirects. No credentials or cookies. | S4 | **Implemented** (S4): `security/url_safety.py` checks scheme, credentials, ports and every resolved address, including on every redirect. Residual risk: DNS rebinding between the check and the connection. The public app never fetches user URLs (live mode searches Wikipedia only), so the risk applies to the CLI and the API with user-supplied URLs. |
| T5 | **Resource exhaustion** (huge inputs, long pages, endless loops) | Length-bounded models (v1). Per-fetch size, time and content-type caps. Per-run budgets for calls, tokens, time and revisions. No unbounded loops (ADR-002). | S1, S2, S4 | Partial: input bounds (v1) and the run `Budget` (S1); fetch caps in S4 |
| T6 | **Quota draining** of the public demo | Demo mode is the default. Live mode has per-session run and token caps, a daily global cap, a concurrency limit, and a bring-your-own-key option. | S9 | **Implemented** (S9): demo by default; live runs capped per run, per session, per day and in concurrency (section 5) |
| T7 | **Secret leakage** in logs, errors, exports or the UI | A redaction filter on the package logger (key patterns, plus the actual configured key values). Safe user-facing error messages. Keys are never written to memory, checkpoints or exports. `.env` is git-ignored (v1). | S1, S9 | Partial: redaction filter, keys held as `SecretStr`, redacted provider errors (S1); every string written to memory is redacted first, verified by reading every table and index (S5). In S9: the app configures redacting logging; the engine, the run service and the Lab redact every error message they record; a test drives a live session whose provider echoes the key and checks logs, errors, run records, exports and memory. |
| T8 | **Visitor key misuse** | Visitor keys live only in Streamlit session state: never logged, never persisted, and cleared when the session ends. HTTPS only (platform-provided). | S9 | **Implemented** (S9): masked field in a form that clears on submit; Groq key shape required, so key-pattern redaction always matches; held only by the live session's settings (`SecretStr`); "Forget my key" drops the session. Live sessions use isolated settings, so a visitor run can never reach the deployment's other keys, fallbacks or overrides. |
| T9 | **Cross-tenant data access** | Every repository call needs a `Scope(org_id, user_id)`; there is no unscoped query path; tests cover isolation. The public demo stores no visitor data on the server (ADR-008). | S5 | **Implemented** (S5) for memory and the SQLite run store: every query filters on the scope; `delete_scope` and `memory forget --yes` remove a tenant's data; tests cover isolation across users and organisations, on both search paths |
| T10 | **Code execution** through generated output | The system never executes model-generated Python, shell, SQL or JavaScript. Workflow conditions are declarative predicates, never `eval`. SQL is parameterised. Exported HTML is escaped. | All | Satisfied, enforced by the architecture test (S1) |
| T11 | **Unapproved external actions** | No publishing, messaging, payment or destructive tools exist. If one is ever added, it must be marked `side_effects: external` and run behind an approval gate the engine enforces. | S2 | Satisfied: no such actions exist, and engine approval gates are in place (S2) |
| T12 | **Malicious workflow definitions** | Definitions are validated: known agents only, an acyclic graph, budgets within platform maximums, and tools within each agent's allowlist. | S2 | **Implemented** (S2): structural and registry validation of every definition |
| T13 | **Vulnerable dependencies** | `pip-audit` in CI; minimal core dependencies; optional extras isolate the heavy ones. | S9 | **Implemented** (S9): a CI job audits the pinned runtime dependencies of every extra |
| T14 | **Harmful or non-compliant marketing content** | `safety_reviewer` flags (health, finance and environmental claims, discriminatory targeting). The critic rubric includes claim substantiation. | S3, S8 | Partial (S3): the critic rubric checks on-brief and tone. Injection-echo and grounding checks are critical. `safety_reviewer` arrives in S8. |
| T15 | **Privacy exposure on free providers** | Visible data-use notice per provider. The demo discourages confidential input. Groq is the default for the public live mode (ADR-019). | S6, S9 | **Implemented** (S9): live mode is Groq only, and its page warns that submissions go to Groq under Groq's terms and must not be confidential |

## 4. Prompt-injection strategy

No defense makes an LLM immune to injection. The design therefore aims to make a successful injection **low-impact** and **detectable**:

1. **Least capability.** Agents produce text and structured data only. No tool an agent can call publishes, sends, pays or deletes.
2. **Separation.** Instructions come only from agent specs. User, tool and memory content is wrapped as labeled data.
3. **Validation.** Every output must match its schema and rules (for example, required sections, citation IDs that exist, platform length limits).
4. **Review.** The critic and safety reviewer score outputs; outputs below threshold are revised or flagged.
5. **Measurement.** A dedicated injection dataset (14 cases since Session 9: direct, indirect through the user's sources and a stored brand profile, forged and bare delimiters, chat-template tokens, hidden characters, an exfiltration link, another language) with deterministic canaries. Results are tracked as numbers, not claims; CI runs it offline against a baseline.

## 5. Public demo controls (Session 9)

All values are configurable (ADR-037). The Session 0 plan proposed 20,000 tokens and 20 calls per live run. Session 9 tuned them to measured runs: a live `full-campaign` used 19-21 calls and 26,535-27,129 tokens, so the planned caps would stop the flagship workflow every time.

| Control | Default | Setting |
| --- | --- | --- |
| Default mode | Demo (simulated; no provider calls) | `CF_UI_MODE=demo` |
| Live mode on the owner's key | Off until the owner opts in | `CF_LIVE_OWNER_KEY` (plus `GROQ_API_KEY`) |
| Live mode on a visitor's own Groq key | On | `CF_LIVE_VISITOR_KEYS` |
| Owner-key live runs per browser session | 3 | `CF_LIVE_SESSION_RUNS` |
| Owner-key live runs per UTC day, per app process | 5 (5 x 30,000 tokens stays under Groq's free 200,000 per day) | `CF_LIVE_DAILY_RUNS` |
| Live runs executing at once, per app process (any key) | 2 | `CF_LIVE_CONCURRENT_RUNS` |
| Model calls per live run | 25 | `CF_LIVE_RUN_CALLS` |
| Tokens per live run | 30,000 | `CF_LIVE_RUN_TOKENS` |
| Wall time per live run | 20 minutes | `CF_LIVE_RUN_SECONDS` |
| Input size | v1 brief bounds; uploads capped by size and type | - |
| Visitor key handling | Session memory only; masked input; never logged | - |

- **How the caps apply.** Per-run caps tighten the plan's budget and never loosen it, and they are applied again when the run starts, whatever the page sent. A run that reaches a cap pauses with its finished steps kept. Starts refused by a cap or a full slot are not counted.
- **Limitations.** Counters live in process memory: they reset when the app restarts, and a page reload starts a new browser session, so the per-session cap is a courtesy limit. The daily cap and the concurrency limit are the hard bounds. Beneath them is the free tier itself: no card is on file, so the worst case is a used-up daily quota, never a bill.

### Implemented in Session 9

- **Live mode** (ADR-037): Groq with the owner's key (opt-in) or the visitor's own; storage in session memory as in demo mode; the Evaluation Lab stays simulated in the public app.
- **Isolated settings.** A live session is built from code-given values only (provider, key, Wikipedia research, the deployment's Groq model settings). It never reads the environment, so the deployment's other keys, fallback providers and routing overrides are unreachable.
- **Owner key from Streamlit secrets.** Streamlit exports root-level secrets to the environment at startup; `Settings()` reads them there.
- **Errors and logs.** The app configures the package logger with the redacting filter. The engine, the run service and the Lab redact every error message they record (provider errors already were); a live test found that an unexpected exception's text reached the run record unredacted, and that path is now fixed.
- **D6 hardening.** See T1 and section 4.

### Implemented in Session 6

- **Demo mode stores nothing on the server.** Runs, brands and notes live in the browser session's memory only (ADR-008).
- **Model text cannot inject markup.** Streamlit renders Markdown without raw HTML, and the HTML export disables raw HTML and refuses `javascript:` links.
- **No chain of thought.** Inline reasoning blocks are stripped from provider responses before parsing or storage.
- **Uploads.** Uploads are capped at 2 MB (`.streamlit/config.toml`) and limited to JSON or text types. Text sources are truncated to the source size cap. The app never fetches URLs.
- **No error detail in the browser.** `client.showErrorDetails = false`, so tracebacks are not shown.
- **Secrets.** `.streamlit/secrets.toml` is git-ignored. Keys appear only as "set" or "not set" on the health page.

### Implemented in Session 7 (API)

- **Closed by default.** Every `/api/v1` endpoint needs an `X-API-Key` that matches `CF_API_KEYS`, compared in constant time. With no keys configured, every request gets 401. Keys are secrets: they are redacted like provider keys, and the health page shows them only as set or not set.
- **One tenant per key.** A key's runs, custom workflows and memory are invisible to other keys (tests cover this).
- **Limits.** Requests and run starts are limited per key, and active runs per process (429 or 503 with `Retry-After`). Plan options can only lower a workflow's budget, never raise it.
- **Errors leak nothing.** Messages are redacted, and unexpected errors return a generic 500 with no traceback.
- **Container.** The image runs as a non-root user with a healthcheck. Compose binds to `127.0.0.1`; a TLS proxy is required before exposing the service.

## 6. Human approval policy

- **Approval gates exist in the engine** from Session 2 (`WAITING_FOR_APPROVAL`). They are used for plan review and optional strategy sign-off.
- **External effects always need approval.** Any future step with external side effects (publishing, messaging, financial or destructive actions) requires explicit approval. Configuration cannot turn this requirement off.
- **No such step is on the roadmap.**

## 7. Security testing

- `tests/security/` (from Session 1): redaction, framing of untrusted content and the architecture rules are in place. SSRF blocking (S4), definition validation (S2) and tenant isolation (S5) are added as those features land.
- An injection eval dataset (Session 8; 14 cases since Session 9). Live runs are opt-in; CI uses offline canaries and a baseline.
- `pip-audit` in CI (Session 9).
- Live-mode tests (Session 9, `tests/test_live.py` and the app tests): caps per run, session and day; slots shared across sessions; refused starts not counted; isolated settings; visitor-key handling; and no key in logs, errors, run records, exports or memory when a provider echoes it or an unexpected error contains it.
- An architecture test that forbids `eval`/`exec`/`compile`, `os.system`, and imports of `subprocess`/`pickle`/`marshal` in the package (in place since Session 1).
