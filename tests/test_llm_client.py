import json

import httpx
import pytest

from airi.agents.requirement_parser.schemas import RequirementParseOutput
from airi.core.config import Settings
from airi.core.exceptions import LLMServiceError
from airi.infrastructure.llm import OpenAICompatibleLLMClient, strict_response_schema


def settings():
    return Settings(
        _env_file=None,
        llm_model="mock-model",
        llm_api_key="private-key",
        llm_base_url="https://llm.example/v1",
        llm_timeout_seconds=7,
    )


def invoke(transport):
    return OpenAICompatibleLLMClient(settings(), transport).complete_structured(
        system_prompt="system",
        user_prompt="user",
        schema=RequirementParseOutput.model_json_schema(),
    )


def test_http_adapter_sends_schema_and_uses_configured_endpoint():
    def handler(request):
        assert str(request.url) == "https://llm.example/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer private-key"
        body = json.loads(request.content)
        assert body["model"] == "mock-model"
        assert body["response_format"]["type"] == "json_schema"
        assert body["response_format"]["json_schema"]["strict"] is True
        assert body["messages"][0] == {"role": "system", "content": "system"}
        assert request.extensions["timeout"]["read"] == 7
        return httpx.Response(
            200,
            json={"choices": [{"finish_reason": "stop", "message": {"content": '{"test":true}'}}]},
        )

    assert invoke(httpx.MockTransport(handler)) == '{"test":true}'


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"choices": []},
        {"choices": [{"message": "bad-envelope"}]},
        {"choices": [{"finish_reason": "length", "message": {"content": "{}"}}]},
        {"choices": [{"finish_reason": "stop", "message": {"content": "", "refusal": "no"}}]},
        {"choices": [{"finish_reason": "stop", "message": {"content": None}}]},
    ],
)
def test_malformed_refused_or_truncated_response(body):
    with pytest.raises(LLMServiceError):
        invoke(httpx.MockTransport(lambda request: httpx.Response(200, json=body)))


@pytest.mark.parametrize("status", [400, 401, 429, 500, 302])
def test_provider_errors_no_retry_or_fallback(status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, text="private provider failure")

    with pytest.raises(LLMServiceError) as error:
        invoke(httpx.MockTransport(handler))
    assert "private" not in str(error.value)
    assert len(calls) == 1


def test_timeout():
    def handler(request):
        raise httpx.ReadTimeout("private endpoint", request=request)

    with pytest.raises(LLMServiceError):
        invoke(httpx.MockTransport(handler))


def test_invalid_provider_json():
    with pytest.raises(LLMServiceError):
        invoke(httpx.MockTransport(lambda request: httpx.Response(200, text="invalid")))


def test_all_structured_object_fields_required_without_mutating_local_schema():
    original = RequirementParseOutput.model_json_schema()
    output = strict_response_schema(original)
    metric = output["$defs"]["MetricIR"]
    assert set(metric["required"]) == set(metric["properties"])
    assert "default" not in metric["properties"]["schema_version"]
    assert "default" in original["$defs"]["MetricIR"]["properties"]["schema_version"]
    assert all(
        node["additionalProperties"] is False
        for node in output["$defs"].values()
        if node.get("type") == "object"
    )
