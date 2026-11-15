"""Interactive CLI:  python -m infra_assistant.cli   (type 'exit' to quit)."""

from __future__ import annotations

import asyncio
import uuid

from infra_assistant.graph import build_app
from infra_assistant.state import turn_input


async def _loop() -> None:
    app = build_app()
    config = {"configurable": {"thread_id": uuid.uuid4().hex}}
    while True:
        try:
            message = input("\nyou> ").strip()
        except EOFError:
            break
        if message.lower() in {"exit", "quit"}:
            break
        if not message:
            continue
        result = await app.ainvoke(turn_input(message), config=config)
        print("path:", " -> ".join(s["node"] for s in result.get("trace", [])))
        print(f"\ninfrapilot> {result.get('final_response', '')}")


def main() -> None:
    asyncio.run(_loop())


if __name__ == "__main__":
    main()
