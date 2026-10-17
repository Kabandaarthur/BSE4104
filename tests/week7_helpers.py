"""Shared test isolation helpers for Week 7 pipeline tests."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def use_real_call_model(monkeypatch):
    """Undo the Week 2 evaluation module's fake /chat model for this test."""
    import main as main_module

    def real_call_model(system_prompt, user_message):
        return main_module.run_session_turn(
            user_message, system_prompt=system_prompt
        )

    monkeypatch.setattr(main_module, "call_model", real_call_model)
