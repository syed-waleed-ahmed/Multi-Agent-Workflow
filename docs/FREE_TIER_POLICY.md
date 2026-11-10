# Free-Tier Policy

Campaign Forge must be fully usable without paid subscriptions, paid APIs or a credit card. This document has two parts:

- **The rules** (section 1). These are stable.
- **The verified facts behind them** (sections 3-6). These change, so each fact carries its date and source, and is re-checked on the schedule in section 7.

- **Last verified:** 2026-11-10 (Session 0)
- **Verification method:** official documentation, pricing and terms pages, found through web search. Facts that could only be found in third-party sources, or that the official pages contradict, are marked **UNVERIFIED** and are never used as hard-coded limits.

---

## 1. Rules (binding for all sessions)

1. **No paid dependency is ever mandatory.** This covers LLMs, search, vector databases, image generation, monitoring, hosting and agent frameworks.
2. **No credit card is needed for the core deployment.** Services that need a card to activate (for example Brave Search API) are excluded from defaults, and documented as optional at most.
3. **No automatic paid usage.** The code never enables billing, never targets paid-only models in `FREE_ONLY` mode, and never upgrades a plan.
4. **No silent model or provider switching.** A fallback provider is used only if the operator lists it explicitly, and every artifact records the provider and model that actually ran (ADR-010).
5. **Bounded retries only.** The system honours `Retry-After`, uses bounded exponential backoff, and never retries indefinitely.
6. **When a quota is exhausted, the run pauses and keeps its state.** It does not fail away its completed work. The user gets a clear quota message and can resume later.
7. **Free quotas are not assumed to be stable.** Limits live in provider profiles (configuration), not in code. The engine's own budgets protect the user whatever the provider allows.
8. **Private data stays out of the public demo.** Some free tiers allow the provider to use prompts to improve its products (see section 3). The demo says so visibly and discourages confidential input.
9. **Live LLM calls are never part of mandatory CI.**

## 2. What `FREE_ONLY` mode guarantees, and what it cannot

`FREE_ONLY=true` is the default. In this mode Campaign Forge:

- only calls provider profiles marked `free_tier: true` in configuration;
- only calls models on that profile's free-model allowlist;
- refuses to start a run that would need a paid-only model, and explains why.

Since Session 2, `FREE_ONLY` covers every entry point, including the v1
`campaign-forge run` and `batch` commands (ADR-023). Only code that constructs
the legacy `LLMClient` directly bypasses the router.

What it **cannot** do is see your provider account's billing status. If billing is enabled on the account behind your API key, the provider may bill usage above the free allowance. To stay strictly free, use keys from accounts **with no billing method attached**, or with a provider-side spend limit of zero where one exists. This limitation is stated in the README and in the app's settings page.

---

## 3. LLM providers (verified 2026-11-10)

| Provider | Free tier, no card | Free models | Free limits | Data use on the free tier | OpenAI-compatible endpoint |
| --- | --- | --- | --- | --- | --- |
| **Groq** | Yes (free plan) | **Verified live 2026-11-10:** `qwen/qwen3.8-27b` (default), `openai/gpt-oss-20b`, `openai/gpt-oss-120b`, `allam-2-7b`. `llama-3.1-8b-instant` is retired (HTTP 404). | **Verified live from response headers:** 8,000 tokens per minute and 1,000 requests per day, per model. **From a 429 message:** `qwen/qwen3.8-27b` also has 200,000 tokens per day. | **UNVERIFIED** (not confirmed this session) | Yes, `https://api.groq.com/openai/v1`; JSON mode and `usage` verified live |
| **Google Gemini** (AI Studio) | Yes; the free tier needs no linked billing account | "Limited access to certain models"; Flash-Lite models are listed as "Free of charge" | Per project, not per key; RPD resets at midnight Pacific time; Google says it does not guarantee its published limits. Forum reports of 20 RPD on some Flash models (**UNVERIFIED**). | Free-tier data **is used to improve Google products and may be read by human reviewers.** Only paid services may be offered to users in the **EEA, Switzerland and the UK**. | Yes, `https://generativelanguage.googleapis.com/v1beta/openai/`. `json_schema` behaviour reportedly varies by schema features and mode, so it must be tested per model. |
| **Mistral** | Yes, the "Experiment" free plan. The pricing page separately mentions monthly free API credits (the two pages conflict). | Many API models; some (e.g. Devstral 2) have moved to paid | Per organisation: requests per second, tokens per minute and tokens per month; the free mode has the lowest limits | **Conflicting.** The help centre says data is used for training by default unless you opt out (Admin > Privacy). The API documentation says API data is not used for training. Treat it as "may be used" and opt out. | Yes (OpenAI-style chat completions); to be confirmed in Session 1 |
| **Cloudflare Workers AI** | Yes, on the Workers Free plan | Many models; some are paid-only, and on the free plan those return HTTP 403 (internal error 5035) | **10,000 Neurons per day**, reset at 00:00 UTC. When exhausted: HTTP 429, "Account limited" (internal error 3036). | Not checked this session (**UNVERIFIED**) | Yes, `https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1` (`/chat/completions`, `/embeddings`) with a Cloudflare API token |

