from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import asdict

from investor_digest.config import Settings
from investor_digest.parser import build_prepared_context, parse_source
from investor_digest.schemas import InvestorDigest, PreparedContext


SYSTEM_PROMPT = """You are a financial education assistant.

Your job is to transform a company filing into a plain-language explainer for ordinary investors.

Rules:
- Write in the requested language.
- Stay faithful to the filing context.
- Do not invent numbers.
- If data is incomplete, say so in warnings.
- Explain jargon simply.
- Use a calm, educational tone, not hype.
- Return JSON only.
- For chart_specs, include at most 3 charts.
- Only include chart data when the numbers are clearly supported by the provided context.
- Use color-blind-friendly palettes such as blue, teal, orange, and slate.
"""


def analyze_path(
    path: str,
    *,
    settings: Settings,
    audience: str | None = None,
    language: str | None = None,
) -> InvestorDigest:
    audience = audience or settings.analysis_audience
    language = language or settings.analysis_language

    document = parse_source(path)
    resolved_model = _resolve_runtime_model_name(settings)
    model_scale = _context_scale_for_model(resolved_model)
    base_context_limit = max(12000, int(settings.max_context_chars * model_scale))
    base_opening_limit = max(1800, int(settings.opening_excerpt_chars * max(0.55, model_scale)))
    base_section_limit = max(3200, int(settings.section_excerpt_chars * model_scale))
    base_closing_limit = max(1600, int(settings.closing_excerpt_chars * max(0.6, model_scale)))
    reductions = [1.0, 0.82, 0.68, 0.56, 0.44, 0.34]
    last_error: Exception | None = None

    for attempt, reduction in enumerate(reductions):
        context_limit = max(6000, int(base_context_limit * reduction))
        opening_limit = max(1600, int(base_opening_limit * reduction))
        section_limit = max(2600, int(base_section_limit * reduction))
        closing_limit = max(1400, int(base_closing_limit * reduction))
        use_modular_generation = attempt < 3
        prepared = build_prepared_context(
            document,
            max_chars=context_limit,
            opening_excerpt_chars=opening_limit,
            section_excerpt_chars=section_limit,
            closing_excerpt_chars=closing_limit,
        )
        try:
            if use_modular_generation:
                digest = _run_modular_digest_generation(
                    prepared=prepared,
                    settings=settings,
                    audience=audience,
                    language=language,
                )
            else:
                digest = _run_digest_generation(
                    prepared=prepared,
                    settings=settings,
                    audience=audience,
                    language=language,
                    strict_numeric=True,
                )
            if _needs_numeric_retry(digest, prepared):
                if use_modular_generation:
                    digest = _run_modular_digest_generation(
                        prepared=prepared,
                        settings=settings,
                        audience=audience,
                        language=language,
                        strict_numeric=True,
                    )
                else:
                    digest = _run_digest_generation(
                        prepared=prepared,
                        settings=settings,
                        audience=audience,
                        language=language,
                        strict_numeric=True,
                    )
                digest.warnings.append(
                    "The report was regenerated with stricter numeric constraints."
                )
            _enforce_minimum_information_quality(digest, prepared)
            digest.warnings.extend(
                warning for warning in prepared.warnings if warning not in digest.warnings
            )
            if model_scale < 1.0:
                digest.warnings.append(
                    f"Runtime model {resolved_model} is using a reduced context budget."
                )
            if attempt > 0:
                digest.warnings.append(
                    "The filing context budget was reduced during retry."
                )
            if not use_modular_generation:
                digest.warnings.append(
                    "The system switched to a lighter single-pass generation mode."
                )
            return digest
        except Exception as exc:
            last_error = exc
            if (
                not _is_context_window_error(exc)
                and not _is_timeout_error(exc)
            ) or attempt == len(reductions) - 1:
                raise

    assert last_error is not None
    raise last_error


def prepare_path(path: str, *, settings: Settings) -> PreparedContext:
    document = parse_source(path)
    resolved_model = _resolve_runtime_model_name(settings)
    model_scale = _context_scale_for_model(resolved_model)
    return build_prepared_context(
        document,
        max_chars=max(12000, int(settings.max_context_chars * model_scale)),
        opening_excerpt_chars=max(
            1800, int(settings.opening_excerpt_chars * max(0.55, model_scale))
        ),
        section_excerpt_chars=max(3200, int(settings.section_excerpt_chars * model_scale)),
        closing_excerpt_chars=max(
            1600, int(settings.closing_excerpt_chars * max(0.6, model_scale))
        ),
    )


