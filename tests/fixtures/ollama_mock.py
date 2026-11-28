# tests/fixtures/ollama_mock.py
"""
Configurable offline mock controller for local Ollama interactions in tests.

Supports:
- Returning structured JSON strings or dicts
- Queuing sequential responses (e.g., malformed JSON once then valid JSON)
- Simulating request timeouts (`TimeoutError`)
- Simulating offline/unavailable Ollama (`ConnectionError`)
- Recording prompts to verify zero PII leakage
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

_PHONE_PATTERN = re.compile(r"\b01[0-9][- ]?\d{4}[- ]?\d{4}\b")
_EMAIL_PATTERN = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_HASH_PATTERN = re.compile(r"!DISABLED_NON_HASH!", re.IGNORECASE)


@dataclass
class MockOllamaController:
    """Test double simulating local `qwen3.5:4b` responses and failure modes."""

    model: str = "qwen3.5:4b"
    mode: str = "ok"  # "ok" | "timeout" | "offline"
    default_payload: dict[str, Any] = field(
        default_factory=lambda: {
            "groups": [
                {
                    "canonical": "عرض 6 قطع",
                    "members": ["عرض ال ٦ قطع", "عرض ست قطع", "عرض 6 قطع"],
                    "confidence": 0.95,
                }
            ]
        }
    )
    queued_responses: list[str | dict[str, Any]] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)

    def set_response(self, payload: dict[str, Any] | str) -> None:
        """Configure a deterministic response for subsequent calls."""
        self.mode = "ok"
        if isinstance(payload, str):
            self.queued_responses = [payload]
        else:
            self.default_payload = payload
            self.queued_responses.clear()

    def queue_responses(self, *payloads: dict[str, Any] | str) -> None:
        """Queue sequential responses (first in, first out)."""
        self.mode = "ok"
        self.queued_responses = list(payloads)

    def set_timeout(self, enabled: bool = True) -> None:
        """Simulate Ollama read/connect timeout."""
        self.mode = "timeout" if enabled else "ok"

    def set_offline(self, enabled: bool = True) -> None:
        """Simulate Ollama daemon unavailable / unreachable."""
        self.mode = "offline" if enabled else "ok"

    def is_available(self) -> bool:
        """Return False when simulating offline mode."""
        return self.mode != "offline"

    def generate(self, prompt: str, *, model: str | None = None) -> str:
        """Simulate an Ollama generation call returning a JSON string."""
        target_model = model or self.model
        self.calls.append({"model": target_model, "prompt": prompt})

        if self.mode == "offline":
            raise ConnectionError("Simulated Ollama daemon unavailable at localhost:11434")
        if self.mode == "timeout":
            raise TimeoutError("Simulated Ollama request timed out after 30s")

        if self.queued_responses:
            nxt = self.queued_responses.pop(0)
            return nxt if isinstance(nxt, str) else json.dumps(nxt, ensure_ascii=False)

        return json.dumps(self.default_payload, ensure_ascii=False)

    def generate_json(self, prompt: str, *, model: str | None = None) -> dict[str, Any]:
        """Parse simulated response text into JSON dict (raises JSONDecodeError if malformed)."""
        raw = self.generate(prompt, model=model)
        return json.loads(raw)

    def assert_no_pii_in_prompts(self) -> None:
        """Assert that none of the recorded prompts contain phone, email, or hash markers."""
        for idx, call in enumerate(self.calls):
            prompt = call["prompt"]
            assert not _PHONE_PATTERN.search(prompt), f"Phone PII leaked in prompt #{idx}"
            assert not _EMAIL_PATTERN.search(prompt), f"Email PII leaked in prompt #{idx}"
            assert not _HASH_PATTERN.search(prompt), f"Password hash marker leaked in prompt #{idx}"
