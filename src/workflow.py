from dataclasses import dataclass
from typing import List, Dict, Any

from .agents.research_agent import run_research
from .agents.copywriter_agent import generate_copy
from .agents.art_director_agent import generate_image_prompts
from .agents.manager_agent import assemble_brief


@dataclass
class CampaignBrief:
    product_name: str
    product_description: str
    target_audience: str
    goal: str
    tone: str
    channels: List[str]


@dataclass
class CampaignResult:
    brief: CampaignBrief
    research_summary: str
    copy_data: Dict[str, Any]
    image_prompts: List[str]
    final_brief: str


def run_marketing_campaign(brief: CampaignBrief) -> CampaignResult:
    brief_dict: Dict[str, Any] = {
        "product_name": brief.product_name,
        "product_description": brief.product_description,
        "target_audience": brief.target_audience,
        "goal": brief.goal,
        "tone": brief.tone,
        "channels": brief.channels,
    }

    # 1. Research
    research_summary = run_research(brief_dict)

    # 2. Copywriting
    copy_data = generate_copy(brief_dict, research_summary)

    # 3. Art direction
    image_prompts = generate_image_prompts(brief_dict, research_summary, copy_data)

    # 4. Manager assembles final brief
    final_brief = assemble_brief(brief_dict, research_summary, copy_data, image_prompts)

    return CampaignResult(
        brief=brief,
        research_summary=research_summary,
        copy_data=copy_data,
        image_prompts=image_prompts,
        final_brief=final_brief,
    )
