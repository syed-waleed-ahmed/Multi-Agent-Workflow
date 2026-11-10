# Agent Catalog

This is the planned catalog of specialised agents for Campaign Forge 2.0. It covers
how agents are defined, which ones are built in which order, and how the 37 agent
names requested in the master prompt map onto a smaller set of agents. Each agent in
that set has a distinct, testable output.

- **Status:** wave 1 is **implemented** (Session 3, 2026-11-10): all 11 agents are in `campaign_forge.agents.library` and their output contracts in `campaign_forge.agents.schemas`. Wave 2 is still planned.
- **Delivery order:** Wave 1 is Session 3; wave 2 is spread across Sessions 4, 5 and 8.
  See [ROADMAP.md](ROADMAP.md).

---

## 1. Catalog principles

1. **One agent per distinct output contract.** An agent exists only if it produces its
   own artifact kind. Variations of the same output (tone, length, platform) are
   parameters, not new classes. This is why 37 requested names become 28 agents
   (ADR-012).
2. **Facts about the outside world need evidence.** Agents that make claims about
   competitors, trends or SEO data use the research tools (Session 4). Without
   evidence they run in labeled **assumptions mode**, and they never invent sources,
   competitor metrics, traffic or keyword volumes.
3. **Agents that need data refuse to run without it.** A performance analysis without
   user-supplied metrics is not attempted; the planner reports the missing input
   instead.
4. **Numbers are computed by code, not by the model** (ADR-011). This covers budget
   splits, platform character limits, sample sizes, performance ratios, calendar
   dates and ICE scores. The model explains the numbers; it does not calculate them.
5. **Definition of done for every agent:**
   - an `AgentSpec`;
   - a versioned prompt;
   - a Pydantic output model;
   - output validation rules;
   - unit tests with `FakeProvider` (happy path, malformed output, repair, refusal or
     empty-input cases);
   - at least one eval dataset entry and a rubric;
   - a catalog entry here.

   No empty classes, and no duplicated prompts dressed up as distinct agents.
6. **Wave 1 must be stable before wave 2 starts.** Quality comes before agent count.

## 1a. Implementation notes (Session 3)

Decisions made while building wave 1 are recorded in ADR-026 and ADR-027:

- **Context passing.** Agents receive compact **digests** of upstream artifacts, not
  full JSON, so each request stays inside free-tier tokens-per-minute limits.
- **Contracts are code.** The output contracts carry their own rules. A violation
  (for example a Google Search headline over 30 characters, a post outside the
  calendar, an image prompt under 8 words) becomes the exact error that the repair
  loop sends back to the model.
- **Numbers by code.** Effort shares (largest-remainder rounding to 100%),
  calendar weeks, ordering, hashtag normalisation and limit warnings are all
  computed in code.
- **Research is assumptions-only.** The research contracts offer only
  `user_provided` and `model_assumption` as a basis, so the model cannot claim
  external research before Session 4 adds tools.
- **The critic** runs the deterministic checks of
  `campaign_forge.evaluation.checks` first. Any critical failure caps its score at
  0.40. The model scores the kind's rubric 1-5, and code maps that to 0-1.
- **The report.** `campaign_reporter` has the model write only the narrative
  (six validated sections, written as Markdown rather than JSON). Code appends
  the exact deliverables, every model assumption, and a list of what the run did
  not produce.
- **Ads** are written for at most 6 channels, ad-capable platforms first. Code
  chooses the channels, so the prompt and the 12-variant contract agree.
- **Templates.** The focused templates (`ad-pack`, `social-calendar`,
  `email-sequence`) all start with audience research, positioning and reviewed
  core copy, because every content agent needs the core copy.

## 2. Agent specification (every agent)

```text
id                 snake_case, unique, stable          e.g. "ad_copy"
name, description  catalog / marketplace metadata
version            semantic version of the agent
prompt_version     bumped on every prompt change (eval comparisons)
category           research | strategy | content | seo | creative | analytics | quality | synthesis
capabilities       tags used by the planner and the UI
inputs             required / optional artifact kinds
output             artifact kind + Pydantic model
allowed_tools      tool allowlist (default: none)
provider_preference  model class: small | standard | strong (+ optional profile)
limits             max_tokens, max_calls, timeout_s, max_repairs
rubric             evaluation criteria (deterministic checks + optional judge criteria)
```

Agents register with a decorator inside the package, or through the
`campaign_forge.agents` entry-point group for third-party packages. The orchestrator
never changes when an agent is added.

## 3. The catalog

**Wave** column: **W1** = Session 3 (Milestone 1). Other values give the session where
the agent lands.

