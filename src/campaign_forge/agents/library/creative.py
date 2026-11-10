"""Creative agents (wave 1): art direction (visual concepts and image prompts)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import ClassVar

from ...core.agent import InputSpec
from ...domain.artifacts import Artifact
from ..schemas import CORE_COPY_KIND, POSITIONING_KIND, VISUALS_KIND, VisualConcepts
from .common import BRIEF, AgentCategory, StructuredAgent, context, framed_brief, make_spec


class ArtDirectionAgent(StructuredAgent):
    spec = make_spec(
        "art_director",
        "Art director",
        "Three to five visual concepts, each with a specific single-line image prompt.",
        category=AgentCategory.CREATIVE,
        inputs=(
            BRIEF,
            InputSpec(name="copy", kind=CORE_COPY_KIND),
            InputSpec(name="positioning", kind=POSITIONING_KIND, required=False),
        ),
        output_kind=VISUALS_KIND,
        capabilities=("creative_direction", "image_prompts"),
        rubric=("on_brief", "specificity", "variety"),
        max_tokens=900,
        reviewable=True,
    )
    output_model: ClassVar[type[VisualConcepts]] = VisualConcepts
    role = (
        "You are an art director. You turn messaging into distinct visual concepts and "
        "write precise prompts for image-generation models."
    )
    shape = (
        '{"concepts": [{"name": "...", "description": "...", "image_prompt": "subject, setting, '
        'style, lighting, colours, composition", "mood": "...", "palette": ["..."], '
        '"aspect_ratio": "4:5"}]}'
    )

    def task(self, inputs: Mapping[str, Artifact]) -> str:
        return (
            f"Campaign brief:\n{framed_brief(inputs)}\n\n"
            f"{context(inputs, 'copy', 'positioning')}\n\n"
            "Task: propose 3-4 distinct visual concepts. Each image prompt must be a single "
            "line of at least 8 words covering subject, setting, style, lighting, colours and "
            "composition. Choose an aspect ratio suited to the channels (1:1, 4:5, 9:16 or 16:9)."
        )
