from __future__ import annotations

import tempfile
from dataclasses import replace
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from investor_digest.config import (
    DEFAULT_DEEPSEEK_BASE_URL,
    DEFAULT_DEEPSEEK_MODEL,
    DEFAULT_LLM_BASE_URL,
    DEFAULT_LLM_MODEL,
    DEFAULT_OPENAI_BASE_URL,
    DEFAULT_OPENAI_MODEL,
    Settings,
)
from investor_digest.llm_client import LocalOpenAIClient
from investor_digest.pipeline import analyze_path, prepare_path
from investor_digest.schemas import AnalyzePathRequest, ReportChatRequest


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="Investor Digest API", version="0.1.0")
    runtime_settings = settings or Settings.from_env()
    static_dir = Path(__file__).resolve().parent / "static"

    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/", response_class=FileResponse)
    def home() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    @app.get("/report", response_class=FileResponse)
    def report() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    @app.get("/health")
    def health() -> dict[str, str]:
        client = LocalOpenAIClient(runtime_settings)
        return {"status": "ok", "model": client.resolve_model_name()}

    @app.get("/api/filings")
    def list_local_filings(limit: int = 160) -> dict[str, object]:
        return {"filings": _list_local_filings(limit=max(1, min(limit, 500)))}

    @app.post("/api/prepare/path")
    def prepare_from_path(request: AnalyzePathRequest) -> dict[str, object]:
        request_settings = _settings_for_request(runtime_settings, request)
        try:
            prepared = prepare_path(request.path, settings=request_settings)
        except Exception as exc:  # pragma: no cover - API glue
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        return {
            "company_name": prepared.document.company_name,
            "reporting_period": prepared.document.reporting_period,
            "selected_file": str(prepared.document.selected_file),
            "warnings": prepared.warnings,
            "context_preview": prepared.context[:5000],
        }

    @app.post("/api/analyze/path")
    def analyze_from_path(request: AnalyzePathRequest) -> dict[str, object]:
        request_settings = _settings_for_request(runtime_settings, request)
        try:
            digest = analyze_path(
                request.path,
                settings=request_settings,
                audience=request.audience,
                language=request.language,
            )
        except Exception as exc:  # pragma: no cover - API glue
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        return digest.model_dump()

    @app.post("/api/analyze/file")
    async def analyze_from_file(
        file: UploadFile = File(...),
        audience: str | None = Form(default=None),
        language: str | None = Form(default=None),
        llm_provider: str | None = Form(default=None),
        llm_base_url: str | None = Form(default=None),
        llm_api_key: str | None = Form(default=None),
        llm_model: str | None = Form(default=None),
    ) -> dict[str, object]:
        request_settings = _settings_for_request(
            runtime_settings,
            {
                "llm_provider": llm_provider,
                "llm_base_url": llm_base_url,
                "llm_api_key": llm_api_key,
                "llm_model": llm_model,
            },
        )
        suffix = Path(file.filename or "upload.txt").suffix or ".txt"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(await file.read())
            temp_path = Path(tmp.name)

        try:
            digest = analyze_path(
                str(temp_path),
                settings=request_settings,
                audience=audience,
                language=language,
            )
        except Exception as exc:  # pragma: no cover - API glue
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            temp_path.unlink(missing_ok=True)

        return digest.model_dump()

    @app.post("/api/chat/report")
    def chat_with_report(request: ReportChatRequest) -> dict[str, object]:
        request_settings = _settings_for_request(runtime_settings, request)
        try:
            return _answer_report_question(request, settings=request_settings)
        except Exception as exc:  # pragma: no cover - API glue
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return app


app = create_app()


def _settings_for_request(settings: Settings, overrides: object) -> Settings:
    def get_value(name: str) -> str:
        if isinstance(overrides, dict):
            raw = overrides.get(name)
        else:
            raw = getattr(overrides, name, None)
        return str(raw or "").strip()

    provider = get_value("llm_provider").lower()
    base_url = get_value("llm_base_url")
    api_key = get_value("llm_api_key")
    model = get_value("llm_model")
    if not any((provider, base_url, api_key, model)):
        return settings

    resolved_provider = provider or settings.llm_provider
    resolved_base_url = base_url or settings.llm_base_url
    resolved_model = model or settings.llm_model
    resolved_api_key = api_key or settings.llm_api_key

    if provider == "deepseek":
        resolved_base_url = base_url or DEFAULT_DEEPSEEK_BASE_URL
        resolved_model = model or DEFAULT_DEEPSEEK_MODEL
    elif provider == "openai":
        resolved_base_url = base_url or DEFAULT_OPENAI_BASE_URL
        resolved_model = model or DEFAULT_OPENAI_MODEL
    elif provider == "local":
        resolved_base_url = base_url or DEFAULT_LLM_BASE_URL
        resolved_model = model or DEFAULT_LLM_MODEL

    return replace(
        settings,
        llm_provider=resolved_provider,
        llm_base_url=resolved_base_url,
        llm_api_key=resolved_api_key,
        llm_model=resolved_model,
    )


