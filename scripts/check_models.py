"""Check which Gemini models the configured key can use (never prints the key).

  python scripts/check_models.py            # list available model ids
  python scripts/check_models.py --ping     # also send a 1-word request to each configured model
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from google import genai  # noqa: E402

from infra_assistant.config import Settings  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ping", action="store_true")
    args = parser.parse_args()
    settings = Settings.load()
    client = genai.Client(api_key=settings.google_api_key)
    available = sorted(m.name.removeprefix("models/") for m in client.models.list())
    print("available:", ", ".join(m for m in available if "gemini" in m or "gemma" in m or "embedding" in m))
    configured = {"heavy": settings.heavy_model, "fast": settings.fast_model, "judge": settings.judge_model,
                  "embedding": settings.embedding_model}
    for role, model in configured.items():
        print(f"{role:9} {model:28} {'OK' if model in available else 'NOT FOUND'}")
    if args.ping:
        for role in ("heavy", "fast", "judge"):
            try:
                reply = client.models.generate_content(model=configured[role], contents="Reply with one word: ok")
                print(f"ping {role}: {reply.text.strip()[:20]}")
            except Exception as exc:
                print(f"ping {role}: FAILED {type(exc).__name__}: {str(exc)[:150]}")


if __name__ == "__main__":
    main()
