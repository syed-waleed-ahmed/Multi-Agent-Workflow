from typing import Dict

from ..llm_client import client
from ..config import GENERATOR_MODEL, TEMPERATURE, MAX_TOKENS


def run_research(brief: Dict) -> str:
    """
    Research Agent:
    - Analyzes the product, audience, goal, tone and channels.
    - Returns a clear summary of audience insights, pain points,
      trends and positioning ideas.
    """
    system_prompt = (
        "You are a Marketing Research Agent. "
        "Given a campaign brief, you analyze target audience, pain points, "
        "current trends and competitor angles. "
        "Respond with a structured summary using headings and bullet points."
    )

    user_prompt = f"""
Campaign brief:
- Product name: {brief['product_name']}
- Product description: {brief['product_description']}
- Target audience: {brief['target_audience']}
- Goal: {brief['goal']}
- Tone: {brief['tone']}
- Channels: {', '.join(brief['channels'])}

Task:
1. Describe the target audience (demographics + interests).
2. List their main pain points and desires.
3. Mention a few relevant market or content trends.
4. Suggest 3–5 positioning angles we could use in the campaign.
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