Sources:
- Groq: [rate limits](https://console.groq.com/docs/rate-limits), [billing FAQs](https://console.groq.com/docs/billing-faqs)
- Gemini: [pricing](https://ai.google.dev/gemini-api/docs/pricing), [rate limits](https://ai.google.dev/gemini-api/docs/rate-limits), [billing](https://aistudio.google.com/docs/billing), [terms](https://aistudio.google.com/docs/terms), [OpenAI compatibility](https://ai.google.dev/gemini-api/docs/openai)
- Mistral: [pricing](https://mistral.ai/pricing/), [rate limits](https://help.mistral.ai/en/articles/698531-why-am-i-hitting-api-rate-limits-and-how-do-i-increase-them), [training opt-out](https://help.mistral.ai/en/articles/455207-can-i-opt-out-of-my-input-or-output-data-being-used-for-training), [Vibe 2.0 note on the Experiment plan](https://mistral.ai/news/mistral-vibe-2-0/)
- Cloudflare: [Workers AI pricing](https://developers.cloudflare.com/workers-ai/platform/pricing/), [errors](https://developers.cloudflare.com/workers-ai/platform/errors/), [OpenAI compatibility](https://developers.cloudflare.com/workers-ai/configuration/open-ai-compatibility/)

### Consequences for the design

- **Groq is the default provider for the public live mode** (ADR-019). It is already integrated and tested, it is fast, it needs no card, and no regional restriction was found. The master prompt suggested Gemini as primary, but two of Google's own terms make that unsuitable for an open public demo:
  - visitor data could be used to improve Google products and read by human reviewers;
  - free services may not be offered to users in the EEA, Switzerland or the UK.

  Gemini stays a first-class, configurable provider for local use and for visitors' own keys.
- **Model IDs are not hard-coded from this table.** Free model lists change often (one Gemini model is already scheduled to be re-routed to a newer one on 2026-12-01). Session 1 resolves each profile's `small`, `standard` and `strong` aliases against the provider's live `/models` endpoint, and records the result with a date.
- **Groq output-token cap (verified live 2026-11-10).** `qwen/qwen3.8-27b` has an output-token limit of 1,000 per minute, enforced against each request's `max_tokens` before any output is produced. A larger request is rejected with "Request too large", so requests are clamped to 1,000 (ADR-030). In practice this allows about one call per minute, and a `full-campaign` run takes roughly 15 minutes on this tier. This limit does not appear in the `x-ratelimit-*` headers.
- **Groq daily quota and the strong model (verified live 2026-11-10, ADR-032).**
  - **Daily quota.** `qwen/qwen3.8-27b` has a rolling 200,000-token daily quota. A day of development runs used it up, so a free-tier day supports only a handful of live full campaigns.
  - **Strong model.** `openai/gpt-oss-120b` returned 2,500 output tokens in one request (11 reasoning tokens at `reasoning_effort: low`), so it does not share qwen's 1,000-token output cap. Long-output agents (the v1 manager, the campaign reporter) use it. It also has its own daily quota.
  - **Concurrency.** Groq requests are serialised (`max_concurrency` 1), because parallel requests under the output cap only displace each other.
- **A `QuotaExhausted` error is not the same as a `RateLimited` error.**
  - `RateLimited` (per minute): wait for `Retry-After` and retry, within bounds.
  - `QuotaExhausted` (per day or month, e.g. Cloudflare 3036, Groq TPD or RPD, Gemini RPD): do not retry; pause the run.

  Provider profiles carry the message patterns used to classify a response.

---

## 4. Hosting (verified 2026-11-10)

| Option | Free? | Key limits | Verdict |
| --- | --- | --- | --- |
| **Streamlit Community Cloud** | Yes, public apps only | About 0.078-2 CPU cores, 690 MB-2.7 GB RAM, up to 50 GB storage (Streamlit's figures as of Feb 2024). Sleeps after **12 h** without traffic and wakes when a visitor clicks. Secrets entered as TOML in settings, read with `st.secrets`. Default Python 3.12. Personal-data apps limited to personal, non-commercial use. | **Primary public demo host** |
| Streamlit Cloud filesystem persistence | No | Runtime files, including SQLite, are lost on restart (forum answer, **UNVERIFIED** in official docs) | Treat local storage as a throwaway cache |
| Streamlit Cloud second server (FastAPI) | No | Not supported, per a forum answer (**UNVERIFIED**) | FastAPI stays local or Docker (ADR-006) |
| **Hugging Face Spaces** | **No longer free for new Docker or Gradio Spaces** | Creating a Gradio or Docker Space now needs a paid plan; only Static Spaces are free. Streamlit would need Docker. | Excluded as a free host |
| **Cloudflare Workers** (Python, GA since 2026-09-21) | Yes | Free plan: **10 ms CPU per request**, 128 MB memory, 50 subrequests per invocation, 100,000 requests per day | Unsuitable for orchestration; at most a thin proxy. Not planned. |
| **Supabase** | Free plan | 500 MB database per project, 1 GB file storage, 5 GB egress, **pauses after 1 week of inactivity**, 2 free projects. Card requirement **UNVERIFIED**. | Optional persistence for self-hosters only; never required |
| **GitHub Actions** | Free for public repositories | Standard runner limits | CI, offline gates only |

Sources:
- Streamlit: [Cloud](https://streamlit.io/cloud), [manage your app (resources, sleep)](https://docs.streamlit.io/deploy/streamlit-community-cloud/manage-your-app), [secrets](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/secrets-management), [deploy](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy), [terms](https://streamlit.io/deployment-terms-of-use)
- Supabase: [pricing](https://supabase.com/pricing), [billing](https://supabase.com/docs/guides/platform/billing-on-supabase)
- Hugging Face: [Spaces overview](https://huggingface.co/docs/hub/spaces-overview), [hardware](https://huggingface.co/docs/hub/spaces-gpus)
- Cloudflare: [Workers limits](https://developers.cloudflare.com/workers/platform/limits/), [Python Workers GA](https://blog.cloudflare.com/python-workers-ga/)

---

## 5. Research sources (verified 2026-11-10)

| Source | Free allowance | Card | Notable terms | Role |
| --- | --- | --- | --- | --- |
| **User-supplied sources** (pasted text, files, URLs) | Unlimited | No | The user is responsible for rights to the content | **Always available; the baseline** |
| **Direct URL fetch** (own code, `httpx`) | Unlimited | No | Honour `robots.txt`; no bypassing access controls | Default for user-supplied URLs |
| **Wikimedia / Wikipedia API** | 10 requests/min for requests identified only by IP; 200 requests/min for bots with a compliant User-Agent; at most 3 concurrent requests | No | A descriptive User-Agent with contact details is required; generic ones get 403 | Keyless background research |
| **Tavily** | 1,000 credits/month (basic search = 1 credit) | **No** | No reselling; no training competing models; no "automated decisions without human oversight" | **Optional search adapter (user's own key)** |
| **Jina Reader** | 20 requests/min without a key, more with a free key (its page's figures conflict) | No | Does not bypass anti-bot systems; you stay responsible for the fetched sites' terms | Optional extraction fallback (sends URLs to a third party, so off by default) |
| SearXNG (self-hosted) | Free, open source | No | JSON output must be enabled; upstream engines may block it | Optional adapter for self-hosters |
| Brave Search API | Monthly credits, but **needs a card to activate** | **Yes** | Storing results needs a plan with storage rights | **Excluded** (rule 2) |
| Google Custom Search JSON API | **Closed to new customers** (existing ones until 2027-01-01) | - | - | **Excluded** |
| Bing Search API | **Retired 2025-08-11** | - | - | **Excluded** |
| DuckDuckGo | No official full web-search API found | - | Stance on automated querying unclear | **Excluded** (no scraping) |
| Gemini Grounding with Google Search | Free only on older Flash models with daily caps; paid-only on newer models | No | Search Suggestions must be displayed; no caching; free-tier data use applies | **Excluded** (terms incompatible with the evidence store, and models are being retired) |

Sources:
- Tavily: [pricing](https://www.tavily.com/pricing), [credits](https://docs.tavily.com/documentation/api-credits), [terms](https://www.tavily.com/terms)
- Brave: [Search API](https://brave.com/search/api/), [quickstart](https://api-dashboard.search.brave.com/documentation/quickstart)
- Google: [Custom Search overview](https://developers.google.com/custom-search/v1/overview)
- Microsoft: [Bing Search API retirement](https://learn.microsoft.com/en-us/lifecycle/announcements/bing-search-api-retirement)
- Wikimedia: [rate limits](https://www.mediawiki.org/wiki/Wikimedia_APIs/Rate_limits), [User-Agent policy](https://foundation.wikimedia.org/wiki/Policy:Wikimedia_Foundation_User-Agent_Policy)
- Jina: [Reader](https://jina.ai/reader/)
- SearXNG: [search API](https://docs.searxng.org/dev/search_api.html)
- Gemini: [pricing](https://aistudio.google.com/docs/pricing), [terms](https://aistudio.google.com/docs/terms)

**Implemented in Session 4 (ADR-029):** Wikipedia is the default search tool (`CF_RESEARCH_TOOLS=wikipedia`), Tavily is opt-in with `TAVILY_API_KEY`, and user files and URLs are supplied with `--sources`. Jina Reader and SearXNG are not implemented.

**Default research configuration:**
- **Public demo:** user-supplied sources plus Wikipedia.
- **Local:** the same, plus Tavily if the user sets `TAVILY_API_KEY`.

When no source is available, agents run in labeled **assumptions mode** (see [ARCHITECTURE.md](ARCHITECTURE.md), section T8).

---

## 6. Public demo modes

| Mode | Provider | Key | Labeling | Limits |
| --- | --- | --- | --- | --- |
| **Demo** (default) | `FakeProvider` with recorded fixtures | None | Every output is marked **SIMULATED**; fixture provenance is shown | None needed |
| **Live, owner key** | Groq (owner's free key from Streamlit secrets) | Owner's | Shows provider, model and data-use notice | Per-session and daily caps (see [SECURITY.md](SECURITY.md)) |
| **Live, visitor key** | Any supported provider | Visitor's, kept only in session memory | Shows provider, model and that provider's data-use notice | Per-session caps; key never logged or stored |

---

## 7. Re-verification schedule

- **When:** at the start of Sessions 1, 6 and 10 (providers, hosting and research respectively), and before any public release.
- **How:** update the tables above with the new date and sources, and record every change that affects behaviour in [DECISIONS.md](DECISIONS.md).
- **If a default service stops being free:** change its profile to `free_tier: false`. `FREE_ONLY` mode then refuses it automatically, and the next session plans a replacement.
