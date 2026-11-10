"""The v1 ``CampaignWorkflow`` API, now a facade over the 2.0 workflow engine.

:class:`CampaignWorkflow` runs the ``classic-campaign`` template (research, then
copy, then art direction, then the final brief) for one brief, and returns the v1
:class:`~campaign_forge.models.CampaignResult`.

:meth:`run_batch` fans many briefs across a thread pool with **per-item error
isolation**: one failing campaign never aborts the rest of the batch.

Running on the engine means every v1 call is now:

* budgeted;
* routed under ``FREE_ONLY`` policy (ADR-020);
* recorded with token usage.

The signature, the results and the error types are unchanged (ADR-001).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from .agents.legacy import PromptListPayload, TextPayload
from .config import MAX_WORKERS, Settings, get_settings
from .core.registry import AgentRegistry
from .exceptions import CampaignForgeError, ConfigurationError
from .logging_config import get_logger
from .models import CampaignBrief, CampaignResult, CopyContent
from .orchestration.engine import RunResult, WorkflowEngine
from .orchestration.store import InMemoryRunStore
from .orchestration.templates import classic_campaign, default_registry
from .providers.compat import ChatClient, LegacyClientProvider
from .providers.router import ProviderRouter


@dataclass(slots=True)
class BatchItemResult:
    """Outcome of a single brief within a batch run.

    Exactly one of :attr:`result` / :attr:`error` is populated.
    """

    index: int
    brief: CampaignBrief
    result: CampaignResult | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.result is not None


class CampaignWorkflow:
    """Runs the Research -> Copywriter -> Art Director -> Manager pipeline."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        llm: ChatClient | None = None,
        registry: AgentRegistry | None = None,
    ) -> None:
        """Create a workflow.

        Args:
            settings: Application settings; defaults to :func:`get_settings`.
            llm: A v1-style chat client to use instead of the configured provider.
                Injecting one is how tests supply a fake client.
            registry: Agent registry; defaults to the built-in agents.

        Raises:
            ConfigurationError: if no API key is configured, or ``FREE_ONLY``
                policy refuses the configured endpoint (checked eagerly, as in v1).
        """
        self._settings = settings or get_settings()
        self._log = get_logger("workflow")
        router = ProviderRouter(self._settings)
        if llm is not None:
            name = router.global_provider
            router.add_provider(name, LegacyClientProvider(llm, name=name))
            router.resolve()  # policy check for the injected client's route
        else:
            router.provider(router.global_provider)  # fail fast: missing key / FREE_ONLY
        self._engine = WorkflowEngine(
            registry or default_registry(), router, store=InMemoryRunStore()
        )

    @property
    def engine(self) -> WorkflowEngine:
        """The underlying 2.0 engine."""
        return self._engine

    def run(self, brief: CampaignBrief) -> CampaignResult:
        """Generate a complete campaign for a single brief.

        Raises:
            CampaignForgeError: (or a subclass) if any stage fails, with the original
                exception type (e.g. ``LLMError``, ``OutputParsingError``).
        """
        start = time.perf_counter()
        self._log.info("Generating campaign for %r", brief.product_name)
        result = self._engine.start(classic_campaign(), {"brief": brief})
        try:
            if not result.ok:
                raise self._failure(result)
            campaign = self._to_campaign_result(brief, result, time.perf_counter() - start)
        finally:
            # The v1 API returns results directly; keep no run history in memory.
            self._engine.store.delete(result.record.run_id, result.record.scope)
        self._log.info("Finished %r in %.1fs", brief.product_name, campaign.duration_seconds)
        return campaign

    @staticmethod
    def _failure(result: RunResult) -> BaseException:
        error = result.first_error()
        if error is not None:
            return error
        reason = result.record.status_reason or result.record.status.value
        return CampaignForgeError(f"Campaign run {result.record.status.value}: {reason}")

    def _to_campaign_result(
        self, brief: CampaignBrief, result: RunResult, duration: float
    ) -> CampaignResult:
        final = result.outputs()["final_brief"]
        return CampaignResult(
            brief=brief,
            research_summary=result.output("research", TextPayload).text,
            marketing_copy=result.output("copy", CopyContent),
            image_prompts=result.output("image_prompts", PromptListPayload).prompts,
            final_brief=final.as_model(TextPayload).text,
            model=final.produced_by.model or self._settings.model,
            duration_seconds=duration,
        )

    def run_batch(
        self,
        briefs: Iterable[CampaignBrief],
        *,
        max_workers: int | None = None,
        on_result: Callable[[BatchItemResult], None] | None = None,
    ) -> list[BatchItemResult]:
        """Generate campaigns for many briefs concurrently.

        Each brief is processed in isolation: failures are captured on the
        returned :class:`BatchItemResult` rather than raised, so a single bad
        input cannot bring down the whole batch.

        Args:
            briefs: The briefs to process.
            max_workers: Thread-pool size (1-64); defaults to ``settings.max_workers``.
            on_result: Optional callback invoked as each item completes (useful
                for progress reporting). It runs on the *calling* thread, one item
                at a time. If it raises, the error is logged and the batch continues.

        Returns:
            Results in the original input order.

        Raises:
            ConfigurationError: if ``max_workers`` is outside 1-64.
        """
        brief_list = list(briefs)
        workers = max_workers if max_workers is not None else self._settings.max_workers
        if not 1 <= workers <= MAX_WORKERS:
            raise ConfigurationError(
                f"max_workers must be between 1 and {MAX_WORKERS}; got {workers}."
            )
        if not brief_list:
            return []
        self._log.info("Processing %d campaigns with up to %d workers", len(brief_list), workers)

        results: list[BatchItemResult] = []
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [
                executor.submit(self._run_isolated, index, brief)
                for index, brief in enumerate(brief_list)
            ]
            for future in as_completed(futures):
                item = future.result()  # _run_isolated never raises
                results.append(item)
                if on_result is not None:
                    try:
                        on_result(item)
                    except Exception:  # a broken progress callback must not lose results
                        self._log.exception(
                            "on_result callback failed for item %d; continuing.", item.index
                        )

        results.sort(key=lambda item: item.index)
        succeeded = sum(1 for item in results if item.ok)
        self._log.info(
            "Batch complete: %d succeeded, %d failed", succeeded, len(results) - succeeded
        )
        return results

    def _run_isolated(self, index: int, brief: CampaignBrief) -> BatchItemResult:
        """Run a single brief, converting any exception into a captured error."""
        try:
            result = self.run(brief)
            return BatchItemResult(index=index, brief=brief, result=result)
        except Exception as exc:  # noqa: BLE001 - deliberate: isolate batch failures
            self._log.error(
                "Campaign %d (%r) failed: %s", index, brief.product_name, exc, exc_info=True
            )
            return BatchItemResult(index=index, brief=brief, error=str(exc))
