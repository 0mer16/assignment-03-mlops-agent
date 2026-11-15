"""Run the demonstration scenarios and write them up as evidence.

Each conversation is a real agent invocation against whatever services are
running, so the transcript records what the tools actually returned, including
anything that was unreachable at the time.

    python scripts/capture_conversations.py --output docs/evidence/conversations.md
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from agent.orchestrator import MLOpsAgent  # noqa: E402
from agent.schemas import AgentAnswer  # noqa: E402

SCENARIOS: list[tuple[str, str, bool]] = [
    (
        "Scenario 1 - Dataset provenance",
        "Which dataset version is in use, and how do I reproduce it?",
        False,
    ),
    (
        "Scenario 2 - Model selection",
        "Compare the last five training runs and recommend the best candidate based on F1 score",
        False,
    ),
    (
        "Scenario 3 - Pipeline execution",
        "What is the status of the training pipeline and did the last run succeed?",
        False,
    ),
    (
        "Scenario 4 - Deployment diagnosis",
        "Why is my prediction API unavailable?",
        False,
    ),
    (
        "Scenario 5 - Multi-tool reasoning",
        "Find the best recent model, identify the dataset version used to train it, "
        "check the deployment status, and explain whether retraining is necessary",
        False,
    ),
]


def render(title: str, question: str, confirm: bool, answer: AgentAnswer) -> str:
    lines = [
        f"## {title}",
        "",
        f"**Question:** {question}",
        "",
        f"Planner: `{answer.planner}` &middot; request id: `{answer.request_id}`"
        + (" &middot; confirmation given" if confirm else ""),
        "",
        "**Answer**",
        "",
        "```",
        answer.answer.strip(),
        "```",
        "",
    ]

    for heading, items in (
        ("Facts the tools returned", answer.facts),
        ("Uncertainties", answer.uncertainties),
        ("Recommended actions", answer.recommended_actions),
    ):
        if items:
            lines.append(f"**{heading}**")
            lines.append("")
            lines.extend(f"- {item}" for item in items)
            lines.append("")

    if answer.evidence:
        lines.append("**Evidence**")
        lines.append("")
        lines.append("| tool | status | source | ms |")
        lines.append("|---|---|---|---|")
        for item in answer.evidence:
            source = (item.source or "n/a").replace("|", "\\|")
            lines.append(f"| `{item.tool}` | {item.status.value} | `{source}` | {item.duration_ms:.0f} |")
        lines.append("")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Capture the demonstration conversations.")
    parser.add_argument("--output", type=Path, default=REPO_ROOT / "docs" / "evidence" / "conversations.md")
    args = parser.parse_args(argv)

    agent = MLOpsAgent()
    stamp = dt.datetime.now(dt.UTC).strftime("%Y-%m-%d %H:%M UTC")

    sections = [
        "# Tested agent conversations",
        "",
        f"Captured {stamp} with the `{agent.planner.name}` planner against the running stack.",
        "Every fact below came from a tool call recorded in the evidence table under it.",
        "",
    ]

    for title, question, confirm in SCENARIOS:
        print(f"running: {title}")
        answer = agent.ask(question, confirm=confirm)
        sections.append(render(title, question, confirm, answer))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(sections), encoding="utf-8")
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