def _run_digest_generation(
    *,
    prepared: PreparedContext,
    settings: Settings,
    audience: str,
    language: str,
    strict_numeric: bool = False,
) -> InvestorDigest:
    from investor_digest.llm_client import LocalOpenAIClient

    client = LocalOpenAIClient(settings)
    user_prompt = _build_user_prompt(
        prepared,
        audience=audience,
        language=language,
        strict_numeric=strict_numeric,
    )
    payload = client.chat_json(system_prompt=SYSTEM_PROMPT, user_prompt=user_prompt)
    payload = _normalize_payload(payload, prepared=prepared, audience=audience, language=language)
    digest = InvestorDigest.model_validate(payload)

    if not digest.company_name:
        digest.company_name = prepared.document.company_name
    if not digest.reporting_period:
        digest.reporting_period = prepared.document.reporting_period
    if not digest.analysis_language:
        digest.analysis_language = language
    if not digest.audience:
        digest.audience = audience

    return digest


def _run_modular_digest_generation(
    *,
    prepared: PreparedContext,
    settings: Settings,
    audience: str,
    language: str,
    strict_numeric: bool = False,
) -> InvestorDigest:
    from investor_digest.llm_client import LocalOpenAIClient

    client = LocalOpenAIClient(settings)
    financial_payload = client.chat_json(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=_build_financial_module_prompt(
            prepared,
            audience=audience,
            language=language,
            strict_numeric=strict_numeric,
        ),
    )
    text_payload = client.chat_json(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=_build_text_module_prompt(
            prepared,
            audience=audience,
            language=language,
            strict_numeric=strict_numeric,
        ),
    )
    synthesis_payload = client.chat_json(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=_build_synthesis_prompt(
            prepared,
            financial_payload=financial_payload,
            text_payload=text_payload,
            audience=audience,
            language=language,
            strict_numeric=strict_numeric,
        ),
    )
    merged_payload = _merge_module_payloads(
        prepared=prepared,
        audience=audience,
        language=language,
        financial_payload=financial_payload,
        text_payload=text_payload,
        synthesis_payload=synthesis_payload,
    )
    merged_payload = _normalize_payload(
        merged_payload,
        prepared=prepared,
        audience=audience,
        language=language,
    )
    return InvestorDigest.model_validate(merged_payload)


def _build_user_prompt(
    prepared: PreparedContext,
    *,
    audience: str,
    language: str,
    strict_numeric: bool = False,
) -> str:
    document = prepared.document
    structured_facts = (
        "\n".join(f"- {fact}" for fact in prepared.financial_facts)
        if prepared.financial_facts
        else "- No structured financial facts were extracted confidently."
    )
    extra_constraints = """
- Prioritize financial performance, profitability, cash flow, segment/product performance, and balance-sheet strength before product storytelling.
- If structured facts include revenue, margin, profit, cash flow, deliveries, or deployment numbers, use them explicitly.
- Do not let market capitalization or share count dominate the report unless core operating metrics are missing.
- If a key metric is missing, say it is missing instead of replacing it with generic strategic language.
- one_sentence_takeaway must include at least one concrete number whenever one is available in the structured facts.
- At least 4 bullets across key_points, positives, risks, and watchlist should contain a concrete number, percentage, or explicitly say the metric was not found.
- If there are not enough concrete financial facts for 3 good charts, return fewer charts instead of weak filler charts.
- When revenue, costs, gross profit, operating income, and net income can be linked, prefer one sankey chart that shows the profit flow.
""".strip()
    if strict_numeric:
        extra_constraints += """
- This is a retry because the previous draft was too generic.
- Be specific. Do not write vague claims like 'focuses on AI' unless tied to revenue, margin, cash flow, deliveries, deployment, or stated filing impact.
- If the filing text is incomplete, make that the headline rather than pretending you have a full financial read.
""".strip()
    return f"""Analyze the filing context below and return a JSON object with exactly these top-level keys:

company_name
reporting_period
analysis_language
audience
one_sentence_takeaway
overview_markdown
key_points
positives
risks
watchlist
glossary
chart_specs
risk_disclaimer
warnings

Shape requirements:
- key_points, positives, risks, watchlist: arrays of short strings
- glossary: array of objects with keys term and plain_explanation
- chart_specs: array of objects with keys title, chart_type, why_it_matters, x_axis_label, categories, series, flow_nodes, flow_links, palette, source_snippet, confidence
- for bar/line/area/stacked_bar/donut, each chart series object must have keys name, unit, values
- for sankey, flow_nodes is an array of objects with keys name, value, item_style_color and flow_links is an array of objects with keys source, target, value

Language: {language}
Audience: {audience}
Company fallback: {document.company_name}
Reporting period fallback: {document.reporting_period}

Extra output requirements:
- overview_markdown should be concise and readable on a consumer-facing page
- mention both upside and downside
- glossary should only include terms that actually appear or matter
- risk_disclaimer should clearly state this is educational content, not personalized investment advice
- warnings should mention any ambiguity, missing context, or low-confidence chart data
{extra_constraints}

Structured facts extracted before generation:
{structured_facts}

Filing context:
{prepared.context}
"""