def _list_local_filings(*, limit: int) -> list[dict[str, str]]:
    root = Path.cwd() / "sec_filings" / "sec-edgar-filings"
    if not root.is_dir():
        return []

    filings: list[dict[str, str]] = []
    for ticker_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        form_dir = ticker_dir / "10-K"
        if not form_dir.is_dir():
            continue
        for accession_dir in sorted(path for path in form_dir.iterdir() if path.is_dir()):
            selected_file = _pick_demo_filing_file(accession_dir)
            if not selected_file:
                continue
            reporting_period = _extract_demo_reporting_period(selected_file)
            report_year = _extract_demo_accession_year(accession_dir.name) or _extract_demo_year(
                reporting_period
            )
            label_parts = [ticker_dir.name, report_year or "Unknown year", accession_dir.name]
            filings.append(
                {
                    "label": " | ".join(label_parts),
                    "ticker": ticker_dir.name,
                    "form": "10-K",
                    "accession": accession_dir.name,
                    "path": str(accession_dir),
                    "selected_file": str(selected_file),
                    "reporting_period": reporting_period,
                    "report_year": report_year,
                }
            )

    filings.sort(
        key=lambda item: (
            item["ticker"],
            -(int(item["report_year"]) if item["report_year"].isdigit() else 0),
            item["accession"],
        )
    )
    return filings[:limit]


def _pick_demo_filing_file(accession_dir: Path) -> Path | None:
    for filename in ("primary-document.html", "primary_doc.html", "full-submission.txt"):
        candidate = accession_dir / filename
        if candidate.is_file():
            return candidate
    return None


def _extract_demo_reporting_period(selected_file: Path) -> str:
    try:
        text = selected_file.read_text(encoding="utf-8", errors="ignore")[:120000]
    except OSError:
        return ""

    import re

    match = re.search(r"CONFORMED PERIOD OF REPORT:\s*(\d{8})", text, re.IGNORECASE)
    if match:
        raw = match.group(1)
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"

    normalized = re.sub(r"\s+", " ", text)
    match = re.search(
        r"for the fiscal year ended\s+([A-Za-z]+\s+\d{1,2}\s*,\s*20\d{2})",
        normalized,
        re.IGNORECASE,
    )
    if match:
        return re.sub(r"\s*,\s*", ", ", match.group(1)).strip()

    return ""


def _extract_demo_year(reporting_period: str) -> str:
    import re

    years = re.findall(r"20\d{2}", reporting_period or "")
    return years[-1] if years else ""


def _extract_demo_accession_year(accession: str) -> str:
    import re

    match = re.search(r"-(\d{2})-", accession or "")
    return f"20{match.group(1)}" if match else ""


def _answer_report_question(
    request: ReportChatRequest,
    *,
    settings: Settings,
) -> dict[str, object]:
    question = request.question.strip()
    if not question:
        raise ValueError("Question is required")

    compact_digest = _compact_digest_for_chat(request.digest)
    history = [
        {"role": item.role, "content": item.content[:1200]}
        for item in request.history[-6:]
        if item.content.strip()
    ]
    language = request.language or str(request.digest.get("analysis_language") or "zh-Hans")
    audience = request.audience or str(request.digest.get("audience") or "普通投资者")
    if _is_personal_investment_question(question):
        return _build_personal_question_fallback(
            question=question,
            digest=request.digest,
            upstream_error="",
        )

    client = LocalOpenAIClient(settings)
    try:
        payload = client.chat_json(
            system_prompt=_REPORT_CHAT_SYSTEM_PROMPT,
            user_prompt=(
                "Answer the user's follow-up question using only the report digest below.\n"
                "If the digest does not contain enough evidence, clearly say what is missing.\n"
                "Return JSON only with keys: answer_markdown, followups, warnings.\n\n"
                f"Language: {language}\n"
                f"Audience: {audience}\n"
                f"Question: {question}\n"
                f"Recent conversation: {history}\n\n"
                f"Report digest:\n{compact_digest}"
            ),
        )
    except Exception as exc:
        return _build_personal_question_fallback(
            question=question,
            digest=request.digest,
            upstream_error=str(exc),
        )
    answer = str(payload.get("answer_markdown") or "").strip()
    if not answer:
        raise ValueError("Model response did not include answer_markdown")
    followups = payload.get("followups") if isinstance(payload.get("followups"), list) else []
    warnings = payload.get("warnings") if isinstance(payload.get("warnings"), list) else []
    return {
        "answer_markdown": answer,
        "followups": [str(item) for item in followups[:4] if str(item).strip()],
        "warnings": [str(item) for item in warnings[:4] if str(item).strip()],
    }


