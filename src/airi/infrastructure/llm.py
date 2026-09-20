from abc import ABC, abstractmethod
from copy import deepcopy
from typing import Any

import httpx

from airi.core.config import Settings
from airi.core.exceptions import LLMConfigurationError, LLMServiceError


class LLMClient(ABC):
    @abstractmethod
    def complete_structured(
        self, *, system_prompt: str, user_prompt: str, schema: dict[str, Any]
    ) -> str:
        """Return raw JSON text. The caller MUST validate it before use."""


def strict_response_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Structured Outputs requires all object properties in required, including nullables."""
    result = deepcopy(schema)

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            node.pop("default", None)
            if node.get("type") == "object":
                node["required"] = list(node.get("properties", {}))
                node["additionalProperties"] = False
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(result)
    return result


class OpenAICompatibleLLMClient(LLMClient):
    """Small Chat Completions HTTP adapter; no SDK dependency or response fallback."""

    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None):
        self.settings = settings
        self.transport = transport

    def complete_structured(
        self, *, system_prompt: str, user_prompt: str, schema: dict[str, Any]
    ) -> str:
        if self.settings.llm_model == "configure-your-model":
            raise LLMConfigurationError("LLM model is not configured")
        headers = {"Content-Type": "application/json"}
        key = self.settings.llm_api_key.get_secret_value()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        payload = {
            "model": self.settings.llm_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "invoice_requirement",
                    "strict": True,
                    "schema": strict_response_schema(schema),
                },
            },
        }
        try:
            with httpx.Client(
                timeout=self.settings.llm_timeout_seconds,
                transport=self.transport,
                follow_redirects=False,
            ) as client:
                response = client.post(
                    str(self.settings.llm_base_url).rstrip("/") + "/chat/completions",
                    headers=headers,
                    json=payload,
                )
                response.raise_for_status()
            body = response.json()
            choice = body["choices"][0]
            message = choice["message"]
            if not isinstance(message, dict):
                raise ValueError("Invalid message envelope")
            content = message.get("content")
            if (
                choice.get("finish_reason") != "stop"
                or message.get("refusal")
                or not isinstance(content, str)
                or not content.strip()
            ):
                raise ValueError("No complete structured response")
            return content
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
            # Do not expose provider response bodies, endpoint credentials or prompts.
            raise LLMServiceError("Structured LLM request failed") from exc


class MockLLMClient(LLMClient):
    """Explicitly injected test double. Never selected as a production fallback."""

    model_identifier = "mock-structured@1.0.0"

    def __init__(self, response: str):
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def complete_structured(
        self, *, system_prompt: str, user_prompt: str, schema: dict[str, Any]
    ) -> str:
        self.calls.append(
            {"system_prompt": system_prompt, "user_prompt": user_prompt, "schema": schema}
        )
        return self.response