def _build_financial_module_prompt(
    prepared: PreparedContext,
    *,
    audience: str,
    language: str,
    strict_numeric: bool,
) -> str:
    financial_snapshot = prepared.financial_snapshot
    key_explanations = _serialize_summary_cards(prepared.key_explanations)
    accounting_flags = _serialize_summary_cards(prepared.accounting_flags)
    extra = (
        "Use explicit numbers whenever possible. Prefer revenue, profit, margin, EPS, cash flow, and balance sheet facts over strategy."
        if not strict_numeric
        else "Be strict. If a metric is missing, write 'Not mentioned in the provided report.' Do not substitute vague business language."
    )
    return f"""You are a financial report analysis assistant.

Focus only on the provided structured metrics and finance-related filing excerpts.
Do not make up facts.
If information is missing, say "Not mentioned in the provided report."
Explain in simple language for non-expert users.

Analyze only the financial data and finance-related filing excerpts below.

Return JSON with exactly these keys:
company_overview
revenue_trend
profit_trend
cash_flow_summary
key_financial_points
chart_specs
warnings

Rules:
- Language: {language}
- Audience: {audience}
- company_overview, revenue_trend, profit_trend, cash_flow_summary must each be a short paragraph
- key_financial_points must be an array of short strings
- chart_specs may contain bar, line, area, stacked_bar, donut, or sankey
- every chart spec must include title, why_it_matters, source_snippet, confidence
- for non-sankey charts include x_axis_label, categories, and series
- for sankey charts include flow_nodes and flow_links, and x_axis_label may be an empty string
- If you do not have enough supported numeric data for a chart, return an empty chart_specs array instead of inventing chart data.
- Prefer one sankey chart when revenue, costs, gross profit, operating income, and net income can be connected
- Use financial_snapshot as the only source of core financial numbers.
- Do not derive or overwrite revenue, net income, operating income, gross margin, EPS, or cash flow from explanations.
- Use explanation and accounting objects only to explain why numbers changed.
- {extra}

Financial snapshot:
{json.dumps(financial_snapshot, ensure_ascii=False, indent=2) if financial_snapshot else '"Not mentioned in the provided report."'}

Key explanations:
{json.dumps(key_explanations, ensure_ascii=False, indent=2) if key_explanations else '"Not mentioned in the provided report."'}

Accounting flags:
{json.dumps(accounting_flags, ensure_ascii=False, indent=2) if accounting_flags else '"Not mentioned in the provided report."'}
"""


def _build_text_module_prompt(
    prepared: PreparedContext,
    *,
    audience: str,
    language: str,
    strict_numeric: bool,
) -> str:
    company_profile = asdict(prepared.company_profile) if prepared.company_profile else {}
    key_risks = _serialize_risk_cards(prepared.key_risks)
    outlook_signals = _serialize_summary_cards(prepared.outlook_signals)
    accounting_flags = _serialize_summary_cards(prepared.accounting_flags)
    extra = (
        "Focus on business model, competitive position, and concrete risks."
        if not strict_numeric
        else "Be strict. Avoid marketing language and avoid repeating the same point in different words."
    )
    return f"""You are a financial report analysis assistant.

Focus only on the provided text sections.
Do not make up facts.
If information is missing, say "Not mentioned in the provided report."
Explain in simple language for non-expert users.

Analyze only the business, risk, and management discussion excerpts below.

Return JSON with exactly these keys:
business_model
main_risks
watchlist
plain_language_summary
glossary
warnings

Rules:
- Language: {language}
- Audience: {audience}
- main_risks and watchlist must be arrays of short strings
- glossary must be an array of objects with keys term and plain_explanation
- {extra}
- plain_language_summary should stay neutral and educational
- If a topic is missing, write 'Not mentioned in the provided report.'

Company profile:
{json.dumps(company_profile, ensure_ascii=False, indent=2) if company_profile else '"Not mentioned in the provided report."'}

Risk cards:
{json.dumps(key_risks, ensure_ascii=False, indent=2) if key_risks else '"Not mentioned in the provided report."'}

Outlook signals:
{json.dumps(outlook_signals, ensure_ascii=False, indent=2) if outlook_signals else '"Not mentioned in the provided report."'}

Accounting flags:
{json.dumps(accounting_flags, ensure_ascii=False, indent=2) if accounting_flags else '"Not mentioned in the provided report."'}
"""