_REPORT_CHAT_SYSTEM_PROMPT = """You are FinSight's report follow-up assistant.

Rules:
- Answer in the requested language.
- Use only the supplied report digest; do not invent new filing facts or numbers.
- If the user asks for a module, explain the relevant module and cite available metric/source labels.
- If the user asks whether something is trustworthy, discuss confidence, warnings, and available evidence.
- If the user asks what to do with their own holdings, do not recommend buy/sell/hold. Give a decision framework based on the report.
- Keep answers concise, practical, and educational.
- Do not provide investment advice or buy/sell recommendations.
- Return JSON only.
"""


def _is_personal_investment_question(question: str) -> bool:
    lowered = question.lower()
    personal_terms = ("我有", "我的", "持有", "股票", "仓位", "买", "卖", "减仓", "加仓", "apple股票", "aapl")
    action_terms = ("怎么办", "怎么处理", "适合", "该不该", "要不要", "buy", "sell", "hold")
    return any(term in lowered for term in personal_terms) and any(
        term in lowered for term in action_terms
    )


def _build_personal_question_fallback(
    *,
    question: str,
    digest: dict[str, object],
    upstream_error: str,
) -> dict[str, object]:
    company_name = str(digest.get("company_name") or "这家公司")
    facts = digest.get("fact_snapshot") if isinstance(digest.get("fact_snapshot"), list) else []
    key_facts = [
        f"{item.get('label')}: {item.get('value_text')}"
        for item in facts[:4]
        if isinstance(item, dict) and item.get("label") and item.get("value_text")
    ]
    risks = digest.get("risks") if isinstance(digest.get("risks"), list) else []
    watchlist = digest.get("watchlist") if isinstance(digest.get("watchlist"), list) else []
    answer_lines = [
        f"我不能根据你的个人持仓直接给出买入、卖出或继续持有的建议，但可以基于当前 {company_name} 财报给你一个检查框架。",
        "先判断你的原始买入逻辑是否仍成立：收入增长、利润率、现金流和主要风险有没有明显偏离你的预期。",
    ]
    if key_facts:
        answer_lines.append("这份报告里可以优先核对这些指标：" + "；".join(key_facts) + "。")
    if risks:
        answer_lines.append("风险上，建议重点看：" + "；".join(str(item) for item in risks[:3]) + "。")
    if watchlist:
        answer_lines.append("后续跟踪项可以包括：" + "；".join(str(item) for item in watchlist[:3]) + "。")
    answer_lines.extend(
        [
            "如果你已经持有，比较稳妥的做法是设定自己的复盘条件：目标持有周期、可承受回撤、仓位占比、以及哪些财报指标恶化时需要重新评估。",
            "如果问题是短期交易时点，这份年报本身信息不足，还需要结合估值、价格走势、你的资金期限和风险承受能力。",
        ]
    )
    warnings = ["外部模型暂时不可用，已切换为基于当前报告的本地兜底回答。"] if upstream_error else []
    return {
        "answer_markdown": "\n".join(answer_lines),
        "followups": [
            "这份财报里最值得跟踪的三个指标是什么？",
            "当前风险里哪些会影响未来利润率？",
            "帮我把持仓复盘清单列出来。",
        ],
        "warnings": warnings,
    }


def _compact_digest_for_chat(digest: dict[str, object]) -> str:
    import json

    allowed_keys = (
        "company_name",
        "reporting_period",
        "one_sentence_takeaway",
        "overview_markdown",
        "investor_view_markdown",
        "key_points",
        "positives",
        "risks",
        "watchlist",
        "glossary",
        "fact_snapshot",
        "evidence_cards",
        "chart_specs",
        "risk_disclaimer",
        "warnings",
    )
    compact: dict[str, object] = {}
    for key in allowed_keys:
        if key not in digest:
            continue
        compact[key] = _trim_chat_value(digest[key])
    text = json.dumps(compact, ensure_ascii=False, indent=2)
    return text[:24000]


def _trim_chat_value(value: object) -> object:
    if isinstance(value, str):
        return value[:1800]
    if isinstance(value, list):
        return [_trim_chat_value(item) for item in value[:10]]
    if isinstance(value, dict):
        return {str(key): _trim_chat_value(item) for key, item in list(value.items())[:18]}
    return value
