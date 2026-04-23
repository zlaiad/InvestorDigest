from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import asdict

from investor_digest.config import Settings
from investor_digest.llm_client import LocalOpenAIClient
from investor_digest.parser import (
    CORE_METRIC_SPECS,
    _extract_item8_region,
    _extract_ixbrl_profit_flow_totals,
    _extract_ixbrl_revenue_composition,
    _build_financial_snapshot,
    _extract_period_from_text,
    _format_metric_record,
    _group_table_chunks,
    _validate_metric_value,
    build_prepared_context,
    parse_source,
)
from investor_digest.schemas import InvestorDigest, MetricRecord, PreparedContext


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
    uses_cloud_model = settings.uses_cloud_model
    model_scale = 1.0 if uses_cloud_model else _context_scale_for_model(resolved_model)
    base_context_limit = max(12000, int(settings.max_context_chars * model_scale))
    base_opening_limit = max(1800, int(settings.opening_excerpt_chars * max(0.55, model_scale)))
    base_section_limit = max(3200, int(settings.section_excerpt_chars * model_scale))
    base_closing_limit = max(1600, int(settings.closing_excerpt_chars * max(0.6, model_scale)))
    reductions = [1.0, 0.92, 0.84] if uses_cloud_model else [1.0, 0.82, 0.68, 0.56, 0.44, 0.34]
    last_error: Exception | None = None

    for attempt, reduction in enumerate(reductions):
        context_limit = max(6000, int(base_context_limit * reduction))
        opening_limit = max(1600, int(base_opening_limit * reduction))
        section_limit = max(2600, int(base_section_limit * reduction))
        closing_limit = max(1400, int(base_closing_limit * reduction))
        prepared = build_prepared_context(
            document,
            max_chars=context_limit,
            opening_excerpt_chars=opening_limit,
            section_excerpt_chars=section_limit,
            closing_excerpt_chars=closing_limit,
        )
        prepared = _maybe_enrich_prepared_with_llm_table_metrics(
            prepared,
            settings=settings,
        )
        try:
            use_modular_generation = uses_cloud_model or attempt < 3
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
            if not uses_cloud_model and model_scale < 1.0:
                digest.warnings.append(
                    f"Runtime model {resolved_model} is using a reduced context budget."
                )
            if not uses_cloud_model and attempt > 0:
                digest.warnings.append(
                    "The filing context budget was reduced during retry."
                )
            if not uses_cloud_model and not use_modular_generation:
                digest.warnings.append(
                    "The system switched to a lighter single-pass generation mode."
                )
            digest.warnings = _finalize_user_warnings(digest.warnings)
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
    model_scale = 1.0 if settings.uses_cloud_model else _context_scale_for_model(resolved_model)
    prepared = build_prepared_context(
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
    return _maybe_enrich_prepared_with_llm_table_metrics(prepared, settings=settings)


def _maybe_enrich_prepared_with_llm_table_metrics(
    prepared: PreparedContext,
    *,
    settings: Settings,
) -> PreparedContext:
    if not settings.uses_cloud_model:
        return prepared

    valid_metrics = {record.metric for record in prepared.metric_records if record.valid}
    primary_metrics = {"revenue", "gross_profit", "operating_income", "net_income"}
    important_metrics = primary_metrics | {
        "operating_cash_flow",
        "cash_and_equivalents",
        "capital_expenditures",
        "diluted_eps",
    }
    missing_primary = sorted(primary_metrics - valid_metrics)
    missing_important = sorted(important_metrics - valid_metrics)
    if not missing_primary and len(missing_important) <= 1:
        return prepared

    table_context = _build_llm_table_metric_context(prepared)
    if not table_context:
        return prepared

    try:
        fallback_payload = _extract_metrics_from_tables_with_llm(
            settings=settings,
            reporting_period=prepared.document.reporting_period,
            missing_metrics=missing_important,
            table_context=table_context,
        )
    except Exception:
        return prepared

    recovered_metrics = _merge_llm_metric_fallback(
        prepared,
        payload=fallback_payload,
    )
    if recovered_metrics:
        prepared.warnings = [
            *prepared.warnings,
            f"Recovered missing financial metrics from full statement tables: {', '.join(recovered_metrics[:5])}.",
        ]
    return prepared


def _build_llm_table_metric_context(prepared: PreparedContext) -> str:
    if not prepared.table_chunks:
        raw_item8_text, _ = _extract_item8_region(str(getattr(prepared.document, "text", "") or ""))
        section_blocks: list[str] = []
        if raw_item8_text:
            section_blocks.append(
                f"[SECTION] Item 8 Financial Statements\n{_trim_text(raw_item8_text, 12000)}"
            )
        item7_snippet = str(prepared.section_snippets.get("Item 7 MD&A") or "").strip()
        if item7_snippet:
            section_blocks.append(f"[SECTION] Item 7 MD&A\n{_trim_text(item7_snippet, 5000)}")
        return "\n\n".join(section_blocks)

    grouped = _group_table_chunks(prepared.table_chunks)
    preferred_keywords = (
        "summary results of operations",
        "segment results of operations",
        "statements of income",
        "statements of operations",
        "statements of earnings",
        "balance sheets",
        "cash flows",
    )
    selected_blocks: list[str] = []
    seen: set[str] = set()
    for keyword in preferred_keywords:
        for table_name, payload in grouped.items():
            if keyword not in table_name.lower() or table_name in seen:
                continue
            table_text = str(payload.get("text") or "").strip()
            if not table_text:
                continue
            selected_blocks.append(
                f"[TABLE] {table_name}\n{_trim_text(table_text, 4200)}"
            )
            seen.add(table_name)

    if not selected_blocks:
        for table_name, payload in list(grouped.items())[:4]:
            table_text = str(payload.get("text") or "").strip()
            if not table_text:
                continue
            selected_blocks.append(
                f"[TABLE] {table_name}\n{_trim_text(table_text, 3200)}"
            )

    return "\n\n".join(selected_blocks[:6])


def _extract_metrics_from_tables_with_llm(
    *,
    settings: Settings,
    reporting_period: str,
    missing_metrics: list[str],
    table_context: str,
) -> dict[str, object]:
    client = LocalOpenAIClient(settings)
    system_prompt = """You extract explicit financial statement values from pre-parsed 10-K financial modules.

Rules:
- Read only the provided financial tables or financial statement sections.
- Extract only explicit numeric values that are directly shown in the provided material.
- If a number is shown in a financial statement section but not in a clean table row, you may still extract it if the value is explicit.
- Do not invent numbers.
- Keep units faithful to the table and normalize to:
  - USD_million for money amounts
  - USD_per_share for EPS
- If a metric is unavailable, return null values for it.
- If segment revenue, cost of revenue, or operating expenses are explicitly shown, extract them for profit-flow charting.
- Return JSON only.
"""
    user_prompt = f"""
Reporting period: {reporting_period}

Target metrics to recover if explicitly present:
{json.dumps(missing_metrics, ensure_ascii=False)}

Return JSON with this exact shape:
{{
  "metrics": {{
    "revenue": {{"current_value": null, "previous_value": null, "unit": "USD_million", "source_table_name": "", "evidence_excerpt": ""}},
    "gross_profit": {{"current_value": null, "previous_value": null, "unit": "USD_million", "source_table_name": "", "evidence_excerpt": ""}},
    "operating_income": {{"current_value": null, "previous_value": null, "unit": "USD_million", "source_table_name": "", "evidence_excerpt": ""}},
    "net_income": {{"current_value": null, "previous_value": null, "unit": "USD_million", "source_table_name": "", "evidence_excerpt": ""}},
    "diluted_eps": {{"current_value": null, "previous_value": null, "unit": "USD_per_share", "source_table_name": "", "evidence_excerpt": ""}},
    "operating_cash_flow": {{"current_value": null, "previous_value": null, "unit": "USD_million", "source_table_name": "", "evidence_excerpt": ""}},
    "cash_and_equivalents": {{"current_value": null, "previous_value": null, "unit": "USD_million", "source_table_name": "", "evidence_excerpt": ""}},
    "short_term_investments": {{"current_value": null, "previous_value": null, "unit": "USD_million", "source_table_name": "", "evidence_excerpt": ""}},
    "total_debt": {{"current_value": null, "previous_value": null, "unit": "USD_million", "source_table_name": "", "evidence_excerpt": ""}},
    "capital_expenditures": {{"current_value": null, "previous_value": null, "unit": "USD_million", "source_table_name": "", "evidence_excerpt": ""}}
  }},
  "profit_flow": {{
    "gross_profit": null,
    "cost_of_revenue": null,
    "operating_expenses": null,
    "segments": [
      {{"name": "", "revenue": null}}
    ],
    "source_label": "",
    "evidence_excerpt": ""
  }}
}}

Financial modules:
{table_context}
"""
    return client.chat_json(system_prompt=system_prompt, user_prompt=user_prompt)


def _merge_llm_metric_fallback(
    prepared: PreparedContext,
    *,
    payload: dict[str, object],
) -> list[str]:
    metrics_payload = payload.get("metrics") if isinstance(payload, dict) else None
    if not isinstance(metrics_payload, dict):
        return []

    metric_index = {record.metric: index for index, record in enumerate(prepared.metric_records)}
    recovered: list[str] = []
    report_period = _extract_period_from_text(prepared.document.reporting_period) or prepared.document.reporting_period

    for metric_name, spec in CORE_METRIC_SPECS.items():
        candidate = metrics_payload.get(metric_name)
        if not isinstance(candidate, dict):
            continue

        current_value = candidate.get("current_value")
        if current_value is None:
            continue

        unit = str(candidate.get("unit") or spec.get("unit") or "")
        numeric_value = _coerce_number(current_value)
        validation_errors = _validate_metric_value(
            metric=metric_name,
            metric_type=str(spec["metric_type"]),
            value=numeric_value,
            unit=unit,
        )
        if validation_errors:
            continue

        previous_value = candidate.get("previous_value")
        source_table_name = str(candidate.get("source_table_name") or "LLM table fallback").strip()
        evidence_excerpt = _trim_text(str(candidate.get("evidence_excerpt") or ""), 240)
        record = MetricRecord(
            metric=metric_name,
            metric_type=str(spec["metric_type"]),
            value=numeric_value,
            unit=unit,
            period=str(report_period),
            source=f"llm_table_fallback:{source_table_name}",
            sources=[f"llm_table_fallback:{source_table_name}"],
            canonical_source_chunk_id="",
            canonical_source_table_name=source_table_name,
            explanatory_chunk_ids=[],
            canonical_numeric_source={
                "chunk_id": "",
                "table_name": source_table_name,
                "source": f"llm_table_fallback:{source_table_name}",
                "current_value": numeric_value,
                "previous_value": _coerce_number(previous_value) if previous_value is not None else None,
                "period": str(report_period),
                "evidence": evidence_excerpt,
            },
            explanatory_sources=[],
            confidence="medium",
            valid=True,
            validation_errors=[],
            evidence=evidence_excerpt,
        )

        if metric_name in metric_index:
            existing = prepared.metric_records[metric_index[metric_name]]
            if existing.valid:
                continue
            prepared.metric_records[metric_index[metric_name]] = record
        else:
            prepared.metric_records.append(record)
        recovered.append(metric_name)

    if recovered:
        prepared.financial_snapshot = _build_financial_snapshot(prepared.metric_records)
        valid_records = [record for record in prepared.metric_records if record.valid]
        prepared.financial_metric_map = {
            record.metric: _format_metric_record(record)
            for record in valid_records
        }
        prepared.financial_facts = [
            _format_metric_record(record)
            for record in valid_records
        ][:16]

    profit_flow = payload.get("profit_flow") if isinstance(payload, dict) else None
    if isinstance(profit_flow, dict):
        prepared.investor_summary_layer["llm_profit_flow"] = _normalize_llm_profit_flow(profit_flow)

    return recovered


def _normalize_llm_profit_flow(payload: dict[str, object]) -> dict[str, object]:
    segments = payload.get("segments") if isinstance(payload.get("segments"), list) else []
    normalized_segments: list[dict[str, object]] = []
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        name = str(segment.get("name") or "").strip()
        revenue = _coerce_number(segment.get("revenue"))
        if not name or revenue <= 0:
            continue
        normalized_segments.append({"name": name, "revenue": revenue})

    return {
        "gross_profit": _coerce_number(payload.get("gross_profit")),
        "cost_of_revenue": _coerce_number(payload.get("cost_of_revenue")),
        "operating_expenses": _coerce_number(payload.get("operating_expenses")),
        "segments": normalized_segments,
        "source_label": str(payload.get("source_label") or "LLM financial module extraction").strip(),
        "evidence_excerpt": _trim_text(str(payload.get("evidence_excerpt") or ""), 220),
    }


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
        settings=settings,
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
            settings=settings,
            audience=audience,
            language=language,
            strict_numeric=strict_numeric,
        ),
    )
    text_payload = client.chat_json(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=_build_text_module_prompt(
            prepared,
            settings=settings,
            audience=audience,
            language=language,
            strict_numeric=strict_numeric,
        ),
    )
    synthesis_payload = client.chat_json(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=_build_synthesis_prompt(
            prepared,
            settings=settings,
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
    settings: Settings,
    audience: str,
    language: str,
    strict_numeric: bool = False,
) -> str:
    document = prepared.document
    compact_bundle = _build_compact_summary_bundle(prepared, profile=settings.prompt_profile)
    structured_facts = (
        "\n".join(f"- {_trim_text(fact, 180)}" for fact in prepared.financial_facts[:10])
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
investor_view_markdown
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
- warnings should only mention data-quality limits, comparability issues, or missing coverage
- warnings must not repeat business risks already covered in risks or watchlist
- warnings must be short, user-facing, and at most 3 items
- Do not simply restate fact cards. Explain what changed, why it changed, and why it matters for an ordinary investor.
- If revenue, operating income, net income, or cash flow are available, overview_markdown must explicitly use them in the corresponding sections.
- In the company overview section, describe how the company makes money from products, services, or customer groups, not just mission statements or abstract strategy themes.
- investor_view_markdown should be a short non-personalized investor view with 3 bullets:
  1. Who may want to follow this company
  2. The key condition for a more positive view
  3. The key signal that would make the view more cautious
- Do not output direct buy, sell, or hold instructions.
{extra_constraints}

Structured facts extracted before generation:
{structured_facts}

Filing context:
{json.dumps(compact_bundle, ensure_ascii=False, indent=2)}
"""


def _build_financial_module_prompt(
    prepared: PreparedContext,
    *,
    settings: Settings,
    audience: str,
    language: str,
    strict_numeric: bool,
) -> str:
    profile = settings.prompt_profile
    financial_snapshot = _compact_financial_snapshot(prepared.financial_snapshot)
    key_explanations = _compact_summary_cards(
        prepared.key_explanations,
        limit=10 if profile == "cloud" else 6,
        profile=profile,
    )
    accounting_flags = _compact_summary_cards(
        prepared.accounting_flags,
        limit=6 if profile == "cloud" else 4,
        profile=profile,
    )
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
- warnings should only contain data-quality or comparability caveats, not business risks, and at most 2 items.
- company_overview must state the business model in concrete terms instead of generic strategy language.
- revenue_trend, profit_trend, and cash_flow_summary should explain both the number and the direction/change when a previous-year value exists.
- {extra}

Financial snapshot:
{json.dumps(financial_snapshot, ensure_ascii=False, indent=2) if financial_snapshot else '"Not mentioned in the provided report."'}

Key explanations:
{json.dumps(key_explanations, ensure_ascii=False, indent=2) if key_explanations else '"Not mentioned in the provided report."'}

Accounting flags:
{json.dumps(accounting_flags, ensure_ascii=False, indent=2) if accounting_flags else '"Not mentioned in the provided report."'}

Investor summary layer:
{json.dumps(prepared.investor_summary_layer, ensure_ascii=False, indent=2) if profile == "cloud" and prepared.investor_summary_layer else '"Not mentioned in the provided report."'}
"""


def _build_text_module_prompt(
    prepared: PreparedContext,
    *,
    settings: Settings,
    audience: str,
    language: str,
    strict_numeric: bool,
) -> str:
    profile = settings.prompt_profile
    company_profile = _compact_company_profile(prepared.company_profile, profile=profile)
    key_risks = _compact_risk_cards(
        prepared.key_risks,
        limit=7 if profile == "cloud" else 5,
        profile=profile,
    )
    outlook_signals = _compact_summary_cards(
        prepared.outlook_signals,
        limit=5 if profile == "cloud" else 3,
        profile=profile,
    )
    accounting_flags = _compact_summary_cards(
        prepared.accounting_flags,
        limit=5 if profile == "cloud" else 3,
        profile=profile,
    )
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
- Determine the business model primarily from company_profile.business_summary when it is available.
- Determine how the company makes money primarily from company_profile.monetization_summary when it is available.
- Use company_profile.segment_summary when available to explain which segment drives the business and which segment is still investment-heavy or loss-making.
- Do not infer the company's main business from a single segment label, risk keyword, or accounting note.
- plain_language_summary should stay neutral and educational
- If a topic is missing, write 'Not mentioned in the provided report.'
- warnings should only contain coverage gaps or ambiguity notices, not business risks, and at most 2 items.
- plain_language_summary must explain how the company makes money in everyday language, not just restate a mission statement.
- Do not use abstract labels such as 'platform ecosystem' or 'AI strategy' as the main business description unless the filing clearly ties them to products, customers, or revenue.
- main_risks must focus on business, competitive, regulatory, demand, or execution risks. Do not place accounting comparability topics in main_risks.
- watchlist should focus on operating metrics, segment performance, monetization, capital intensity, and demand indicators. Do not use accounting topics there when they are already covered by warnings.

Company profile:
{json.dumps(company_profile, ensure_ascii=False, indent=2) if company_profile else '"Not mentioned in the provided report."'}

Risk cards:
{json.dumps(key_risks, ensure_ascii=False, indent=2) if key_risks else '"Not mentioned in the provided report."'}

Outlook signals:
{json.dumps(outlook_signals, ensure_ascii=False, indent=2) if outlook_signals else '"Not mentioned in the provided report."'}

Accounting flags:
{json.dumps(accounting_flags, ensure_ascii=False, indent=2) if accounting_flags else '"Not mentioned in the provided report."'}

Investor summary layer:
{json.dumps(prepared.investor_summary_layer, ensure_ascii=False, indent=2) if profile == "cloud" and prepared.investor_summary_layer else '"Not mentioned in the provided report."'}
"""


def _build_synthesis_prompt(
    prepared: PreparedContext,
    *,
    settings: Settings,
    financial_payload: dict,
    text_payload: dict,
    audience: str,
    language: str,
    strict_numeric: bool,
) -> str:
    compact_investor_summary = _build_compact_summary_bundle(
        prepared,
        profile=settings.prompt_profile,
    )
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
investor_view_markdown
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
- investor_view_markdown should be short markdown with exactly 3 bullets and must remain non-personalized.
- overview_markdown should follow this order:
  1. Company overview
  2. Revenue trend
  3. Profit trend
  4. Cash flow
  5. Main risks
  6. Plain-language summary
- warnings should only contain user-facing data limitations or comparability caveats, not repeated risks, and at most 3 items
- Each section should add interpretation, not repeat the same raw number list already shown elsewhere.
- Do not convert accounting caveats into main business risks; keep them in warnings when needed.
- {extra}

Financial module output:
{json.dumps(financial_payload, ensure_ascii=False, indent=2)}

Business/risk module output:
{json.dumps(text_payload, ensure_ascii=False, indent=2)}

Company fallback: {prepared.document.company_name}
Reporting period fallback: {prepared.document.reporting_period}

Investor summary input:
{json.dumps(compact_investor_summary, ensure_ascii=False, indent=2)}
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
        "investor_view_markdown": synthesis_payload.get("investor_view_markdown", ""),
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


def _finalize_user_warnings(warnings: list[str]) -> list[str]:
    curated: list[str] = []
    seen: set[str] = set()
    for warning in warnings:
        rewritten = _rewrite_warning_for_users(warning)
        if not rewritten:
            continue
        normalized = re.sub(r"\s+", " ", rewritten).strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        curated.append(rewritten)
        if len(curated) >= 4:
            break
    return curated


def _rewrite_warning_for_users(warning: object) -> str:
    text = str(warning or "").strip()
    if not text:
        return ""
    lowered = text.lower()

    internal_markers = (
        "reduced context budget",
        "context budget was reduced",
        "single-pass generation mode",
        "regenerated with stricter numeric constraints",
        "retains ",
        "target 5% to 12% range",
        "trimmed to ",
    )
    if any(marker in lowered for marker in internal_markers):
        return ""

    if "missing structured table value" in lowered or "rejected by validation rules" in lowered:
        return "部分自动提取的指标未通过校验，相关趋势判断已做降级处理，建议结合原始财报核对。"
    if "limited numeric coverage" in lowered:
        return "部分关键财务指标覆盖不足，当前总结主要基于已识别的数字片段，不应视为完整财报解读。"
    if "could not confidently extract validated core financial metrics" in lowered:
        return "未能从财务报表中稳定提取全部核心指标，收入和利润趋势解读可能不完整。"
    if "some standard 10-k sections were not detected cleanly" in lowered:
        return "部分标准 10-K 章节未被稳定识别，业务和风险解读可能不完整。"
    if "ambiguity" in lowered or "missing context" in lowered:
        return "部分上下文存在缺口，个别结论可能需要回看原文确认。"

    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > 120:
        text = text[:117].rstrip() + "..."

    if any(
        marker in lowered
        for marker in (
            "not mentioned in the provided report",
            "未提及",
            "未提供",
            "无法分析",
            "可比性",
            "收入确认",
            "递延税",
            "lease",
            "租赁",
            "segment",
            "分部",
        )
    ):
        return text
    return ""


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


def _build_compact_summary_bundle(
    prepared: PreparedContext,
    *,
    profile: str = "local",
) -> dict[str, object]:
    bundle = {
        "company_profile": _compact_company_profile(prepared.company_profile, profile=profile),
        "financial_snapshot": _compact_financial_snapshot(prepared.financial_snapshot),
        "key_explanations": _compact_summary_cards(
            prepared.key_explanations,
            limit=10 if profile == "cloud" else 6,
            profile=profile,
        ),
        "key_risks": _compact_risk_cards(
            prepared.key_risks,
            limit=7 if profile == "cloud" else 5,
            profile=profile,
        ),
        "accounting_flags": _compact_summary_cards(
            prepared.accounting_flags,
            limit=6 if profile == "cloud" else 4,
            profile=profile,
        ),
        "outlook_signals": _compact_summary_cards(
            prepared.outlook_signals,
            limit=5 if profile == "cloud" else 3,
            profile=profile,
        ),
    }
    if profile == "cloud" and prepared.investor_summary_layer:
        bundle["investor_summary_layer"] = prepared.investor_summary_layer
        bundle["structured_financial_facts"] = prepared.financial_facts[:16]
    return bundle

def _compact_company_profile(profile_data: object, *, profile: str = "local") -> dict[str, object]:
    if not profile_data:
        return {}
    payload = asdict(profile_data)
    has_business_summary = bool(str(payload.get("business_summary") or "").strip())
    compact = {}
    for key in [
        "company_name",
        "reporting_period",
        "business_summary",
        "monetization_summary",
        "segment_summary",
        "segments",
        "major_products",
        "manufacturing_regions",
        "strategic_themes",
    ]:
        if key == "strategic_themes" and has_business_summary:
            continue
        value = payload.get(key)
        if isinstance(value, list):
            list_limit = 8 if profile == "cloud" else 6
            compact[key] = [_trim_text(str(item), 72 if profile == "cloud" else 48) for item in value[:list_limit]]
        elif value:
            compact[key] = _trim_text(str(value), 220 if profile == "cloud" else 120)
    return compact


def _compact_financial_snapshot(snapshot: object) -> dict[str, object]:
    if not isinstance(snapshot, dict):
        return {}
    preferred_order = [
        "revenue",
        "gross_profit",
        "operating_income",
        "net_income",
        "gross_margin",
        "operating_margin",
        "diluted_eps",
        "operating_cash_flow",
        "capital_expenditures",
        "free_cash_flow",
        "cash_and_equivalents",
        "short_term_investments",
        "accounts_receivable",
        "inventory",
        "total_debt",
    ]
    compact = {}
    for key in preferred_order:
        value = snapshot.get(key)
        if not isinstance(value, dict):
            continue
        compact_value = {
            field: value.get(field)
            for field in [
                "metric_name",
                "metric_type",
                "value",
                "current_value",
                "previous_value",
                "unit",
                "period",
                "yoy_change_value",
                "yoy_change_pct",
                "canonical_source_table_name",
            ]
            if value.get(field) is not None and value.get(field) != ""
        }
        if compact_value:
            compact[key] = compact_value
    return compact


def _compact_summary_cards(
    cards: list,
    *,
    limit: int,
    profile: str = "local",
) -> list[dict[str, object]]:
    compact = []
    for card in _sort_by_importance(cards)[:limit]:
        payload = asdict(card)
        item = {}
        for key in [
            "topic",
            "summary",
            "importance",
            "linked_metrics",
            "explanation_type",
            "flag_type",
            "why_it_matters",
            "time_horizon",
            "guidance_type",
            "certainty_level",
        ]:
            value = payload.get(key)
            if isinstance(value, list):
                list_limit = 6 if profile == "cloud" else 4
                value = [str(entry).strip() for entry in value[:list_limit] if str(entry).strip()]
            elif isinstance(value, str):
                max_chars = 320 if profile == "cloud" and key == "summary" else 220 if key == "summary" else 180 if profile == "cloud" else 120
                value = _trim_text(value, max_chars)
            if value not in (None, "", []):
                item[key] = value
        if item:
            compact.append(item)
    return compact


def _compact_risk_cards(
    cards: list,
    *,
    limit: int,
    profile: str = "local",
) -> list[dict[str, object]]:
    compact = []
    for card in _sort_by_importance(cards)[:limit]:
        payload = asdict(card)
        item = {}
        for key in ["risk_name", "short_summary", "impact_area", "importance", "severity"]:
            value = payload.get(key)
            if isinstance(value, list):
                list_limit = 6 if profile == "cloud" else 4
                value = [str(entry).strip() for entry in value[:list_limit] if str(entry).strip()]
            elif isinstance(value, str):
                max_chars = 280 if profile == "cloud" and key == "short_summary" else 180 if key == "short_summary" else 120 if profile == "cloud" else 80
                value = _trim_text(value, max_chars)
            if value not in (None, "", []):
                item[key] = value
        if item:
            compact.append(item)
    return compact


def _sort_by_importance(items: list) -> list:
    return sorted(items, key=lambda item: (_importance_rank(getattr(item, "importance", ""))), reverse=True)


def _importance_rank(value: str) -> int:
    text = str(value).strip().lower()
    if text == "high":
        return 3
    if text == "medium":
        return 2
    if text == "low":
        return 1
    return 0


def _trim_text(text: str, max_chars: int) -> str:
    normalized = " ".join(str(text).split())
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 3].rstrip() + "..."


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
    normalized.setdefault("investor_view_markdown", "")
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
        normalized_series = []
        for series in series_list:
            if not isinstance(series, dict):
                continue
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
            normalized_series.append(series)
        series_list = normalized_series
        chart["series"] = series_list

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

    business_risks, accounting_risks = _split_accounting_items(normalized["risks"])
    watchlist_items, accounting_watchlist = _split_accounting_items(normalized["watchlist"])
    normalized["risks"] = business_risks[:5]
    normalized["watchlist"] = watchlist_items[:5]
    normalized["warnings"] = _merge_unique_items(
        [
            *_ensure_string_list(normalized.get("warnings")),
            *accounting_risks,
            *accounting_watchlist,
        ],
        [],
        limit=6,
    )

    normalized["chart_specs"] = _merge_chart_specs(
        _build_programmatic_charts(prepared, language=language),
        cleaned_charts,
        limit=3,
    )
    if _is_placeholder_list(normalized["risks"]) and prepared.key_risks:
        normalized["risks"] = [
            _trim_text(card.short_summary, 120)
            for card in prepared.key_risks[:4]
            if str(card.short_summary).strip()
        ]
    normalized["warnings"] = _filter_contradictory_warnings(
        normalized["warnings"],
        prepared=prepared,
    )
    if not str(normalized.get("investor_view_markdown") or "").strip():
        normalized["investor_view_markdown"] = _build_investor_view_fallback(prepared, language=language)
    normalized["fact_snapshot"] = _build_fact_snapshot(prepared)
    normalized["evidence_cards"] = _build_evidence_cards(prepared)

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


def _is_placeholder_list(items: list[str]) -> bool:
    if not items:
        return True
    placeholders = (
        "未在提供的报告中提及",
        "not mentioned in the provided report",
        "未提及",
    )
    return all(any(marker in item.lower() for marker in placeholders) for item in [str(entry).lower() for entry in items])


def _filter_contradictory_warnings(
    warnings: list[str],
    *,
    prepared: PreparedContext,
) -> list[str]:
    snapshot = prepared.financial_snapshot or {}
    has_revenue = "revenue" in snapshot
    has_profit = any(metric in snapshot for metric in ("gross_profit", "operating_income", "net_income"))
    has_cash_flow = any(metric in snapshot for metric in ("operating_cash_flow", "free_cash_flow"))
    has_risks = bool(prepared.key_risks)
    filtered: list[str] = []
    for warning in warnings:
        lowered = str(warning).lower()
        if has_revenue and "未提供详细财务数据" in str(warning) and "收入" in str(warning):
            continue
        if has_profit and ("利润" in str(warning) or "profit" in lowered) and ("未提供" in str(warning) or "not provided" in lowered):
            continue
        if has_cash_flow and ("现金流" in str(warning) or "cash flow" in lowered) and ("未提供" in str(warning) or "not provided" in lowered):
            continue
        if has_risks and ("风险信息" in str(warning) or "risk information" in lowered) and ("未提供" in str(warning) or "not provided" in lowered):
            continue
        filtered.append(warning)
    return filtered


def _split_accounting_items(items: list[str]) -> tuple[list[str], list[str]]:
    business_items: list[str] = []
    accounting_items: list[str] = []
    markers = (
        "递延税",
        "税项",
        "估值备抵",
        "租赁会计",
        "lease",
        "revenue recognition",
        "收入确认",
        "可比性",
        "accounting",
        "recognition timing",
        "deferred tax",
    )
    for item in items:
        text = str(item).strip()
        if not text:
            continue
        lowered = text.lower()
        if any(marker.lower() in lowered for marker in markers):
            accounting_items.append(text)
        else:
            business_items.append(text)
    return business_items, accounting_items


def _merge_chart_specs(
    primary: list[dict[str, object]],
    secondary: list[dict[str, object]],
    *,
    limit: int,
) -> list[dict[str, object]]:
    merged: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()
    for chart in [*primary, *secondary]:
        if not isinstance(chart, dict):
            continue
        title = str(chart.get("title") or "").strip()
        chart_type = str(chart.get("chart_type") or "").strip()
        if not title:
            continue
        key = (title.lower(), chart_type.lower())
        if key in seen:
            continue
        seen.add(key)
        merged.append(chart)
        if len(merged) >= limit:
            break
    return merged


def _build_investor_view_fallback(
    prepared: PreparedContext,
    *,
    language: str,
) -> str:
    snapshot = prepared.financial_snapshot
    profile = prepared.company_profile
    lang = (language or "").lower()
    is_zh = "zh" in lang or "中文" in lang

    revenue = snapshot.get("revenue") if isinstance(snapshot, dict) else None
    operating_margin = snapshot.get("operating_margin") if isinstance(snapshot, dict) else None
    fcf = snapshot.get("free_cash_flow") if isinstance(snapshot, dict) else None
    debt = snapshot.get("total_debt") if isinstance(snapshot, dict) else None
    cash = snapshot.get("cash_and_equivalents") if isinstance(snapshot, dict) else None
    investments = snapshot.get("short_term_investments") if isinstance(snapshot, dict) else None

    growth_ok = bool(
        isinstance(revenue, dict)
        and _coerce_number(revenue.get("yoy_change_pct")) > 0
        and isinstance(fcf, dict)
        and _coerce_number(fcf.get("yoy_change_pct")) >= 0
    )
    balance_ok = _coerce_number((cash or {}).get("value")) + _coerce_number((investments or {}).get("value")) > _coerce_number((debt or {}).get("value"))
    margin_ok = _coerce_number((operating_margin or {}).get("value")) >= 0.2

    model_text = ""
    if profile:
        model_text = str(
            getattr(profile, "monetization_summary", "")
            or getattr(profile, "business_summary", "")
        ).strip()
    if is_zh:
        lowered_model = model_text.lower()
        if "advertising placements" in lowered_model and "family of apps" in lowered_model:
            model_text = "Family of Apps 广告业务贡献了绝大部分收入，Reality Labs 主要贡献硬件、软件和内容收入且仍处于高投入阶段"
    model_text = _trim_text(model_text, 160)

    if is_zh:
        bullets = [
            f"- 适合关注：更适合关注高盈利能力、现金流质量和平台型商业模式的投资者；当前业务理解应以“{model_text or '主营业务和收入来源'}”为核心。",
            "- 更积极前提：如果收入增长、营业利润率和自由现金流能继续同步改善，说明增长质量仍在提升。",
            "- 更谨慎信号：如果核心变现业务增速放缓、资本开支继续大幅抬升但现金回报转弱，或竞争/会计可比性扰动加重，就需要下调判断。",
        ]
        if not growth_ok:
            bullets[1] = "- 更积极前提：需要先看到收入和现金流恢复到更稳定的增长状态，再谈更积极的投资者视角。"
        if not balance_ok:
            bullets[2] = "- 更谨慎信号：如果债务压力上升、现金缓冲减弱，或资本开支继续抬升但回报不跟上，就需要更谨慎。"
        if not margin_ok:
            bullets[1] = "- 更积极前提：需要先看到利润率和现金流重新改善，才能支持更积极的投资者视角。"
        return "\n".join(bullets)

    fallback_model_text = model_text or "the company main revenue engine"
    bullets = [
        f"- Best suited for: investors tracking scalable platform businesses with strong profitability and cash generation; the core business model is '{fallback_model_text}'.",
        "- More constructive if: revenue growth, operating margin, and free cash flow continue improving together.",
        "- More cautious if: the core monetization engine slows, capex keeps rising without matching cash returns, or accounting comparability issues become more important.",
    ]
    return "\n".join(bullets)


def _build_programmatic_charts(
    prepared: PreparedContext,
    *,
    language: str,
) -> list[dict[str, object]]:
    snapshot = prepared.financial_snapshot
    profile = prepared.company_profile
    if not isinstance(snapshot, dict):
        return []

    charts: list[dict[str, object]] = []
    lang = (language or "").lower()
    is_zh = "zh" in lang or "中文" in lang

    def label(zh: str, en: str) -> str:
        return zh if is_zh else en

    palette = ["#2563eb", "#0f766e", "#ea580c", "#475569", "#16a34a"]
    metrics = {
        "revenue": snapshot.get("revenue"),
        "operating_income": snapshot.get("operating_income"),
        "net_income": snapshot.get("net_income"),
        "operating_cash_flow": snapshot.get("operating_cash_flow"),
        "capital_expenditures": snapshot.get("capital_expenditures"),
        "free_cash_flow": snapshot.get("free_cash_flow"),
        "cash_and_equivalents": snapshot.get("cash_and_equivalents"),
        "short_term_investments": snapshot.get("short_term_investments"),
        "total_debt": snapshot.get("total_debt"),
    }

    previous_period, current_period = _period_labels_from_snapshot(snapshot, fallback=prepared.document.reporting_period)

    trend_metrics = [
        ("revenue", label("营业收入", "Revenue")),
        ("operating_income", label("营业利润", "Operating income")),
        ("net_income", label("净利润", "Net income")),
    ]
    trend_series = []
    for key, series_name in trend_metrics:
        metric = metrics.get(key)
        if not isinstance(metric, dict):
            continue
        current_value = metric.get("value")
        previous_value = metric.get("previous_value")
        if current_value is None or previous_value is None:
            continue
        trend_series.append(
            {
                "name": series_name,
                "unit": "USD_million",
                "values": [_coerce_number(previous_value), _coerce_number(current_value)],
            }
        )
    if len(trend_series) >= 2:
        charts.append(
            {
                "title": label("收入与利润两年对比", "Revenue and profit: two-year comparison"),
                "chart_type": "line",
                "why_it_matters": label(
                    "先看营收，再看营业利润和净利润，能更快判断增长是否真的转化成利润。",
                    "Comparing revenue, operating income, and net income shows whether growth is translating into profit.",
                ),
                "x_axis_label": label("财年", "Fiscal year"),
                "categories": [previous_period, current_period],
                "series": trend_series,
                "palette": palette[: len(trend_series)],
                "source_snippet": _chart_source_from_metrics(
                    snapshot,
                    ("revenue", "operating_income", "net_income"),
                ),
                "confidence": "high",
            }
        )

    cash_metrics = [
        ("operating_cash_flow", label("经营现金流", "Operating cash flow")),
        ("capital_expenditures", label("资本开支", "Capital expenditures")),
        ("free_cash_flow", label("自由现金流", "Free cash flow")),
    ]
    cash_categories = []
    cash_values = []
    for key, category in cash_metrics:
        metric = metrics.get(key)
        if not isinstance(metric, dict) or metric.get("value") is None:
            continue
        cash_categories.append(category)
        cash_values.append(_coerce_number(metric.get("value")))
    if len(cash_categories) >= 2:
        charts.append(
            {
                "title": label("现金流结构", "Cash generation profile"),
                "chart_type": "bar",
                "why_it_matters": label(
                    "经营现金流、资本开支和自由现金流放在一起，更容易看出公司是在强力造血，还是主要靠压缩投资来维持现金流。",
                    "Showing operating cash flow, capex, and free cash flow together helps investors judge cash generation quality.",
                ),
                "x_axis_label": label("指标", "Metric"),
                "categories": cash_categories,
                "series": [
                    {
                        "name": current_period,
                        "unit": "USD_million",
                        "values": cash_values,
                    }
                ],
                "palette": ["#0f766e"],
                "source_snippet": _chart_source_from_metrics(
                    snapshot,
                    ("operating_cash_flow", "capital_expenditures", "free_cash_flow"),
                ),
                "confidence": "high",
            }
        )

    revenue_metric = metrics.get("revenue")
    gross_profit_metric = snapshot.get("gross_profit")
    op_income_metric = metrics.get("operating_income")
    net_income_metric = metrics.get("net_income")
    profit_flow_chart = _build_profit_flow_sankey(
        prepared,
        language=language,
        revenue_metric=revenue_metric,
        gross_profit_metric=gross_profit_metric,
        operating_income_metric=op_income_metric,
        net_income_metric=net_income_metric,
    )
    if profit_flow_chart:
        charts.append(profit_flow_chart)
    else:
        segment_chart = _build_segment_allocation_chart(profile, language=language)
        if segment_chart:
            charts.append(segment_chart)

    liquidity_categories = []
    liquidity_values = []
    liquidity_keys = [
        ("cash_and_equivalents", label("现金及等价物", "Cash and equivalents")),
        ("short_term_investments", label("短期投资", "Short-term investments")),
        ("total_debt", label("总债务", "Total debt")),
    ]
    for key, category in liquidity_keys:
        metric = metrics.get(key)
        if not isinstance(metric, dict) or metric.get("value") is None:
            continue
        liquidity_categories.append(category)
        liquidity_values.append(_coerce_number(metric.get("value")))
    if len(liquidity_categories) >= 2:
        charts.append(
            {
                "title": label("流动性与债务", "Liquidity and debt position"),
                "chart_type": "bar",
                "why_it_matters": label(
                    "把现金、短期投资和债务放在一起，有助于判断资产负债表的缓冲能力。",
                    "Comparing cash, short-term investments, and debt helps investors assess balance-sheet flexibility.",
                ),
                "x_axis_label": label("指标", "Metric"),
                "categories": liquidity_categories,
                "series": [
                    {
                        "name": current_period,
                        "unit": "USD_million",
                        "values": liquidity_values,
                    }
                ],
                "palette": ["#475569"],
                "source_snippet": _chart_source_from_metrics(
                    snapshot,
                    ("cash_and_equivalents", "short_term_investments", "total_debt"),
                ),
                "confidence": "high",
            }
        )

    return charts


def _build_profit_flow_sankey(
    prepared: PreparedContext,
    *,
    language: str,
    revenue_metric: object,
    gross_profit_metric: object,
    operating_income_metric: object,
    net_income_metric: object,
) -> dict[str, object] | None:
    if not all(
        isinstance(metric, dict) and metric.get("value") is not None
        for metric in (revenue_metric, operating_income_metric, net_income_metric)
    ):
        return None

    lang = (language or "").lower()
    is_zh = "zh" in lang or "中文" in lang

    def label(zh: str, en: str) -> str:
        return zh if is_zh else en

    revenue_value = _coerce_number(revenue_metric.get("value"))
    gross_profit_value = _coerce_number((gross_profit_metric or {}).get("value"))
    operating_income_value = _coerce_number(operating_income_metric.get("value"))
    net_income_value = _coerce_number(net_income_metric.get("value"))

    segment_flow = _extract_segment_income_breakdown(prepared)
    if not segment_flow:
        llm_profit_flow = prepared.investor_summary_layer.get("llm_profit_flow")
        if isinstance(llm_profit_flow, dict):
            segment_flow = llm_profit_flow
    segment_nodes = segment_flow.get("segments", []) if segment_flow else []
    cost_of_revenue_value = _coerce_number(segment_flow.get("cost_of_revenue")) if segment_flow else 0.0
    operating_expenses_value = _coerce_number(segment_flow.get("operating_expenses")) if segment_flow else 0.0

    ixbrl_totals = _extract_ixbrl_profit_flow_totals(
        prepared.document.selected_file,
        prepared.document.reporting_period,
    )
    if ixbrl_totals:
        if cost_of_revenue_value <= 0:
            cost_of_revenue_value = _coerce_number(ixbrl_totals.get("cost_of_revenue"))
        if operating_expenses_value <= 0:
            direct_opex = _coerce_number(ixbrl_totals.get("operating_expenses"))
            if direct_opex > 0:
                operating_expenses_value = direct_opex
            else:
                total_costs = _coerce_number(ixbrl_totals.get("costs_and_expenses"))
                if total_costs > 0 and cost_of_revenue_value > 0:
                    operating_expenses_value = max(total_costs - cost_of_revenue_value, 0.0)

    if gross_profit_value <= 0 and segment_flow:
        gross_profit_value = _coerce_number(segment_flow.get("gross_profit"))
    if gross_profit_value <= 0 and revenue_value > 0:
        gross_profit_value = max(revenue_value - cost_of_revenue_value, 0.0)
    if cost_of_revenue_value <= 0 and revenue_value > 0 and gross_profit_value > 0:
        cost_of_revenue_value = max(revenue_value - gross_profit_value, 0.0)
    if operating_expenses_value <= 0 and gross_profit_value > 0:
        operating_expenses_value = max(gross_profit_value - operating_income_value, 0.0)

    if gross_profit_value <= 0:
        gross_profit_value = max(revenue_value - cost_of_revenue_value, operating_income_value)
    if operating_expenses_value <= 0 and gross_profit_value > 0:
        operating_expenses_value = max(gross_profit_value - operating_income_value, 0.0)

    below_operating_value = max(operating_income_value - net_income_value, 0.0)

    if (
        revenue_value <= 0
        or gross_profit_value <= 0
        or operating_income_value <= 0
        or net_income_value <= 0
        or len(segment_nodes) < 2
    ):
        return None

    total_revenue_name = label("总收入", "Total revenue")
    gross_profit_name = label("毛利润", "Gross profit")
    operating_income_name = label("营业利润", "Operating income")
    net_income_name = label("净利润", "Net income")
    cost_of_revenue_name = label("营业成本", "Cost of revenue")
    operating_expenses_name = label("营业费用", "Operating expenses")
    below_operating_name = label("税项及其他调整", "Taxes and other adjustments")

    flow_nodes: list[dict[str, object]] = []
    flow_links: list[dict[str, object]] = []

    for entry in segment_nodes:
        name = str(entry.get("name") or "").strip()
        value = _coerce_number(entry.get("revenue"))
        if not name or value <= 0:
            continue
        flow_nodes.append(
            {
                "name": _shorten_sankey_label(name),
                "value": value,
                "item_style_color": "#111111",
                "depth": 0,
                "layout_order": len(flow_nodes),
            }
        )
        flow_links.append(
            {
                "source": _shorten_sankey_label(name),
                "target": total_revenue_name,
                "value": value,
            }
        )

    flow_nodes.extend(
        [
            {
                "name": total_revenue_name,
                "value": revenue_value,
                "item_style_color": "#111111",
                "depth": 1,
                "layout_order": 0,
            },
            {
                "name": gross_profit_name,
                "value": gross_profit_value,
                "item_style_color": "#22c55e",
                "depth": 2,
                "layout_order": 0,
            },
            {
                "name": cost_of_revenue_name,
                "value": cost_of_revenue_value,
                "item_style_color": "#dc2626",
                "depth": 2,
                "layout_order": 1,
            },
            {
                "name": operating_income_name,
                "value": operating_income_value,
                "item_style_color": "#16a34a",
                "depth": 3,
                "layout_order": 0,
            },
            {
                "name": operating_expenses_name,
                "value": operating_expenses_value,
                "item_style_color": "#ef4444",
                "depth": 3,
                "layout_order": 1,
            },
            {
                "name": net_income_name,
                "value": net_income_value,
                "item_style_color": "#0f766e",
                "depth": 4,
                "layout_order": 0,
            },
            {
                "name": below_operating_name,
                "value": below_operating_value,
                "item_style_color": "#f97316",
                "depth": 4,
                "layout_order": 1,
            },
        ]
    )

    flow_links.extend(
        [
            {"source": total_revenue_name, "target": cost_of_revenue_name, "value": cost_of_revenue_value},
            {"source": total_revenue_name, "target": gross_profit_name, "value": gross_profit_value},
            {"source": gross_profit_name, "target": operating_expenses_name, "value": operating_expenses_value},
            {"source": gross_profit_name, "target": operating_income_name, "value": operating_income_value},
            {"source": operating_income_name, "target": net_income_name, "value": net_income_value},
        ]
    )
    if below_operating_value > 0:
        flow_links.append(
            {
                "source": operating_income_name,
                "target": below_operating_name,
                "value": below_operating_value,
            }
        )

    source_snippet = (
        segment_flow.get("source_label")
        if isinstance(segment_flow, dict) and segment_flow.get("source_label")
        else _chart_source_from_metrics(
            prepared.financial_snapshot,
            ("revenue", "gross_profit", "operating_income", "net_income"),
        )
    )
    if source_snippet:
        source_snippet = (
            f"{source_snippet} · 单位：百万美元"
            if is_zh
            else f"{source_snippet} · Unit: USD million"
        )

    title = label("营收流向分析", "Revenue-to-profit flow")
    period = str(prepared.document.reporting_period or "").strip()
    year_match = re.search(r"(20\d{2})", period)
    if year_match:
        title = f"{title}（{year_match.group(1)}年）" if is_zh else f"{title} ({year_match.group(1)})"

    return {
        "title": title,
        "chart_type": "sankey",
        "why_it_matters": label(
            "左侧先看主要收入来源，中间看总收入如何变成毛利和营业利润，右侧再看税项及其他因素如何影响净利润。",
            "Start with the main revenue contributors on the left, then see how total revenue becomes gross profit and operating income before taxes and other items shape net income.",
        ),
        "x_axis_label": "",
        "categories": [],
        "series": [],
        "flow_nodes": flow_nodes,
        "flow_links": flow_links,
        "palette": ["#111111", "#22c55e", "#16a34a", "#0f766e", "#ef4444", "#f97316"],
        "source_snippet": source_snippet,
        "confidence": "high" if segment_nodes else "medium",
    }


def _extract_segment_income_breakdown(prepared: PreparedContext) -> dict[str, object] | None:
    snapshot = prepared.financial_snapshot if isinstance(prepared.financial_snapshot, dict) else {}
    total_revenue = _coerce_number((snapshot.get("revenue") or {}).get("value"))
    ixbrl_composition = _extract_ixbrl_revenue_composition(
        prepared.document.selected_file,
        prepared.document.reporting_period,
        total_revenue,
    )

    text = str(getattr(prepared.document, "text", "") or "")
    if not text:
        return ixbrl_composition

    lower_text = text.lower()
    anchor_markers = (
        "segment results of operations",
        "segment revenue, cost of revenue, operating expenses, and operating income were as follows",
    )
    start = -1
    for marker in anchor_markers:
        start = lower_text.find(marker)
        if start >= 0:
            break
    if start < 0:
        return ixbrl_composition

    window = text[start : start + 6000]
    for marker in ("Reportable Segments", "Fiscal Year", "Revenue Recognition"):
        marker_index = window.find(marker)
        if marker_index > 0:
            window = window[:marker_index]
            break

    pattern = re.compile(
        r"(?P<name>[A-Z][A-Za-z&/(),\- ]{2,80}?)\s+"
        r"Revenue\s+\$?\s*(?P<revenue>[\d,]+)\s+\$?\s*[\d,]+\s+\d+%\s+"
        r"Cost of revenue\s+(?P<cost>[\d,]+)\s+[\d,]+\s+\d+%\s+"
        r"Operating expenses\s+(?P<opex>[\d,]+)\s+[\d,]+\s+\d+%\s+"
        r"Operating Income\s+\$?\s*(?P<op_income>[\d,]+)",
        re.IGNORECASE | re.DOTALL,
    )

    segments: list[dict[str, object]] = []
    totals: dict[str, float] = {}
    for match in pattern.finditer(window):
        name = " ".join(str(match.group("name") or "").split()).strip(" .")
        if not name or "percentage change" in name.lower():
            continue
        record = {
            "name": name,
            "revenue": _coerce_number(match.group("revenue")),
            "cost_of_revenue": _coerce_number(match.group("cost")),
            "operating_expenses": _coerce_number(match.group("opex")),
            "operating_income": _coerce_number(match.group("op_income")),
        }
        if name.lower() == "total":
            totals = record
        else:
            segments.append(record)

    if not segments and not totals:
        return ixbrl_composition

    if not totals and segments:
        totals = {
            "name": "Total",
            "revenue": sum(_coerce_number(entry.get("revenue")) for entry in segments),
            "cost_of_revenue": sum(_coerce_number(entry.get("cost_of_revenue")) for entry in segments),
            "operating_expenses": sum(_coerce_number(entry.get("operating_expenses")) for entry in segments),
            "operating_income": sum(_coerce_number(entry.get("operating_income")) for entry in segments),
        }

    result = {
        "segments": segments,
        "revenue": _coerce_number(totals.get("revenue")),
        "cost_of_revenue": _coerce_number(totals.get("cost_of_revenue")),
        "operating_expenses": _coerce_number(totals.get("operating_expenses")),
        "operating_income": _coerce_number(totals.get("operating_income")),
        "source_label": _trim_text(window.splitlines()[0] if window else "SEGMENT RESULTS OF OPERATIONS", 120),
    }
    if ixbrl_composition and len(result.get("segments") or []) < 2:
        return ixbrl_composition
    return result


def _shorten_sankey_label(name: str) -> str:
    cleaned = " ".join(str(name or "").split()).strip()
    replacements = {
        "Productivity and Business Processes": "Productivity & Business",
        "More Personal Computing": "Personal Computing",
        "Family of Apps": "Family of Apps",
        "Reality Labs": "Reality Labs",
    }
    if cleaned in replacements:
        return replacements[cleaned]
    return cleaned


def _build_segment_allocation_chart(
    profile: object,
    *,
    language: str,
) -> dict[str, object] | None:
    if not profile:
        return None
    summary = str(getattr(profile, "segment_summary", "") or "").strip()
    if not summary:
        return None

    lang = (language or "").lower()
    is_zh = "zh" in lang or "中文" in lang

    def label(zh: str, en: str) -> str:
        return zh if is_zh else en

    def normalize_segment_name(name: str) -> str:
        mapping = {
            "foa": "Family of Apps",
            "rl": "Reality Labs",
        }
        cleaned = str(name or "").strip()
        return mapping.get(cleaned.lower(), cleaned.title())

    lower_summary = summary.lower()
    cost_match = re.search(
        r"(\d{1,3})%\s+of our total costs and expenses were recognized in\s+([a-z][a-z ]+?)\s+and\s+(\d{1,3})%\s+were recognized in\s+([a-z][a-z ]+)",
        lower_summary,
    )
    invest_match = re.search(
        r"our\s+([a-z][a-z ]+?)\s+investments were\s+\$([\d.]+)\s*billion.*?our total\s+([a-z][a-z ]+?)\s+investments were\s+\$([\d.]+)\s*billion",
        lower_summary,
        re.DOTALL,
    )

    categories: list[str] = []
    values: list[float] = []
    title = ""
    why = ""
    unit = ""

    if invest_match:
        categories = [
            normalize_segment_name(invest_match.group(1)),
            normalize_segment_name(invest_match.group(3)),
        ]
        values = [float(invest_match.group(2)) * 1000, float(invest_match.group(4)) * 1000]
        title = label("分部投入结构", "Segment investment mix")
        why = label(
            "这张图展示公司把投入主要放在哪些业务上，有助于判断利润来源和亏损拖累分别来自哪里。",
            "This chart shows which segments absorb most of the company's investment and where profits or losses may be concentrated.",
        )
        unit = "USD_million"
    elif cost_match:
        categories = [
            normalize_segment_name(cost_match.group(2)),
            normalize_segment_name(cost_match.group(4)),
        ]
        values = [float(cost_match.group(1)), float(cost_match.group(3))]
        title = label("分部成本占比", "Segment cost mix")
        why = label(
            "如果公司按分部披露成本占比，这能帮助普通投资者看出核心业务和高投入业务的资源分配差异。",
            "When a filing discloses cost mix by segment, investors can see which segment is core and which is investment-heavy.",
        )
        unit = "percent"
    else:
        return None

    return {
        "title": title,
        "chart_type": "bar",
        "why_it_matters": why,
        "x_axis_label": label("分部", "Segment"),
        "categories": categories,
        "series": [
            {
                "name": label("当前披露值", "Reported value"),
                "unit": unit,
                "values": values,
            }
        ],
        "palette": ["#d97706"],
        "source_snippet": _trim_text(summary, 180),
        "confidence": "medium",
    }


def _period_labels_from_snapshot(
    snapshot: dict[str, object],
    *,
    fallback: str,
) -> tuple[str, str]:
    for metric in snapshot.values():
        if not isinstance(metric, dict):
            continue
        period = str(metric.get("period") or "").strip()
        if not period:
            continue
        if period.isdigit():
            return str(int(period) - 1), period
        return "Prior period", period
    fallback_text = str(fallback or "").strip()
    return "Prior period", fallback_text or "Current period"


def _chart_source_from_metrics(
    snapshot: dict[str, object],
    metric_keys: tuple[str, ...],
) -> str:
    snippets: list[str] = []
    for key in metric_keys:
        metric = snapshot.get(key)
        if not isinstance(metric, dict):
            continue
        table_name = str(metric.get("canonical_source_table_name") or "").strip()
        if table_name and table_name not in snippets:
            snippets.append(table_name)
    return " | ".join(snippets[:3])


def _build_fact_snapshot(prepared: PreparedContext) -> list[dict[str, object]]:
    metrics_order = [
        "revenue",
        "gross_profit",
        "operating_income",
        "net_income",
        "gross_margin",
        "operating_margin",
        "diluted_eps",
        "operating_cash_flow",
        "free_cash_flow",
        "cash_and_equivalents",
    ]
    labels = {
        "revenue": "营业收入",
        "gross_profit": "毛利润",
        "operating_income": "营业利润",
        "net_income": "净利润",
        "gross_margin": "毛利率",
        "operating_margin": "营业利润率",
        "diluted_eps": "摊薄每股收益",
        "operating_cash_flow": "经营现金流",
        "free_cash_flow": "自由现金流",
        "cash_and_equivalents": "现金及等价物",
    }
    chunk_lookup = _build_chunk_lookup(prepared)
    confidence_map = {record.metric: record.confidence for record in prepared.metric_records if record.valid}
    items: list[dict[str, object]] = []

    for key in metrics_order:
        metric = prepared.financial_snapshot.get(key)
        if not isinstance(metric, dict) or metric.get("value") is None:
            continue
        chunk = chunk_lookup.get(str(metric.get("canonical_source_chunk_id") or ""))
        items.append(
            {
                "metric_key": key,
                "label": labels.get(key, key.replace("_", " ").title()),
                "value_text": _format_metric_value(metric.get("value"), str(metric.get("unit") or "")),
                "yoy_text": _format_yoy_text(metric),
                "source_label": str(metric.get("canonical_source_table_name") or _chunk_label(chunk)),
                "source_snippet": _chunk_snippet(chunk),
                "confidence": _normalize_confidence(confidence_map.get(key, "medium")),
            }
        )
    return items


def _build_evidence_cards(prepared: PreparedContext) -> list[dict[str, object]]:
    chunk_lookup = _build_chunk_lookup(prepared)
    cards: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()

    def append_card(card_payload: dict[str, object]) -> None:
        identity = (str(card_payload.get("category", "")), str(card_payload.get("title", "")))
        if identity in seen:
            return
        seen.add(identity)
        cards.append(card_payload)

    for card in _sort_by_importance(prepared.key_explanations)[:4]:
        chunk = chunk_lookup.get(card.source_chunk_id)
        append_card(
            {
                "title": _humanize_card_title(card.topic),
                "category": "explanation",
                "summary": _trim_text(card.summary, 220),
                "source_label": _chunk_label(chunk),
                "source_snippet": _chunk_snippet(chunk),
                "related_metrics": card.linked_metrics[:4],
                "importance": card.importance,
                "why_it_matters": _trim_text(card.why_it_matters, 140),
            }
        )

    for card in _sort_by_importance(prepared.key_risks)[:4]:
        chunk = chunk_lookup.get(card.source_chunk_id)
        append_card(
            {
                "title": _trim_text(card.risk_name, 80),
                "category": "risk",
                "summary": _trim_text(card.short_summary, 220),
                "source_label": _chunk_label(chunk),
                "source_snippet": _chunk_snippet(chunk),
                "related_metrics": card.impact_area[:4],
                "importance": card.severity or card.importance,
                "why_it_matters": _risk_why_it_matters(card),
            }
        )

    for card in _sort_by_importance(prepared.accounting_flags)[:3]:
        chunk = chunk_lookup.get(card.source_chunk_id)
        append_card(
            {
                "title": _humanize_card_title(card.topic),
                "category": "accounting",
                "summary": _trim_text(card.summary, 220),
                "source_label": _chunk_label(chunk),
                "source_snippet": _chunk_snippet(chunk),
                "related_metrics": card.linked_metrics[:4],
                "importance": card.importance,
                "why_it_matters": _trim_text(card.why_it_matters, 140),
            }
        )

    for card in _sort_by_importance(prepared.outlook_signals)[:2]:
        chunk = chunk_lookup.get(card.source_chunk_id)
        append_card(
            {
                "title": _humanize_card_title(card.topic),
                "category": "outlook",
                "summary": _trim_text(card.summary, 220),
                "source_label": _chunk_label(chunk),
                "source_snippet": _chunk_snippet(chunk),
                "related_metrics": card.linked_metrics[:4],
                "importance": card.importance,
                "why_it_matters": _trim_text(card.why_it_matters, 140),
            }
        )

    return cards


def _build_chunk_lookup(prepared: PreparedContext) -> dict[str, object]:
    chunks = [*prepared.narrative_chunks, *prepared.table_chunks, *prepared.note_chunks]
    return {chunk.chunk_id: chunk for chunk in chunks if getattr(chunk, "chunk_id", "")}


def _chunk_label(chunk: object) -> str:
    if not chunk:
        return ""
    subsection = str(getattr(chunk, "subsection_title", "") or "").strip()
    section_path = str(getattr(chunk, "section_path", "") or "").strip()
    item_number = str(getattr(chunk, "item_number", "") or "").strip()
    if subsection and section_path:
        return f"{section_path} / {subsection}"
    if section_path:
        return section_path
    if subsection:
        return subsection
    return item_number


def _chunk_snippet(chunk: object) -> str:
    if not chunk:
        return ""
    text = str(getattr(chunk, "text", "") or "").strip()
    if not text:
        return ""
    return _trim_text(text, 220)


def _format_metric_value(value: object, unit: str) -> str:
    number = _coerce_number(value)
    if unit == "ratio":
        return f"{number * 100:.1f}%"
    if unit == "USD_per_share":
        return f"${number:.2f}"
    if unit == "USD_million":
        if abs(number) >= 1000:
            return f"${number / 1000:.1f}B"
        return f"${number:.0f}M"
    if number.is_integer():
        return f"{int(number):,}"
    return f"{number:,.2f}"


def _format_yoy_text(metric: dict[str, object]) -> str:
    change_pct = metric.get("yoy_change_pct")
    change_value = metric.get("yoy_change_value")
    unit = str(metric.get("unit") or "")
    if change_pct is None and change_value is None:
        return ""
    pieces: list[str] = []
    if change_pct is not None:
        pct = _coerce_number(change_pct)
        pieces.append(f"同比 {pct:+.1f}%")
    if change_value is not None and unit in {"USD_million", "USD_per_share"}:
        pieces.append(f"变化 {_format_metric_value(change_value, unit)}")
    return " · ".join(pieces)


def _humanize_card_title(topic: str) -> str:
    text = str(topic or "").replace("_", " ").strip()
    return text.title() if text else "Source note"


def _risk_why_it_matters(card: object) -> str:
    impact_area = getattr(card, "impact_area", []) or []
    if not impact_area:
        return ""
    readable = "、".join(str(item) for item in impact_area[:4] if str(item).strip())
    return f"Potential impact areas: {readable}."
    def normalize_segment_name(name: str) -> str:
        mapping = {
            "foa": "Family of Apps",
            "rl": "Reality Labs",
        }
        cleaned = str(name or "").strip()
        return mapping.get(cleaned.lower(), cleaned.title())