### Research

| ID | Output artifact | Purpose | Tools | Wave | Replaces / absorbs |
| --- | --- | --- | --- | --- | --- |
| `audience_research` | `research.audience@1` | Segments, personas, pain points, desires and objections, each claim labeled with its basis | user sources (W1); Wikipedia and search (S4) | W1 | v1 `ResearchAgent` (part), AudienceResearchAgent |
| `market_research` | `research.market@1` | Category dynamics, trends and positioning angles | as above | W1 (assumptions mode); S4 (evidence-backed) | v1 `ResearchAgent` (part), MarketResearchAgent, TrendResearchAgent |
| `competitor_research` | `research.competitors@1` | Competitor profiles and a comparison matrix, covering only competitors the user named or that appear in evidence | user sources, URL fetch, search | S4 | CompetitorResearchAgent; competitor comparison reports |

### Strategy

| ID | Output artifact | Purpose | Tools | Wave | Replaces / absorbs |
| --- | --- | --- | --- | --- | --- |
| `positioning` | `strategy.positioning@1` | Positioning statement, value propositions, feature-to-benefit ladder, messaging pillars, proof points needed | - | W1 | PositioningAgent, ProductResearchAgent |
| `marketing_strategist` | `strategy.plan@1` | Objectives, KPIs, channel mix with rationale, phases. If the user gives a budget, code computes the allocation from model-proposed weights. | - | W1 | MarketingStrategistAgent |
| `brand_strategist` | `brand.guidelines@1` | Voice and tone, do and don't lists, vocabulary, visual identity direction; written to Brand Memory | memory | S5 | BrandStrategistAgent, BrandIdentityAgent |
| `funnel_strategist` | `strategy.funnel@1` | Funnel stages, content per stage, conversion points, lead-magnet concepts | - | S8 | FunnelStrategyAgent; lead magnets |
| `growth_strategist` | `strategy.growth@1` | Growth experiment backlog. The model proposes impact, confidence and ease; code computes ICE scores and ranks. | - | S8 | GrowthStrategyAgent |

### Content

| ID | Output artifact | Purpose | Tools | Wave | Replaces / absorbs |
| --- | --- | --- | --- | --- | --- |
| `copywriter` | `content.core_copy@1` | Tagline, primary message, headlines, body copy, CTA | - | W1 (upgraded from v1) | v1 `CopywriterAgent` |
| `ad_copy` | `content.ads@1` | Ad variants per platform, with character limits checked by code against a limits table | - | W1 | AdCopyAgent; advertising campaigns |
| `social_media` | `content.social@1` | Platform-specific posts plus a content calendar (dates computed by code from start date and cadence) | - | W1 | SocialMediaAgent; content calendars |
| `email_marketing` | `content.email_sequence@1` | Email sequence: subject, preview text, body, CTA and send-day offset for each email | - | W1 | EmailMarketingAgent |
| `blog_writer` | `content.article@1` | Long-form article, generated outline-first and then section by section to fit free-tier token limits | user sources | S8 | BlogWriterAgent |
| `landing_page` | `content.landing_page@1` | Landing-page copy section by section (hero, benefits, proof, FAQ, CTA) | - | S8 | LandingPageAgent |
| `product_description` | `content.product_descriptions@1` | Short, long and bullet descriptions per marketplace style | - | S8 | ProductDescriptionAgent; e-commerce |
| `repurposer` | `content.repurposed@1` | Turns an existing artifact or user text into other formats | user sources | S8 | Content repurposing |

### SEO

| ID | Output artifact | Purpose | Tools | Wave | Replaces / absorbs |
| --- | --- | --- | --- | --- | --- |
| `keyword_research` | `seo.keywords@1` | Seed keywords, search intent and topic clusters. Search volumes come only from a user-supplied CSV; otherwise the field is empty and labeled. | user sources | S8 | KeywordResearchAgent; keyword clustering |
| `seo_content_planner` | `seo.content_plan@1` | Content briefs, outlines, internal-link ideas, an SEO calendar | - | S8 | SEOContentPlannerAgent |
| `seo_optimizer` | `seo.audit@1` | Deterministic on-page checks computed by code (title and meta length, headings, word count, keyword presence), plus LLM recommendations; input is a URL or text | URL fetch | S8 | SEOAuditAgent, ContentOptimizationAgent |

### Creative

