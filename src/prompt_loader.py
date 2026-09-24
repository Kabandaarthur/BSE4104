"""Loads a versioned prompt specification from prompts/.

Each spec (prompts/v1.0.md, prompts/v1.1.md, ...) mixes design notes for the
team with the actual system prompt. Only the text between the SYSTEM_PROMPT
markers is returned, so the notes never reach the model. The version is
chosen with PROMPT_VERSION in .env (default v3.0), so switching prompts is an
environment change rather than a code change.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
DEFAULT_VERSION = os.getenv("PROMPT_VERSION", "v3.0")

START_MARKER = "<!-- SYSTEM_PROMPT:START -->"
END_MARKER = "<!-- SYSTEM_PROMPT:END -->"


class PromptSpecError(RuntimeError):
    pass


def available_versions():
    return sorted(path.stem for path in PROMPTS_DIR.glob("v*.md"))


def load_prompt_spec(version=None):
    name = version or DEFAULT_VERSION
    path = PROMPTS_DIR / f"{name}.md"
    if not path.is_file():
        raise PromptSpecError(
            f"Prompt spec '{name}' not found in {PROMPTS_DIR}. "
            f"Available: {available_versions()}"
        )

    text = path.read_text(encoding="utf-8")
    if text.count(START_MARKER) != 1 or text.count(END_MARKER) != 1:
        raise PromptSpecError(
            f"{path.name} must contain exactly one {START_MARKER} "
            f"and one {END_MARKER}."
        )

    start = text.index(START_MARKER) + len(START_MARKER)
    end = text.index(END_MARKER)
    prompt = text[start:end].strip()
    if end < start or not prompt:
        raise PromptSpecError(f"{path.name} has an empty or misordered system prompt block.")
    return prompt
