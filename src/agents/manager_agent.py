from typing import Dict, Any, List

from ..llm_client import client
from ..config import GENERATOR_MODEL, TEMPERATURE, MAX_TOKENS


def assemble_brief(
    brief: Dict,
    research_summary: str,
    copy_data: Dict[str, Any],
    image_prompts: List[str],
) -> str:
    """
    Manager Agent:
    - Assembles everything into one structured campaign brief.
    - Output is markdown text: sections for research, messaging, visuals, and sample assets.
    """
    system_prompt = (
        "You are a Campaign Manager Agent. "
        "You synthesize research, copy, and visual directions into a clear campaign brief. "
        "Your output should be easy for marketers and designers to follow."
    )

    image_prompt_block = "\n".join(f"- {p}" for p in image_prompts) if image_prompts else "None"

    user_prompt = f"""
Original campaign brief:
- Product name: {brief['product_name']}
- Product description: {brief['product_description']}
- Target audience: {brief['target_audience']}
- Goal: {brief['goal']}
- Tone: {brief['tone']}
- Channels: {', '.join(brief['channels'])}

Research summary:
{research_summary}

Copy data (JSON-like):
{copy_data}

Image prompts:
{image_prompt_block}

Task:
Create a final campaign brief in markdown with these sections:
1. Overview
2. Target Audience & Insights
3. Positioning & Key Messages (include tagline and primary message)
4. Example Headlines & Copy
5. Visual Direction (summarize the image prompts)
6. Deliverables (what assets to produce, e.g. 3 social ads, landing page hero, etc.)

Be concise but specific. Write it as if handing it to a marketing + design team.
"""

    resp = client.chat.completions.create(
        model=GENERATOR_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=TEMPERATURE,
        max_tokens=MAX_TOKENS,
    )

    return resp.choices[0].message.content.strip()