| ID | Output artifact | Purpose | Tools | Wave | Replaces / absorbs |
| --- | --- | --- | --- | --- | --- |
| `art_director` | `creative.visual_concepts@1` | Visual concepts with image-generation prompts; at least 3 concepts enforced (fixes defect D3) | - | W1 (upgraded from v1) | v1 `ArtDirectorAgent`, VisualPromptAgent |

### Analytics

| ID | Output artifact | Purpose | Tools | Wave | Replaces / absorbs |
| --- | --- | --- | --- | --- | --- |
| `performance_analyst` | `analytics.performance@1` | Code computes CTR, CVR, CPC, CPA and ROAS from user-supplied CSV; the LLM writes a narrative and recommendations citing only the computed numbers. Disabled when no data is given. | CSV ingestion | S8 | CampaignAnalyticsAgent, PerformanceReviewAgent |
| `experiment_designer` | `analytics.experiment_plan@1` | Hypotheses, variants, primary metric; code computes the sample size from baseline rate and minimum detectable effect | - | S8 | A/BTestingAgent |
| `cro_advisor` | `analytics.cro@1` | Conversion recommendations for a landing page (URL or text): a heuristic checklist computed by code, plus LLM reasoning | URL fetch | S8 (lowest priority) | ConversionOptimizationAgent |

### Quality

| ID | Output artifact | Purpose | Tools | Wave | Replaces / absorbs |
| --- | --- | --- | --- | --- | --- |
| `critic` | `quality.review@1` | Rubric scores per criterion, issues and revision instructions. Deterministic checks run first, then the LLM judge. Drives the bounded revision loop. | - | W1 | ContentQualityAgent, CreativeReviewAgent, the master prompt's "Reviewer Agent" |
| `fact_checker` | `quality.fact_check@1` | Checks claims against the run's evidence store and marks each one supported, unsupported or contradicted | evidence store | S4 | FactCheckingAgent |
| `brand_consistency` | `quality.brand_check@1` | Checks content against brand guidelines: deterministic term and vocabulary checks, plus an LLM tone check | memory | S5 | BrandConsistencyAgent; brand consistency checking |
| `safety_reviewer` | `quality.safety@1` | Flags regulated or risky claims (health, finance, environmental "green" claims, unproven superlatives, discriminatory targeting). It raises flags only and gives no legal advice. | - | S8 | SafetyReviewAgent |

### Synthesis

| ID | Output artifact | Purpose | Tools | Wave | Replaces / absorbs |
| --- | --- | --- | --- | --- | --- |
| `campaign_reporter` | `report.campaign_brief@1` | Final hand-off document built from all artifacts, with required sections validated (fixes defect D4) and assumptions labeled | - | W1 (upgraded from v1) | v1 `ManagerAgent`; marketing report generation |

**Total: 28 agents.** Wave 1 has 11 agents: `audience_research`, `market_research`,
`positioning`, `marketing_strategist`, `copywriter`, `ad_copy`, `social_media`,
`email_marketing`, `art_director`, `critic` and `campaign_reporter`.

**Orchestration components are not catalog agents.** The planner, dispatcher,
dependency resolver, state manager and result aggregator are engine components
(Session 2). Only the optional LLM planner calls a model, and its output is
constrained and validated.

---

## 4. Mapping of the 37 requested agent names

| Requested | Becomes | | Requested | Becomes |
| --- | --- | --- | --- | --- |
| MarketResearchAgent | `market_research` | | KeywordResearchAgent | `keyword_research` |
| CompetitorResearchAgent | `competitor_research` | | SEOAuditAgent | `seo_optimizer` |
| AudienceResearchAgent | `audience_research` | | ContentOptimizationAgent | `seo_optimizer` |
| TrendResearchAgent | `market_research` | | SEOContentPlannerAgent | `seo_content_planner` |
| ProductResearchAgent | `positioning` | | ArtDirectorAgent | `art_director` |
| MarketingStrategistAgent | `marketing_strategist` | | BrandIdentityAgent | `brand_strategist` |
| BrandStrategistAgent | `brand_strategist` | | VisualPromptAgent | `art_director` |
| PositioningAgent | `positioning` | | CreativeReviewAgent | `critic` (creative rubric) |
| GrowthStrategyAgent | `growth_strategist` | | CampaignAnalyticsAgent | `performance_analyst` |
| FunnelStrategyAgent | `funnel_strategist` | | PerformanceReviewAgent | `performance_analyst` |
| CopywriterAgent | `copywriter` | | A/BTestingAgent | `experiment_designer` |
| BlogWriterAgent | `blog_writer` | | ConversionOptimizationAgent | `cro_advisor` |
| EmailMarketingAgent | `email_marketing` | | FactCheckingAgent | `fact_checker` |
| AdCopyAgent | `ad_copy` | | BrandConsistencyAgent | `brand_consistency` |
| ProductDescriptionAgent | `product_description` | | ContentQualityAgent | `critic` |
| LandingPageAgent | `landing_page` | | SafetyReviewAgent | `safety_reviewer` |
| SocialMediaAgent | `social_media` | | Critic / Reviewer (orchestration section) | `critic` |
| Planner Agent | engine planner (not a catalog agent) | | Growth Agent (architecture diagram) | `growth_strategist` |
| Analytics Agent (architecture diagram) | `performance_analyst` | | | |

