import argparse

from src.workflow import CampaignBrief, run_marketing_campaign


def parse_args():
    parser = argparse.ArgumentParser(
        description="Multi-Agent Marketing Campaign Creator"
    )
    parser.add_argument("--product-name", type=str, help="Name of the product")
    parser.add_argument("--description", type=str, help="Short product description")
    parser.add_argument("--audience", type=str, help="Target audience")
    parser.add_argument("--goal", type=str, help="Campaign goal (e.g. signups, sales)")
    parser.add_argument("--tone", type=str, help="Tone of voice (e.g. fun, professional)")
    parser.add_argument(
        "--channels",
        type=str,
        help="Comma-separated list of channels (e.g. instagram, tiktok, email)",
    )
    return parser.parse_args()


def prompt_if_missing(current: str | None, label: str) -> str:
    if current:
        return current
    return input(f"{label}: ").strip()


def main():
    args = parse_args()

    product_name = prompt_if_missing(args.product_name, "Product name")
    description = prompt_if_missing(args.description, "Product description")
    audience = prompt_if_missing(args.audience, "Target audience")
    goal = prompt_if_missing(args.goal, "Campaign goal")
    tone = prompt_if_missing(args.tone, "Desired tone (e.g. friendly, premium)")

    if args.channels:
        channels = [c.strip() for c in args.channels.split(",") if c.strip()]
    else:
        raw = input("Channels (comma-separated, e.g. instagram, tiktok, email): ").strip()
        channels = [c.strip() for c in raw.split(",") if c.strip()]

    brief = CampaignBrief(
        product_name=product_name,
        product_description=description,
        target_audience=audience,
        goal=goal,
        tone=tone,
        channels=channels,
    )

    print("\n=== Running Multi-Agent Workflow... ===\n")
    result = run_marketing_campaign(brief)

    print("=== RESEARCH AGENT OUTPUT ===")
    print(result.research_summary)
    print("\n=== COPYWRITER AGENT OUTPUT ===")
    print(result.copy_data)
    print("\n=== ART DIRECTOR IMAGE PROMPTS ===")
    for i, p in enumerate(result.image_prompts, start=1):
        print(f"{i}. {p}")
    print("\n=== FINAL CAMPAIGN BRIEF (MANAGER AGENT) ===")
    print(result.final_brief)


if __name__ == "__main__":
    main()
