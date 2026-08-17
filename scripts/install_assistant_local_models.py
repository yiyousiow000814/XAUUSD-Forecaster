#!/usr/bin/env python
"""Create the hardware-validated Ollama aliases used only by Assistant chat."""

from __future__ import annotations

from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
MODELS = (
    ("assistant-qwen35-4b-256k", "assistant-qwen35-4b-256k.Modelfile"),
)


def main() -> int:
    for name, filename in MODELS:
        subprocess.run(
            ["ollama", "create", name, "-f", str(ROOT / "config" / "ollama" / filename)],
            check=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