def _build_synthesis_prompt(
    prepared: PreparedContext,
    *,
    financial_payload: dict,
    text_payload: dict,
    audience: str,
    language: str,
    strict_numeric: bool,
) -> str:
    extra = (
        "Keep the summary grounded and concise."
        if not strict_numeric
        else "Be extremely grounded. Prefer direct numbers and explicitly say when coverage is incomplete."
    )
    return f"""You are a financial report analysis assistant synthesizing two module outputs into a fixed report format.

Focus only on the provided module outputs.
Do not make up facts.
If information is missing, say "Not mentioned in the provided report."
Explain in simple language for non-expert users.

Return JSON with exactly these keys:
one_sentence_takeaway
overview_markdown
key_points
positives
risks
watchlist
risk_disclaimer
warnings

Rules:
- Language: {language}
- Audience: {audience}
- key_points, positives, risks, watchlist must be arrays of short strings
- one_sentence_takeaway should include at least one concrete financial number when available
- overview_markdown should follow this order:
  1. Company overview
  2. Revenue trend
  3. Profit trend
  4. Main risks
  5. Plain-language summary
- {extra}

Financial module output:
{json.dumps(financial_payload, ensure_ascii=False, indent=2)}

Business/risk module output:
{json.dumps(text_payload, ensure_ascii=False, indent=2)}

Company fallback: {prepared.document.company_name}
Reporting period fallback: {prepared.document.reporting_period}

Investor summary input:
{json.dumps(prepared.investor_summary_input, ensure_ascii=False, indent=2)}
"""


def _merge_module_payloads(
    *,
    prepared: PreparedContext,
    audience: str,
    language: str,
    financial_payload: dict,
    text_payload: dict,
    synthesis_payload: dict,
) -> dict:
    merged = {
        "company_name": prepared.document.company_name,
        "reporting_period": prepared.document.reporting_period,
        "analysis_language": language,
        "audience": audience,
        "one_sentence_takeaway": synthesis_payload.get("one_sentence_takeaway", ""),
        "overview_markdown": synthesis_payload.get("overview_markdown", ""),
        "key_points": synthesis_payload.get("key_points")
        or financial_payload.get("key_financial_points")
        or [],
        "positives": synthesis_payload.get("positives", []),
        "risks": synthesis_payload.get("risks")
        or text_payload.get("main_risks")
        or [],
        "watchlist": synthesis_payload.get("watchlist")
        or text_payload.get("watchlist")
        or [],
        "glossary": text_payload.get("glossary", []),
        "chart_specs": financial_payload.get("chart_specs", []),
        "risk_disclaimer": synthesis_payload.get("risk_disclaimer")
        or "本内容仅用于教育和信息目的，不构成个性化投资建议。请结合完整财报和专业意见进行判断。",
        "warnings": [
            *synthesis_payload.get("warnings", []),
            *financial_payload.get("warnings", []),
            *text_payload.get("warnings", []),
        ],
    }
    return merged


def _needs_numeric_retry(digest: InvestorDigest, prepared: PreparedContext) -> bool:
    if not prepared.financial_facts:
        return False

    numeric_claims = _count_numeric_claims(
        [
            digest.one_sentence_takeaway,
            digest.overview_markdown,
            *digest.key_points,
            *digest.positives,
            *digest.risks,
            *digest.watchlist,
        ]
    )
    low_confidence_charts = sum(1 for chart in digest.chart_specs if chart.confidence == "low")
    return numeric_claims < min(5, len(prepared.financial_facts)) or (
        digest.chart_specs and low_confidence_charts == len(digest.chart_specs)
    )