## 5. Built-in workflow templates

| Template | Steps (each arrow is a dependency; `\|\|` means the steps run in parallel) | Session |
| --- | --- | --- |
| `classic-campaign` | v1 research -> copywriter -> art director -> manager (exact v1 parity; backs `CampaignWorkflow`) | S2 |
| `full-campaign` | (`audience_research` \|\| `market_research`) -> `positioning` -> `marketing_strategist` [optional approval] -> `copywriter` -> (`ad_copy` \|\| `social_media` \|\| `email_marketing` \|\| `art_director`), each reviewed by `critic` -> `campaign_reporter` | S3 |
| `ad-pack` | `positioning` -> `copywriter` -> `ad_copy` (reviewed) | S3 |
| `social-calendar` | `audience_research` -> `social_media` (reviewed) | S3 |
| `email-sequence` | `positioning` -> `email_marketing` (reviewed) | S3 |
| `competitor-report` | `competitor_research` -> `fact_checker` -> `campaign_reporter` (report mode) | S4 |
| `brand-kit` | `audience_research` -> `positioning` -> `brand_strategist` -> `brand_consistency` | S5 |
| `seo-content-pack` | `keyword_research` -> `seo_content_planner` -> `blog_writer` -> `seo_optimizer` | S8 |
| `performance-review` | `performance_analyst` -> `experiment_designer` -> `growth_strategist` | S8 |
| `ecommerce-campaign`, `b2b-campaign`, `product-launch`, `content-repurpose`, `landing-page` | Variants composed from the agents above | S8 |

The UI "studios" (Research, Strategy, Content, SEO, Creative, Social, Analytics) are
entry points that pre-select these templates (ADR-018).

## 6. Coverage of the 30 required capabilities

| # | Capability | Agent(s) | Wave |
| --- | --- | --- | --- |
| 1 | Market research | `market_research` | W1, evidence S4 |
| 2 | Competitor research | `competitor_research` | S4 |
| 3 | Customer and audience analysis | `audience_research` | W1 |
| 4 | Brand identity and guidelines | `brand_strategist` | S5 |
| 5 | Marketing strategy | `marketing_strategist` | W1 |
| 6 | Product positioning | `positioning` | W1 |
| 7 | SEO research and content optimisation | `keyword_research`, `seo_optimizer` | S8 |
| 8 | Keyword clustering | `keyword_research` | S8 |
| 9 | Blog and article generation | `blog_writer` | S8 |
| 10 | Landing page copy | `landing_page` | S8 |
| 11 | Advertising campaigns | `ad_copy` | W1 |
| 12 | Social media content | `social_media` | W1 |
| 13 | Content calendars | `social_media` | W1 |
| 14 | Email marketing | `email_marketing` | W1 |
| 15 | Product descriptions | `product_description` | S8 |
| 16 | E-commerce campaigns | `ecommerce-campaign` template | S8 |
| 17 | B2B campaigns | `full-campaign` (W1); `b2b-campaign` template | S8 |
| 18 | Lead magnet concepts | `funnel_strategist` | S8 |
| 19 | Funnel design | `funnel_strategist` | S8 |
| 20 | Growth strategies | `growth_strategist` | S8 |
| 21 | A/B testing ideas | `experiment_designer` | S8 |
| 22 | Creative direction | `art_director` | W1 |
| 23 | Image-prompt engineering | `art_director` | W1 |
| 24 | Campaign performance analysis | `performance_analyst` (needs user data) | S8 |
| 25 | Competitor comparison reports | `competitor-report` template | S4 |
| 26 | Brand consistency checking | `brand_consistency` | S5 |
| 27 | Content repurposing | `repurposer` | S8 |
| 28 | Marketing report generation | `campaign_reporter` | W1 |
| 29 | Custom user-defined workflows | engine + JSON definitions (S2); Workflow Builder UI (S6) | S2 / S6 |
| 30 | Multi-campaign batch execution | engine batch (v1 `run_batch` kept) | S2 |
