from typing import Dict, Any, List

from ..llm_client import client
from ..config import GENERATOR_MODEL, TEMPERATURE, MAX_TOKENS


def generate_image_prompts(
    brief: Dict,
    research_summary: str,
    copy_data: Dict[str, Any],
) -> List[str]:
    """
    Art Director Agent:
    - Proposes visual directions and prompts for an image model (DALL·E, SD, etc.).
    - Returns a list of prompt strings.
    """
    tagline = copy_data.get("tagline") or ""
    primary_message = copy_data.get("primary_message") or ""

    system_prompt = (
        "You are an Art Director creating image prompts for AI image generators "
        "like DALL·E or Stable Diffusion. "
        "You translate marketing ideas into clear visual prompts."
    )

    user_prompt = f"""
Campaign brief:
- Product name: {brief['product_name']}
- Product description: {brief['product_description']}
- Target audience: {brief['target_audience']}
- Goal: {brief['goal']}
- Tone: {brief['tone']}
- Channels: {', '.join(brief['channels'])}

Key messaging:
- Tagline: {tagline}
- Primary message: {primary_message}

Research summary:
{research_summary}

Task:
1. Propose 3–5 distinct visual concepts for the campaign.
2. For each concept, write a single-line prompt that could be passed to an image model
   (include subject, style, mood, colors, and any important details).
3. Output them as a numbered list, each line being a standalone prompt.
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

    text = resp.choices[0].message.content.strip()

    # Simple parsing: split into lines that contain prompts
    lines = [line.strip("- ").strip() for line in text.splitlines() if line.strip()]
    # Filter out pure headings
    prompts = [ln for ln in lines if len(ln.split()) > 3]

    return prompts
