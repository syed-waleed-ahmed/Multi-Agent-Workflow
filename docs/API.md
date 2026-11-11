# HTTP API

Campaign Forge's FastAPI service runs the same workflows as the CLI and the web app. Requests are authenticated with API keys and limited per key. Runs execute in the background: you start one, then poll it. The full schema is in [openapi.json](openapi.json). A running server also serves it at `/openapi.json` and as interactive docs at `/docs`.

## Run it

```bash
pip install -e ".[api]"
export CF_API_KEYS=dev-key-0123456789          # comma-separated; each key is its own tenant
export CF_PROVIDER=fake                        # optional: simulated, no provider key needed
python -m campaign_forge.api --port 8000       # or: uvicorn campaign_forge.api.app:create_app --factory
```

With Docker, using SQLite on a named volume:

```bash
CF_API_KEYS=dev-key-0123456789 docker compose up --build
```

The compose file binds to `127.0.0.1` only. Put a TLS-terminating proxy in front before exposing the service. To smoke-test any running server:

```bash
python scripts/api_smoke.py http://127.0.0.1:8000 dev-key-0123456789
```

## Endpoints

All paths except `/health` require the `X-API-Key` header.

| Method and path | What it does | Success |
| --- | --- | --- |
| `GET /health` | Liveness check. No key needed. | 200 |
| `GET /api/v1/agents` | Every registered agent: inputs, output kind, model class | 200 |
| `GET /api/v1/agents/{agent_id}` | One agent, including its rubric and limits | 200 |
| `POST /api/v1/campaigns` | Start a built-in workflow (default `full-campaign`) on a brief | 202 |
| `GET /api/v1/campaigns/{campaign_id}` | The run, plus its brief and report Markdown | 200 |
| `GET /api/v1/workflows` | The built-in templates, plus your own workflows | 200 |
| `POST /api/v1/workflows` | Store a custom workflow definition (validated) | 201 |
| `GET /api/v1/workflows/{workflow_id}` | A definition, as JSON | 200 |
| `POST /api/v1/workflows/{workflow_id}/run` | Start any workflow you can see | 202 |
| `GET /api/v1/executions` | Your runs, newest first | 200 |
| `GET /api/v1/executions/{execution_id}` | Status, steps, usage, outputs with claims and evidence | 200 |
| `POST /api/v1/executions/{execution_id}/resume` | Continue a paused, failed or interrupted run | 202 |
| `POST /api/v1/executions/{execution_id}/cancel` | Cancel a run | 200 |
| `POST /api/v1/executions/{execution_id}/approve` | Approve (or reject) a step that waits for a human | 202 |
| `GET /api/v1/providers` | Supported providers; keys appear only as configured or not | 200 |
| `GET /api/v1/system/status` | Version, mode, provider, limits, active runs | 200 |

A run request carries:
- the `brief` (required);
- an optional `brand` profile, whose banned terms are enforced;
- optional `sources`: your own documents, which research agents cite;
- optional `skip_steps` (optional steps only);
- optional `max_llm_calls`, `max_total_tokens` and `max_wall_seconds`. These can only *lower* the workflow's limits, never raise them.

An execution reports `"status": "starting"` until its first checkpoint is saved, and `"failed"` if it stopped before that.

## Authentication and tenancy

- **API keys.** Keys come from `CF_API_KEYS`, and comparisons run in constant time. With no keys configured, every `/api/v1` endpoint answers 401, so the API is closed by default.
- **Tenancy.** Each key is its own tenant scope: a key never sees another key's runs, custom workflows or memory. The scope id is derived from a hash of the key, so the key itself is never stored.

## Limits

| Limit | Setting | Default | When exceeded |
| --- | --- | --- | --- |
| Requests per minute, per key | `CF_API_RATE_LIMIT` | 120 | 429 with `Retry-After` |
| Run starts per minute, per key (start, resume, approve) | `CF_API_RUN_LIMIT` | 10 | 429 with `Retry-After` |
| Background runs executing at once, per process | `CF_API_MAX_ACTIVE_RUNS` | 4 | 503 with `Retry-After: 30` |
| Custom workflows per key | (fixed) | 50 | 503 |

Limits live in process memory. They reset on restart and apply per process; the API is designed for a single node (ADR-009).

## Errors

Every error has the same shape:

```json
{"error": {"type": "not_found", "message": "Run 'run-x' not found.", "details": []}}
```

| Status | `type` | Typical cause |
| --- | --- | --- |
| 400 | `bad_request` | A step that cannot be skipped, an input the workflow does not take, a built-in workflow id reused |
| 401 | `unauthorized` | Missing or invalid `X-API-Key`, or no keys configured |
| 403 | `provider_not_allowed` | `FREE_ONLY` refused the configured provider |
| 404 | `not_found` | Unknown agent, workflow or run, including one owned by another key |
| 409 | `conflict` | An illegal state change, such as cancelling a completed run |
| 422 | `invalid_request`, `invalid_workflow` | A malformed body, or a workflow definition that fails validation (`details` lists every problem) |
| 429 | `rate_limited` | A per-key limit was hit |
| 503 | `at_capacity` | Too many runs executing at once |
| 500 | `internal_error` | An unexpected error; no internals are returned |

Error messages are passed through the secret redactor before they leave the server.

## Notes

- **Custom workflows** live in process memory, so post them again after a restart. Runs and campaign history persist in the configured run store and memory database.
- **Background runs.** A run executes in a thread inside the API process. If the process restarts mid-run, the run keeps its last checkpoint and can be resumed with `POST /executions/{id}/resume`.
- **Example client:** [`examples/api_client.py`](../examples/api_client.py).
