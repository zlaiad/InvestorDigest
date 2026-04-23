from __future__ import annotations

import json
import re
import time
from typing import Any

import httpx

from investor_digest.config import Settings


class LocalOpenAIClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.base_url = settings.llm_base_url.rstrip("/")
        self.chat_url = f"{self.base_url}/chat/completions"
        self.models_url = f"{self.base_url}/models"
        self._resolved_model: str | None = None

    def resolve_model_name(self) -> str:
        if self._resolved_model:
            return self._resolved_model

        configured = self.settings.llm_model.strip()
        try:
            available = self._list_models()
        except Exception:
            self._resolved_model = configured
            return configured

        if not available:
            self._resolved_model = configured
            return configured

        if configured.lower() in {"", "auto"}:
            self._resolved_model = _pick_default_model(available)
            return self._resolved_model

        if configured in available:
            self._resolved_model = configured
            return configured

        normalized_requested = _normalize_model_id(configured)
        ranked = sorted(
            available,
            key=lambda candidate: _model_match_score(normalized_requested, candidate),
            reverse=True,
        )
        best = ranked[0]
        if _model_match_score(normalized_requested, best) <= 0:
            self._resolved_model = configured
            return configured

        self._resolved_model = best
        return best

    def chat_json(self, *, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        base_payload = {
            "model": self.resolve_model_name(),
            "temperature": self.settings.llm_temperature,
            "max_tokens": self.settings.llm_max_output_tokens,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }

        candidates = _build_response_candidates(base_payload, self.settings.llm_provider)

        last_error: Exception | None = None
        for payload in candidates:
            try:
                return self._post_and_parse(payload)
            except (ValueError, json.JSONDecodeError) as exc:
                last_error = exc
                continue
            except RuntimeError as exc:
                last_error = exc
                if _is_response_format_error(str(exc)):
                    continue
                raise

        if last_error is not None:
            raise last_error
        raise RuntimeError("Local model request failed before a response was received")

    def _post_and_parse(self, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.settings.llm_api_key}",
            "Content-Type": "application/json",
        }
        if self.settings.openai_organization:
            headers["OpenAI-Organization"] = self.settings.openai_organization
        if self.settings.openai_project:
            headers["OpenAI-Project"] = self.settings.openai_project

        with httpx.Client(timeout=self.settings.llm_timeout_seconds) as client:
            response = self._post_with_retry(client=client, headers=headers, payload=payload)
            data = response.json()

        content = data["choices"][0]["message"]["content"]
        if isinstance(content, list):
            text = "".join(
                item.get("text", "")
                for item in content
                if isinstance(item, dict) and item.get("type") == "text"
            )
        else:
            text = str(content)

        return _extract_json(text)

    def _post_with_retry(
        self,
        *,
        client: httpx.Client,
        headers: dict[str, str],
        payload: dict[str, Any],
    ) -> httpx.Response:
        retry_statuses = {429, 500, 502, 503, 504}
        delays = [1.5, 3.0, 6.0]
        last_response: httpx.Response | None = None

        for attempt in range(len(delays) + 1):
            response = client.post(self.chat_url, headers=headers, json=payload)
            last_response = response
            if not response.is_error:
                return response
            if response.status_code not in retry_statuses or attempt == len(delays):
                break
            time.sleep(delays[attempt])

        assert last_response is not None
        detail = last_response.text.strip() or "no response body"
        raise RuntimeError(
            f"Local model request failed with {last_response.status_code}: {detail}"
        )

    def _list_models(self) -> list[str]:
        headers = {"Authorization": f"Bearer {self.settings.llm_api_key}"}
        if self.settings.openai_organization:
            headers["OpenAI-Organization"] = self.settings.openai_organization
        if self.settings.openai_project:
            headers["OpenAI-Project"] = self.settings.openai_project
        with httpx.Client(timeout=min(self.settings.llm_timeout_seconds, 20.0)) as client:
            response = client.get(self.models_url, headers=headers)
            response.raise_for_status()
            payload = response.json()

        data = payload.get("data") or []
        models = []
        for item in data:
            if isinstance(item, dict) and item.get("id"):
                models.append(str(item["id"]))
        return models


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()

    if text.startswith("{") and text.endswith("}"):
        return json.loads(text)

    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fenced:
        return json.loads(fenced.group(1))

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return json.loads(text[start : end + 1])

    raise ValueError("Model response did not contain valid JSON")


def _build_response_candidates(
    base_payload: dict[str, Any], provider: str
) -> list[dict[str, Any]]:
    if provider == "deepseek":
        return [
            {
                **base_payload,
                "response_format": {"type": "json_object"},
            },
            {
                **base_payload,
                "response_format": {"type": "text"},
            },
            base_payload,
        ]

    return [
        {
            **base_payload,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "investor_digest_response",
                    "schema": {
                        "type": "object",
                        "additionalProperties": True,
                    },
                },
            },
        },
        {
            **base_payload,
            "response_format": {"type": "text"},
        },
        base_payload,
    ]


def _is_response_format_error(message: str) -> bool:
    lowered = message.lower()
    return "response_format" in lowered or "json_schema" in lowered


def _normalize_model_id(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _model_match_score(requested: str, candidate: str) -> int:
    normalized_candidate = _normalize_model_id(candidate)
    if requested == normalized_candidate:
        return 1000
    if requested and normalized_candidate.startswith(requested):
        return 900 - len(normalized_candidate)
    if requested and requested in normalized_candidate:
        return 800 - len(normalized_candidate)
    if normalized_candidate and normalized_candidate in requested:
        return 700 - len(requested)
    return 0


def _pick_default_model(models: list[str]) -> str:
    text_models = [
        model for model in models if "embed" not in model.lower() and "embedding" not in model.lower()
    ]
    if not text_models:
        return models[0]

    ranked = sorted(
        text_models,
        key=lambda model: (_preferred_family_score(model), _model_capacity_score(model), model),
        reverse=True,
    )
    return ranked[0]


def _preferred_family_score(model: str) -> int:
    lowered = model.lower()
    if "deepseek" in lowered:
        return 4
    if "qwen" in lowered:
        return 3
    if "llama" in lowered:
        return 2
    return 1


def _model_capacity_score(model: str) -> float:
    lowered = model.lower()
    match = re.search(r"(\d+(?:\.\d+)?)b", lowered)
    if not match:
        return 0.0

    size = float(match.group(1))
    if "a3b" in lowered:
        size += 0.25
    if "mlx" in lowered:
        size += 0.05
    return size
