"""Command-line entry point for the MLOps agent."""

from __future__ import annotations

import argparse
import json
import sys

from agent.config import get_settings
from agent.orchestrator import MLOpsAgent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mlops-agent",
        description="Ask questions about datasets, experiments, pipelines and the deployment.",
    )
    parser.add_argument("question", nargs="*", help="The question to ask. Omit for an interactive session.")
    parser.add_argument("--json", action="store_true", help="Print the full answer as JSON.")
    parser.add_argument("--tools", action="store_true", help="List the available tools and exit.")
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Authorise state-changing operations such as triggering a DAG for this request.",
    )
    parser.add_argument(
        "--planner",
        choices=["auto", "rules", "gemini"],
        help="Override the planner. 'rules' needs no API key.",
    )
    return parser


def _emit(answer, as_json: bool) -> None:
    if as_json:
        print(json.dumps(answer.model_dump(mode="json"), indent=2))
    else:
        print(answer.render())


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    settings = get_settings()
    if args.planner:
        settings.planner = args.planner

    agent = MLOpsAgent(settings=settings)

    if args.tools:
        print(agent.transcript())
        return 0

    if args.question:
        _emit(agent.ask(" ".join(args.question), confirm=args.confirm), args.json)
        return 0

    print(f"MLOps agent ready (planner: {agent.planner.name}). Type a question, or 'exit' to quit.")
    print("Prefix a question with 'confirm:' to authorise a state-changing operation.\n")
    while True:
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not line:
            continue
        if line.lower() in {"exit", "quit"}:
            return 0
        if line.lower() in {"tools", "/tools"}:
            print(agent.transcript())
            continue

        confirm = args.confirm
        if line.lower().startswith("confirm:"):
            confirm = True
            line = line.split(":", 1)[1].strip()

        _emit(agent.ask(line, confirm=confirm), args.json)
        print()


if __name__ == "__main__":
    sys.exit(main())