def _enforce_minimum_information_quality(
    digest: InvestorDigest,
    prepared: PreparedContext,
) -> None:
    numeric_claims = _count_numeric_claims(
        [
            digest.one_sentence_takeaway,
            digest.overview_markdown,
            *digest.key_points,
            *digest.positives,
            *digest.risks,
            *digest.watchlist,
        ]
    )
    if prepared.financial_facts and numeric_claims >= 4:
        return

    fallback_points = _build_fallback_points(prepared.financial_facts)
    if fallback_points:
        digest.key_points = _merge_unique_items(fallback_points, digest.key_points, limit=6)

    digest.warnings.insert(
        0,
        "The report had limited numeric coverage, so extracted fact snippets were added to preserve key financial details.",
    )

    if numeric_claims == 0 and prepared.financial_facts:
        digest.one_sentence_takeaway = (
            "原始文本中的具体财务指标抽取有限，当前总结以已识别到的数字片段为主，不应视为完整年报解读。"
        )


def _build_fallback_points(financial_facts: list[str]) -> list[str]:
    points = []
    for fact in financial_facts[:4]:
        text = fact.strip()
        if len(text) > 180:
            text = text[:177].rstrip() + "..."
        points.append(text)
    return points


def _merge_unique_items(primary: list[str], secondary: list[str], *, limit: int) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for item in [*primary, *secondary]:
        normalized = item.strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        merged.append(item.strip())
        if len(merged) >= limit:
            break
    return merged


def _count_numeric_claims(items: list[str]) -> int:
    return sum(1 for item in items if _contains_numeric_claim(item))


def _contains_numeric_claim(text: str) -> bool:
    return bool(text and any(char.isdigit() for char in text))


def _serialize_metric_records(prepared: PreparedContext) -> list[dict]:
    return [asdict(record) for record in prepared.metric_records if record.valid]


def _serialize_summary_cards(cards: list) -> list[dict]:
    return [asdict(card) for card in cards]


def _serialize_risk_cards(cards: list) -> list[dict]:
    return [asdict(card) for card in cards]


def _serialize_chunks(chunks: list) -> list[dict]:
    serialized = []
    for chunk in chunks:
        item = asdict(chunk)
        item["text"] = str(item.get("text", "")).strip()
        if item["text"]:
            serialized.append(item)
    return serialized


def _extract_context_blocks(context: str) -> dict[str, str]:
    blocks: dict[str, list[str]] = {}
    current_title: str | None = None

    for line in context.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]") and len(stripped) > 2:
            current_title = stripped[1:-1]
            blocks.setdefault(current_title, [])
            continue

        if current_title:
            blocks[current_title].append(line)

    return {
        title: "\n".join(lines).strip()
        for title, lines in blocks.items()
        if "\n".join(lines).strip()
    }


def _resolve_runtime_model_name(settings: Settings) -> str:
    from investor_digest.llm_client import LocalOpenAIClient

    try:
        return LocalOpenAIClient(settings).resolve_model_name()
    except Exception:
        return settings.llm_model


def _context_scale_for_model(model_name: str) -> float:
    lowered = model_name.lower()
    if "4b" in lowered:
        return 0.55
    if "7b" in lowered or "8b" in lowered or "9b" in lowered:
        return 0.85
    if "12b" in lowered or "13b" in lowered or "14b" in lowered:
        return 0.95
    if "27b" in lowered or "30b" in lowered or "32b" in lowered or "35b" in lowered:
        return 1.0
    return 0.9


def _is_context_window_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return (
        "context length" in message
        or "maximum context length" in message
        or "number of tokens to keep" in message
        or "too many tokens" in message
        or "prompt is too long" in message
        or "provide a shorter input" in message
    )


def _is_timeout_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return (
        "timed out" in message
        or "timeout" in message
        or "readtimeout" in message
        or "read timeout" in message
    )


