import os
from dotenv import load_dotenv

# Load variables from .env
load_dotenv()

# Try GROQ_API_KEY first, then OPENAI_API_KEY if you ever want to switch back
OPENAI_API_KEY = os.getenv("GROQ_API_KEY") or os.getenv("OPENAI_API_KEY")

if not OPENAI_API_KEY:
    raise ValueError(
        "No API key found. Set GROQ_API_KEY or OPENAI_API_KEY in your .env file."
    )

# Model we use for all agents (Groq OpenAI-compatible)
GENERATOR_MODEL = "llama-3.1-8b-instant"

# Generation settings
TEMPERATURE = 0.5
MAX_TOKENS = 800
