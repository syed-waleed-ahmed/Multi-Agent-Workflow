from typing import Dict, Any
import json

from ..llm_client import client
from ..config import GENERATOR_MODEL, TEMPERATURE, MAX_TOKENS


def generate_copy(brief: Dict, research_summary: str) -> Dict[str, Any]:
    """
    Copywriter Agent:
    - Uses the research to write ad copy.
    - Returns a dictionary with tagline, key message, headlines, body copy, CTA.
    """
    system_prompt = (
        "You are a creative copywriter for digital marketing campaigns. "
        "You write concise, persuasive copy grounded in the research provided. "
        "Respond as a JSON object only."
    )

    user_prompt = f"""
Campaign brief:
- Product name: {brief['product_name']}
- Product description: {brief['product_description']}
- Target audience: {brief['target_audience']}
- Goal: {brief['goal']}
- Tone: {brief['tone']}
- Channels: {', '.join(brief['channels'])}

Research summary:
{research_summary}

Task:
Create campaign copy and return it as JSON with the following keys:
- "tagline": one short catchy line.
- "primary_message": 2–3 sentences summarizing the campaign message.
- "headlines": an array of 3–5 short ad headlines.
- "body_copy": a paragraph suitable for a landing page or social caption.
- "call_to_action": a strong CTA phrase.

Return ONLY valid JSON, no extra text.
"""

    resp = client.chat.completions.create(
        model=GENERATOR_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=TEMPERATURE,
        max_tokens=MAX_TOKENS,
        response_format={"type": "json_object"},
    )

    content = resp.choices[0].message.content
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        # Fallback: wrap raw text
        data = {"raw_text": content}

    return data
