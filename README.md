# 🚀 Multi-Agent Marketing Campaign Creator  
_Automated workflow using AI agents: Researcher → Copywriter → Art Director → Manager_

This project is a **multi-agent AI workflow automator** that generates a **complete marketing campaign brief** from a simple product description.  
Using Groq’s OpenAI-compatible LLM API, each specialized agent performs a different role—just like a real marketing team:

- 🧠 **Research Agent** – analyzes trends, audience, insights  
- ✍️ **Copywriter Agent** – writes taglines, messages, headlines, body copy  
- 🎨 **Art Director Agent** – creates visual concepts & image prompts  
- 📋 **Manager Agent** – assembles everything into a structured final brief  

This project demonstrates agent orchestration, workflow automation, modular design, and real-world LLM application development.

---

## 🧩 Features

✔ Automated end-to-end AI workflow  
✔ 4 specialized AI agents collaborating sequentially  
✔ Generates:
- Research summary  
- Positioning angles  
- Tagline + campaign messaging  
- Headlines + ad copy  
- Image prompts (for DALL·E, Stable Diffusion, etc.)  
- Final campaign brief (Markdown)

✔ Uses **Groq LLMs** (fast & free-tier friendly)  
✔ Uses **OpenAI-compatible API calls**  
✔ Modular, readable Python architecture  
✔ CLI interface for interactive use  

---

## 📂 Project Structure

```text
multi_agent_workflow/
│
├── main.py                     # CLI entry point
├── requirements.txt
├── .env                        # Contains GROQ_API_KEY (not pushed to GitHub)
│
├── src/
│   ├── config.py               # Settings, model names, API keys
│   ├── llm_client.py           # Groq API client
│   ├── workflow.py             # Orchestrates all 4 agents
│   │
│   └── agents/
│       ├── research_agent.py       # Audience insights & research
│       ├── copywriter_agent.py     # Tagline, headlines, messaging, copy
│       ├── art_director_agent.py   # Image prompts & visual concepts
│       └── manager_agent.py        # Final campaign brief assembly
│
└── README.md
```
## ▶️ Usage

Run the program:
python main.py

It will ask for:

- Product name
- Product description
- Target audience
- Campaign goal
- Tone
- Channels (e.g., instagram, tiktok, email)

## Example

- Product name: EcoSip Reusable Bottle
- Product description: Stylish insulated bottle that keeps drinks cold 24 hours
- Target audience: young professionals who care about sustainability
- Campaign goal: drive online sales for summer collection
- Tone: fresh, energetic, eco-friendly
- Channels: instagram, tiktok, email

## Output Includes

✔ Research insights
✔ Copywriting content (tagline, headlines, body copy, CTA)
✔ Image prompts (3–5 visual concepts)
✔ A polished final campaign brief

## 🧠 How the Workflow Works

- Research Agent: Generates insights, pain points, trends, and positioning angles.
- Copywriter Agent: Creates messaging including tagline, headlines, and body copy.
- Art Director Agent:Produces 3–5 detailed prompts for AI image generators.
- Manager Agent: Combines all previous outputs into a final campaign brief in Markdown.

## 🎯 Why This Project Matters

This project demonstrates:
- Multi-agent orchestration
- Practical workflow automation
- Prompt engineering for specialized agent roles
- Modular Python project structure
- LLM-powered content generation
- Realistic simulation of a professional marketing workflow
- Great for showcasing AI engineering, automation, creative pipelines, and multi-agent systems.

## 🛠️ Future Enhancements

- Add JSON schema validation for agent outputs
- Optional PDF export of final brief
- Streamlit or Gradio web UI
- Plugin for automatic image generation (DALL·E, SDXL, Flux)
- Save outputs to /outputs/ directory

## Author

Created by Syed Waleed Ahmed
