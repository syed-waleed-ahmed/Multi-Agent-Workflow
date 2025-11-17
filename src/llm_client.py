from openai import OpenAI
from .config import OPENAI_API_KEY

# Use Groq's OpenAI-compatible API
client = OpenAI(
    api_key=OPENAI_API_KEY,
    base_url="https://api.groq.com/openai/v1",
)
