"""The orchestrator that wires the four agents into an end-to-end pipeline.

:class:`CampaignWorkflow` runs the agents sequentially for a single brief and,
crucially for larger workloads, exposes :meth:`run_batch` which fans many briefs
out across a thread pool with **per-item error isolation** - one failing
campaign never aborts the rest of the batch.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from .agents import ArtDirectorAgent, CopywriterAgent, ManagerAgent, ResearchAgent
from .config import Settings, get_settings
from .llm import LLMClient
from .logging_config import get_logger
from .models import CampaignBrief, CampaignResult


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
        llm: LLMClient | None = None,
    ) -> None:
        """Create a workflow.

        Args:
            settings: Application settings; defaults to :func:`get_settings`.
            llm: An LLM client to use; defaults to one built from ``settings``.
                Injecting one is how tests supply a fake client.
        """
        self._settings = settings or get_settings()
        self._llm = llm or LLMClient(self._settings)
        self._log = get_logger("workflow")

        self._research = ResearchAgent(self._llm)
        self._copywriter = CopywriterAgent(self._llm)
        self._art_director = ArtDirectorAgent(self._llm)
        self._manager = ManagerAgent(self._llm)

    def run(self, brief: CampaignBrief) -> CampaignResult:
        """Generate a complete campaign for a single brief.

        Raises:
            CampaignForgeError: (or a subclass) if any stage fails.
        """
        start = time.perf_counter()
        self._log.info("Generating campaign for %r", brief.product_name)

        research_summary = self._research.run(brief)
        copy = self._copywriter.run(brief, research_summary)
        image_prompts = self._art_director.run(brief, research_summary, copy)
        final_brief = self._manager.run(brief, research_summary, copy, image_prompts)

        duration = time.perf_counter() - start
        self._log.info("Finished %r in %.1fs", brief.product_name, duration)
        return CampaignResult(
            brief=brief,
            research_summary=research_summary,
            marketing_copy=copy,
            image_prompts=image_prompts,
            final_brief=final_brief,
            model=self._llm.model,
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
            max_workers: Thread-pool size; defaults to ``settings.max_workers``.
            on_result: Optional callback invoked as each item completes (useful
                for progress reporting). Called from worker threads.

        Returns:
            Results in the original input order.
        """
        brief_list = list(briefs)
        if not brief_list:
            return []
        workers = max_workers or self._settings.max_workers
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
                    on_result(item)

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