def _normalize_payload(
    payload: dict,
    *,
    prepared: PreparedContext,
    audience: str,
    language: str,
) -> dict:
    normalized = deepcopy(payload)
    normalized.setdefault("company_name", prepared.document.company_name)
    normalized.setdefault("reporting_period", prepared.document.reporting_period)
    normalized.setdefault("analysis_language", language)
    normalized.setdefault("audience", audience)
    normalized.setdefault("key_points", [])
    normalized.setdefault("positives", [])
    normalized.setdefault("risks", [])
    normalized.setdefault("watchlist", [])
    normalized.setdefault("glossary", [])
    normalized.setdefault("chart_specs", [])
    normalized.setdefault("warnings", [])
    normalized["key_points"] = _ensure_string_list(normalized.get("key_points"))
    normalized["positives"] = _ensure_string_list(normalized.get("positives"))
    normalized["risks"] = _ensure_string_list(normalized.get("risks"))
    normalized["watchlist"] = _ensure_string_list(normalized.get("watchlist"))
    normalized["warnings"] = _ensure_string_list(normalized.get("warnings"))

    glossary = normalized.get("glossary") or []
    if not isinstance(glossary, list):
        glossary = []
    normalized["glossary"] = [item for item in glossary if isinstance(item, dict)]

    cleaned_charts = []
    for chart in normalized.get("chart_specs", []):
        if not isinstance(chart, dict):
            continue
        if not chart.get("chart_type") and chart.get("type"):
            chart["chart_type"] = chart.get("type")
        chart["title"] = str(chart.get("title") or "Untitled chart").strip()
        chart["why_it_matters"] = str(chart.get("why_it_matters") or "").strip()
        chart["x_axis_label"] = str(chart.get("x_axis_label") or "").strip()
        chart["source_snippet"] = str(chart.get("source_snippet") or "").strip()
        palette = chart.get("palette") or []
        if not isinstance(palette, list):
            palette = []
        chart["palette"] = [str(item) for item in palette if str(item).strip()]
        categories = chart.get("categories") or []
        if not isinstance(categories, list):
            categories = list(categories) if isinstance(categories, tuple) else []
        chart["categories"] = [str(item) for item in categories]
        chart["confidence"] = _normalize_confidence(chart.get("confidence"))
        chart_type = str(chart.get("chart_type", "bar")).lower()
        chart["chart_type"] = _normalize_chart_type(chart_type)

        series_list = chart.get("series") or []
        if not isinstance(series_list, list):
            series_list = []
        for series in series_list:
            values = series.get("values")
            if isinstance(values, dict):
                if not chart["categories"]:
                    chart["categories"] = [str(key) for key in values.keys()]
                series["values"] = [
                    _coerce_number(values.get(category)) for category in chart["categories"]
                ]
            elif isinstance(values, list):
                series["values"] = [_coerce_number(value) for value in values]
            else:
                series["values"] = []

        flow_nodes = chart.get("flow_nodes") or []
        if not isinstance(flow_nodes, list):
            flow_nodes = []
        normalized_nodes = []
        for node in flow_nodes:
            if not isinstance(node, dict):
                continue
            normalized_nodes.append(
                {
                    "name": str(node.get("name", "")).strip(),
                    "value": _coerce_number(node.get("value")) if node.get("value") is not None else None,
                    "item_style_color": str(
                        node.get("item_style_color") or node.get("color") or ""
                    ).strip()
                    or None,
                }
            )
        chart["flow_nodes"] = [node for node in normalized_nodes if node["name"]]

        flow_links = chart.get("flow_links") or chart.get("links") or []
        if not isinstance(flow_links, list):
            flow_links = []
        normalized_links = []
        for link in flow_links:
            if not isinstance(link, dict):
                continue
            source = str(link.get("source", "")).strip()
            target = str(link.get("target", "")).strip()
            if not source or not target:
                continue
            normalized_links.append(
                {
                    "source": source,
                    "target": target,
                    "value": _coerce_number(link.get("value")),
                }
            )
        chart["flow_links"] = [link for link in normalized_links if link["value"] > 0]
        if chart["chart_type"] == "sankey":
            if not chart["flow_links"]:
                continue
        else:
            if not chart["categories"] or not any(
                isinstance(series, dict) and series.get("values") for series in series_list
            ):
                continue
        cleaned_charts.append(chart)

    normalized["chart_specs"] = cleaned_charts

    return normalized


def _normalize_confidence(value: object) -> str:
    text = str(value or "").lower()
    if "高" in text or "high" in text:
        return "high"
    if "低" in text or "low" in text:
        return "low"
    return "medium"


def _normalize_chart_type(value: str) -> str:
    aliases = {
        "column": "bar",
        "bars": "bar",
        "linechart": "line",
        "pie": "donut",
        "stackedbar": "stacked_bar",
        "flow": "sankey",
        "moneyflow": "sankey",
    }
    compact = value.replace("-", "").replace("_", "").replace(" ", "")
    if compact in aliases:
        return aliases[compact]
    allowed = {"bar", "line", "area", "stacked_bar", "donut", "sankey"}
    return value if value in allowed else "bar"


def _coerce_number(value: object) -> float:
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).replace(",", "").replace("$", "").strip()
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def _ensure_string_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if value is None:
        return []
    text = str(value).strip()
    return [text] if text else []
