from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_LLM_BASE_URL = "http://127.0.0.1:1234/v1"
DEFAULT_LLM_API_KEY = "lm-studio"
DEFAULT_LLM_MODEL = "auto"
DEFAULT_LLM_PROVIDER = "local"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_OPENAI_MODEL = "gpt-4.1-mini"
DEFAULT_LLM_TIMEOUT_SECONDS = 420.0
DEFAULT_LLM_TEMPERATURE = 0.1
DEFAULT_ANALYSIS_LANGUAGE = "zh-Hans"
DEFAULT_ANALYSIS_AUDIENCE = "普通投资者"
DEFAULT_MAX_CONTEXT_CHARS = 120000
DEFAULT_SECTION_EXCERPT_CHARS = 24000
DEFAULT_OPENING_EXCERPT_CHARS = 8000
DEFAULT_CLOSING_EXCERPT_CHARS = 6000


@dataclass(slots=True)
class Settings:
    llm_provider: str = DEFAULT_LLM_PROVIDER
    llm_base_url: str = DEFAULT_LLM_BASE_URL
    llm_api_key: str = DEFAULT_LLM_API_KEY
    llm_model: str = DEFAULT_LLM_MODEL
    openai_organization: str = ""
    openai_project: str = ""
    llm_timeout_seconds: float = DEFAULT_LLM_TIMEOUT_SECONDS
    llm_temperature: float = DEFAULT_LLM_TEMPERATURE
    analysis_language: str = DEFAULT_ANALYSIS_LANGUAGE
    analysis_audience: str = DEFAULT_ANALYSIS_AUDIENCE
    max_context_chars: int = DEFAULT_MAX_CONTEXT_CHARS
    section_excerpt_chars: int = DEFAULT_SECTION_EXCERPT_CHARS
    opening_excerpt_chars: int = DEFAULT_OPENING_EXCERPT_CHARS
    closing_excerpt_chars: int = DEFAULT_CLOSING_EXCERPT_CHARS

    @classmethod
    def from_env(cls) -> "Settings":
        _load_local_env_file()
        provider = os.getenv("LLM_PROVIDER", DEFAULT_LLM_PROVIDER).strip().lower() or "local"

        if provider == "openai":
            llm_base_url = os.getenv("LLM_BASE_URL", DEFAULT_OPENAI_BASE_URL)
            llm_api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY", "")
            llm_model = os.getenv("LLM_MODEL") or os.getenv(
                "OPENAI_MODEL", DEFAULT_OPENAI_MODEL
            )
        else:
            llm_base_url = os.getenv("LLM_BASE_URL", DEFAULT_LLM_BASE_URL)
            llm_api_key = os.getenv("LLM_API_KEY", DEFAULT_LLM_API_KEY)
            llm_model = os.getenv("LLM_MODEL", DEFAULT_LLM_MODEL)

        return cls(
            llm_provider=provider,
            llm_base_url=llm_base_url,
            llm_api_key=llm_api_key,
            llm_model=llm_model,
            openai_organization=os.getenv("OPENAI_ORGANIZATION", ""),
            openai_project=os.getenv("OPENAI_PROJECT", ""),
            llm_timeout_seconds=float(
                os.getenv("LLM_TIMEOUT_SECONDS", str(DEFAULT_LLM_TIMEOUT_SECONDS))
            ),
            llm_temperature=float(
                os.getenv("LLM_TEMPERATURE", str(DEFAULT_LLM_TEMPERATURE))
            ),
            analysis_language=os.getenv("ANALYSIS_LANGUAGE", DEFAULT_ANALYSIS_LANGUAGE),
            analysis_audience=os.getenv("ANALYSIS_AUDIENCE", DEFAULT_ANALYSIS_AUDIENCE),
            max_context_chars=int(
                os.getenv("MAX_CONTEXT_CHARS", str(DEFAULT_MAX_CONTEXT_CHARS))
            ),
            section_excerpt_chars=int(
                os.getenv(
                    "SECTION_EXCERPT_CHARS", str(DEFAULT_SECTION_EXCERPT_CHARS)
                )
            ),
            opening_excerpt_chars=int(
                os.getenv(
                    "OPENING_EXCERPT_CHARS", str(DEFAULT_OPENING_EXCERPT_CHARS)
                )
            ),
            closing_excerpt_chars=int(
                os.getenv(
                    "CLOSING_EXCERPT_CHARS", str(DEFAULT_CLOSING_EXCERPT_CHARS)
                )
            ),
        )


def _load_local_env_file() -> None:
    candidates = [
        Path.cwd() / ".env.local",
        Path(__file__).resolve().parents[1] / ".env.local",
    ]
    seen: set[Path] = set()
    for path in candidates:
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if not key:
                continue
            value = value.strip().strip("'").strip('"')
            os.environ.setdefault(key, value)
