from __future__ import annotations

import html
import json
import re
from datetime import date, datetime
from itertools import combinations
from pathlib import Path

from bs4 import BeautifulSoup

from investor_digest.schemas import (
    CompanyProfile,
    FilingChunk,
    MetricRecord,
    ParsedDocument,
    PreparedContext,
    RiskCard,
    SummaryCard,
)


SEC_CANDIDATE_NAMES = (
    "primary-document.html",
    "primary_doc.html",
    "full-submission.txt",
)

SECTION_PATTERNS = (
    ("Item 1 Business", re.compile(r"\bitem\s+1\.?\s+business(?:\b|(?=[A-Z]))", re.IGNORECASE)),
    (
        "Item 1A Risk Factors",
        re.compile(r"\bitem\s+1a\.?\s+risk factors\b", re.IGNORECASE),
    ),
    (
        "Item 7 MD&A",
        re.compile(
            r"\bitem\s+7\.?\s+management(?:['’]s)?\s+discussion\s+and\s+analysis\b",
            re.IGNORECASE,
        ),
    ),
    (
        "Item 7A Market Risk",
        re.compile(
            r"\bitem\s+7a\.?\s+quantitative\s+and\s+qualitative\s+disclosures",
            re.IGNORECASE,
        ),
    ),
    (
        "Item 8 Financial Statements",
        re.compile(r"\bitem\s+8\.?\s+financial statements(?:\b|(?=[A-Z]))", re.IGNORECASE),
    ),
)

METRIC_PATTERNS = (
    (
        "Net sales by reportable segment",
        re.compile(r"net sales by reportable segment", re.IGNORECASE),
    ),
    (
        "Products and Services Performance",
        re.compile(r"products and services performance", re.IGNORECASE),
    ),
    (
        "Net sales by category",
        re.compile(r"net sales by category", re.IGNORECASE),
    ),
    (
        "Consolidated Statements of Operations",
        re.compile(r"consolidated statements of operations", re.IGNORECASE),
    ),
)

FINANCIAL_FACT_PATTERNS = (
    (
        "Revenue",
        re.compile(r"\b(?:total\s+net\s+sales|total\s+revenues?|net\s+sales|net\s+revenues?|revenues?)\b", re.IGNORECASE),
    ),
    (
        "Gross profit",
        re.compile(r"\bgross (?:profit|margin)\b", re.IGNORECASE),
    ),
    (
        "Operating income",
        re.compile(r"\b(?:operating income|income from operations)\b", re.IGNORECASE),
    ),
    (
        "Net income",
        re.compile(r"\b(?:net income|net earnings)\b", re.IGNORECASE),
    ),
    (
        "Diluted EPS",
        re.compile(
            r"\b(?:diluted (?:earnings|net income) per share|diluted eps|earnings per share diluted)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "Operating cash flow",
        re.compile(
            r"\b(?:operating cash flow|cash flows? from operating activities|net cash provided by operating activities)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "Free cash flow",
        re.compile(r"\bfree cash flow\b", re.IGNORECASE),
    ),
    (
        "Cash and equivalents",
        re.compile(
            r"\b(?:cash and cash equivalents|cash, cash equivalents and investments)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "Capital expenditures",
        re.compile(
            r"\b(?:capital expenditures|purchases of property and equipment)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "Deliveries or production",
        re.compile(
            r"\b(?:vehicle deliveries|deliveries|production|produced|deliver(?:ed|ies))\b",
            re.IGNORECASE,
        ),
    ),
    (
        "Energy storage",
        re.compile(
            r"\b(?:energy generation and storage|energy storage deployments?|megawatt hours deployed|megapack)\b",
            re.IGNORECASE,
        ),
    ),
)

ITEM7_SUBTOPICS = (
    ("overview", ("overview", "highlights", "mission", "strategy", "business model")),
    ("financial_summary", ("revenue", "gross profit", "operating income", "net income", "margin")),
    ("operations", ("operations", "production", "capacity", "deployment", "manufacturing", "supply chain")),
    ("demand", ("demand", "orders", "pricing", "market", "customers")),
    ("cashflow_capex", ("cash flow", "capital expenditures", "capex", "liquidity", "cash and cash equivalents")),
    ("outlook", ("expect", "we plan", "outlook", "future", "guidance", "roadmap")),
)

TABLE_HEADING_PATTERNS = (
    re.compile(r"consolidated balance sheets", re.IGNORECASE),
    re.compile(r"consolidated statements of income", re.IGNORECASE),
    re.compile(r"consolidated statements of operations", re.IGNORECASE),
    re.compile(r"consolidated statements of earnings", re.IGNORECASE),
    re.compile(r"consolidated statements of income and comprehensive income", re.IGNORECASE),
    re.compile(r"consolidated statements of cash flows", re.IGNORECASE),
    re.compile(r"consolidated statements of comprehensive income", re.IGNORECASE),
    re.compile(
        r"consolidated statements of redeemable noncontrolling interests and equity",
        re.IGNORECASE,
    ),
    re.compile(r"notes to consolidated financial statements", re.IGNORECASE),
    re.compile(r"products and services performance", re.IGNORECASE),
    re.compile(r"net sales by reportable segment", re.IGNORECASE),
    re.compile(r"net sales by category", re.IGNORECASE),
)

FALLBACK_TABLE_HEADING_PATTERNS = (
    re.compile(r"summary results of operations", re.IGNORECASE),
    re.compile(r"segment results of operations", re.IGNORECASE),
    re.compile(r"cash flows?\s+s?\s*tatements?", re.IGNORECASE),
)

NOTE_HEADING_INLINE = re.compile(
    r"(?mi)^Note\s+(\d+[A-Z]?)\s*[–\-.:,]?\s*(.+)?$"
)

TABLE_METRIC_PATTERNS = {
    "revenue": re.compile(r"(?:total\s+)?revenues?\s+\$?\s*\(?([\d,]+(?:\.\d+)?)\)?", re.IGNORECASE),
    "gross_profit": re.compile(r"gross profit\s+\$?\s*\(?([\d,]+(?:\.\d+)?)\)?", re.IGNORECASE),
    "operating_income": re.compile(
        r"(?:income from operations|operating income)\s+\$?\s*\(?([\d,]+(?:\.\d+)?)\)?",
        re.IGNORECASE,
    ),
    "net_income": re.compile(r"net income\s+\$?\s*\(?([\d,]+(?:\.\d+)?)\)?", re.IGNORECASE),
    "operating_cash_flow": re.compile(
        r"net cash provided by operating activities\s+\$?\s*\(?([\d,]+(?:\.\d+)?)\)?",
        re.IGNORECASE,
    ),
    "cash_and_equivalents": re.compile(
        r"cash and cash equivalents\s+\$?\s*\(?([\d,]+(?:\.\d+)?)\)?",
        re.IGNORECASE,
    ),
    "short_term_investments": re.compile(
        r"(?:short-term investments|short term investments|marketable securities)\s+\$?\s*\(?([\d,]+(?:\.\d+)?)\)?",
        re.IGNORECASE,
    ),
    "inventory": re.compile(r"inventories?\s+\$?\s*\(?([\d,]+(?:\.\d+)?)\)?", re.IGNORECASE),
    "accounts_receivable": re.compile(
        r"(?:accounts receivable|accounts receivable, net)\s+\$?\s*\(?([\d,]+(?:\.\d+)?)\)?",
        re.IGNORECASE,
    ),
    "total_debt": re.compile(
        r"(?:total debt|long-term debt|long term debt|debt and finance leases|debt and other financing)\s+\$?\s*\(?([\d,]+(?:\.\d+)?)\)?",
        re.IGNORECASE,
    ),
    "lease_liabilities": re.compile(
        r"(?:operating lease liabilities|finance lease liabilities|lease liabilities)\s+\$?\s*\(?([\d,]+(?:\.\d+)?)\)?",
        re.IGNORECASE,
    ),
    "capital_expenditures": re.compile(
        r"purchases of property and equipment(?: excluding finance leases, net of sales)?\s+\(?\$?\s*\(?([\d,]+(?:\.\d+)?)\)?",
        re.IGNORECASE,
    ),
    "stock_based_compensation": re.compile(
        r"(?:stock-based compensation|stock based compensation|share-based compensation)\s+\$?\s*\(?([\d,]+(?:\.\d+)?)\)?",
        re.IGNORECASE,
    ),
    "diluted_eps": re.compile(
        r"net income per share of common stock attributable to common stockholders.*?diluted\s+\$?\s*\(?([\d]+(?:\.\d+)?)\)?",
        re.IGNORECASE | re.DOTALL,
    ),
}

CORE_METRIC_SPECS = {
    "revenue": {"metric_type": "amount", "required": True, "unit": "USD_million"},
    "gross_profit": {"metric_type": "amount", "required": False, "unit": "USD_million"},
    "net_income": {"metric_type": "amount", "required": True, "unit": "USD_million"},
    "operating_income": {"metric_type": "amount", "required": True, "unit": "USD_million"},
    "gross_margin": {"metric_type": "ratio", "required": True, "unit": "ratio"},
    "operating_margin": {"metric_type": "ratio", "required": False, "unit": "ratio"},
    "diluted_eps": {"metric_type": "per_share", "required": True, "unit": "USD_per_share"},
    "operating_cash_flow": {"metric_type": "amount", "required": True, "unit": "USD_million"},
    "cash_and_equivalents": {"metric_type": "amount", "required": False, "unit": "USD_million"},
    "short_term_investments": {"metric_type": "amount", "required": False, "unit": "USD_million"},
    "inventory": {"metric_type": "amount", "required": False, "unit": "USD_million"},
    "accounts_receivable": {"metric_type": "amount", "required": False, "unit": "USD_million"},
    "total_debt": {"metric_type": "amount", "required": False, "unit": "USD_million"},
    "lease_liabilities": {"metric_type": "amount", "required": False, "unit": "USD_million"},
    "capital_expenditures": {"metric_type": "amount", "required": False, "unit": "USD_million"},
    "free_cash_flow": {"metric_type": "amount", "required": False, "unit": "USD_million"},
    "stock_based_compensation": {"metric_type": "amount", "required": False, "unit": "USD_million"},
}

COMPANY_PROFILE_RULES = {
    "segments": (
        ("Data Center", ("data center",)),
        ("Gaming", ("gaming",)),
        ("Professional Visualization", ("professional visualization",)),
        ("Automotive and Robotics", ("automotive and robotics",)),
        ("Automotive", ("automotive",)),
        ("Energy Generation and Storage", ("energy generation and storage",)),
        ("Services and Other", ("services and other",)),
    ),
    "major_products": (
        ("GPUs", ("gpu", "gpus", "graphics processing unit")),
        ("CPUs", ("cpu", "cpus")),
        ("Networking Products", ("networking", "ethernet", "infiniband", "interconnect")),
        ("AI Platforms", ("cuda", "accelerated computing", "ai platform", "ai systems")),
        ("Software Platforms", ("software platform", "sdk", "api", "library")),
        ("Vehicles", ("vehicle", "vehicles", "electric vehicle", "ev")),
        ("Energy Storage", ("energy storage", "battery storage", "megapack", "powerwall")),
    ),
    "manufacturing_regions": (
        ("United States", ("united states", "u.s.", "usa", "california", "texas", "new york")),
        ("China", ("china", "shanghai")),
        ("Taiwan", ("taiwan",)),
        ("Europe", ("europe", "germany", "berlin", "france", "ireland", "united kingdom")),
        ("Mexico", ("mexico",)),
    ),
    "strategic_themes": (
        ("AI and accelerated computing", ("ai", "artificial intelligence", "accelerated computing", "inference", "training")),
        ("Platform software and ecosystem", ("software", "sdk", "api", "developer", "platform")),
        ("Data center expansion", ("data center", "cloud", "hyperscale")),
        ("Product and technology roadmap", ("next-generation", "roadmap", "architecture", "new product")),
        ("Operations and supply chain", ("operations", "capacity", "supply chain", "manufacturing")),
        ("Autonomous and robotics", ("autonomous", "robotics")),
        ("Energy transition", ("energy storage", "battery", "renewable")),
    ),
}

EXPLANATION_TOPIC_RULES = (
    ("revenue_change", ("revenue", "sales", "deliveries", "deployment")),
    ("margin_change", ("gross margin", "gross profit", "margin", "profitability")),
    ("operating_income_change", ("operating income", "income from operations", "operating expenses")),
    ("net_income_change", ("net income", "tax", "valuation allowance")),
    ("cash_flow_change", ("cash flow", "operating activities", "working capital")),
    ("capex_change", ("capital expenditures", "capex", "property and equipment")),
    ("demand_and_pricing", ("pricing", "demand", "orders", "affordable")),
    ("production_and_supply", ("production", "factory", "supply chain", "manufacturing", "gigafactory")),
    ("management_outlook", ("expect", "plan", "guidance", "outlook", "future", "2025")),
)

RISK_RULES = (
    ("execution and scaling risk", ("execution", "ramp", "capacity", "deployment", "manufacturing"), ["revenue", "margin"]),
    ("supplier and concentration risk", ("supplier", "component", "single source", "concentration"), ["revenue", "margin"]),
    ("demand and pricing risk", ("demand", "pricing", "consumer spending", "cyclicality", "inventory"), ["revenue", "margin"]),
    ("competition risk", ("competition", "competitive", "price reductions"), ["revenue", "margin"]),
    ("regulatory and geopolitical risk", ("tariff", "regulation", "policy", "export control", "geopolitical"), ["revenue", "margin"]),
    ("technology transition risk", ("next-generation", "product transition", "roadmap", "architecture"), ["revenue", "margin"]),
)

ACCOUNTING_FLAG_RULES = (
    ("Revenue recognition", ("revenue recognition", "deferred revenue")),
    ("Inventory valuation", ("inventory", "write-down", "lower of cost")),
    ("Warranty reserve", ("warranty", "recall")),
    ("Stock-based compensation", ("stock-based compensation", "share-based compensation")),
    ("Income tax comparability", ("income taxes", "deferred tax", "valuation allowance")),
    ("Segment information", ("segment information", "reportable segment")),
    ("Debt and leases", ("lease", "debt", "convertible")),
    ("Cash and investments", ("cash and cash equivalents", "short-term investments", "marketable securities")),
)

ACCOUNTING_POLICY_KEYWORDS = (
    "revenue recognition",
    "asc 606",
    "performance obligation",
    "accounting policy",
    "lessor perspective",
    "lessee",
    "lease component",
    "estimates and assumptions",
    "warranty reserve",
    "recognized over time",
    "ownership life",
    "ongoing maintenance",
    "carrying value",
    "net amount realizable",
    "estimated selling price",
)

IXBRL_METRIC_FACT_NAMES = {
    "revenue": (
        "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
        "us-gaap:RevenueFromContractWithCustomerIncludingAssessedTax",
        "us-gaap:SalesRevenueNet",
        "us-gaap:Revenues",
    ),
    "gross_profit": ("us-gaap:GrossProfit",),
    "operating_income": ("us-gaap:OperatingIncomeLoss",),
    "net_income": ("us-gaap:NetIncomeLoss",),
    "diluted_eps": (
        "us-gaap:EarningsPerShareDiluted",
        "us-gaap:DilutedEarningsPerShare",
    ),
    "operating_cash_flow": ("us-gaap:NetCashProvidedByUsedInOperatingActivities",),
    "cash_and_equivalents": ("us-gaap:CashAndCashEquivalentsAtCarryingValue",),
    "short_term_investments": (
        "us-gaap:MarketableSecuritiesCurrent",
        "us-gaap:AvailableForSaleSecuritiesCurrent",
        "us-gaap:AvailableForSaleDebtSecuritiesCurrent",
    ),
    "accounts_receivable": (
        "us-gaap:AccountsReceivableNetCurrent",
        "us-gaap:ReceivablesNetCurrent",
    ),
    "inventory": ("us-gaap:InventoryNet",),
    "capital_expenditures": (
        "us-gaap:PaymentsToAcquirePropertyPlantAndEquipment",
        "us-gaap:CapitalExpendituresIncurredButNotYetPaid",
    ),
    "stock_based_compensation": (
        "us-gaap:AllocatedShareBasedCompensationExpense",
        "us-gaap:ShareBasedCompensation",
    ),
}

IXBRL_DURATION_METRICS = {
    "revenue",
    "gross_profit",
    "operating_income",
    "net_income",
    "diluted_eps",
    "operating_cash_flow",
    "capital_expenditures",
    "stock_based_compensation",
}

EXPLANATION_SIGNAL_KEYWORDS = (
    "increase",
    "decrease",
    "compared to",
    "compared with",
    "primarily due",
    "due to",
    "representing",
    "impact on",
    "resulting in",
    "we expect",
    "will continue",
)

RISK_SUMMARY_TEMPLATES = {
    "execution and scaling risk": "Execution problems in scaling operations, deployment, or capacity can delay growth and pressure margins.",
    "supplier and concentration risk": "Dependence on key suppliers, concentrated components, or large counterparties can disrupt operations and raise costs.",
    "demand and pricing risk": "Demand softness, pricing pressure, or inventory adjustments can weigh on revenue growth and profitability.",
    "competition risk": "Competitive pressure can reduce pricing power, raise selling costs, or slow market share gains.",
    "regulatory and geopolitical risk": "Regulation, export controls, tariffs, or geopolitical changes can affect demand, supply continuity, and costs.",
    "technology transition risk": "Product or architecture transitions can create execution risk if timing, adoption, or performance does not meet expectations.",
}


def parse_source(path: str | Path) -> ParsedDocument:
    source_path = Path(path).expanduser().resolve()
    selected_file = _pick_input_file(source_path)
    file_type = selected_file.suffix.lower().lstrip(".")

    if file_type in {"html", "htm"}:
        text = _parse_html(selected_file)
    elif file_type == "txt":
        text = _parse_text(selected_file)
    elif file_type == "pdf":
        text = _parse_pdf(selected_file)
    else:
        raise ValueError(f"Unsupported file type: {selected_file.suffix}")

    company_name = _extract_company_name(text, selected_file)
    reporting_period = _extract_reporting_period(text, selected_file)
    warnings: list[str] = []

    if len(text) < 1500:
        warnings.append(
            "Extracted text is short. If this is a scanned PDF, add a VLM OCR step next."
        )

    return ParsedDocument(
        source_path=source_path,
        selected_file=selected_file,
        file_type=file_type,
        text=text,
        company_name=company_name,
        reporting_period=reporting_period,
        warnings=warnings,
    )


def build_prepared_context(
    document: ParsedDocument,
    *,
    max_chars: int,
    opening_excerpt_chars: int,
    section_excerpt_chars: int,
    closing_excerpt_chars: int,
) -> PreparedContext:
    warnings = list(document.warnings)
    section_snippets = _extract_section_snippets(
        document.text,
        section_excerpt_chars=section_excerpt_chars,
    )
    narrative_chunks = _build_narrative_chunks(section_snippets)
    table_chunks = _build_table_chunks(document.text)
    note_chunks = _build_note_chunks(document.text)
    metric_records = _build_metric_records(
        selected_file=document.selected_file,
        file_type=document.file_type,
        section_snippets=section_snippets,
        narrative_chunks=narrative_chunks,
        table_chunks=table_chunks,
        note_chunks=note_chunks,
        reporting_period=document.reporting_period,
    )
    valid_metric_records = [record for record in metric_records if record.valid]
    financial_metric_map = {
        record.metric: _format_metric_record(record) for record in valid_metric_records
    }
    financial_facts = list(financial_metric_map.values())
    company_profile = _build_company_profile(
        document=document,
        section_snippets=section_snippets,
    )
    financial_snapshot = _build_financial_snapshot(metric_records)
    key_explanations = _build_key_explanations(narrative_chunks)
    key_risks = _build_key_risks(narrative_chunks)
    accounting_flags = _build_accounting_flags(
        note_chunks=note_chunks,
        narrative_chunks=narrative_chunks,
    )
    outlook_signals = _build_outlook_signals(narrative_chunks)
    investor_summary_layer = _build_investor_summary_layer(
        metric_records=metric_records,
        narrative_chunks=narrative_chunks,
        note_chunks=note_chunks,
    )
    investor_summary_input = _build_investor_summary_input(
        company_profile=company_profile,
        financial_snapshot=financial_snapshot,
        key_explanations=key_explanations,
        key_risks=key_risks,
        accounting_flags=accounting_flags,
        outlook_signals=outlook_signals,
    )

    if valid_metric_records:
        pass
    else:
        warnings.append(
            "Could not confidently extract validated core financial metrics from structured financial statements."
        )

    invalid_metrics = [
        f"{record.metric}: {', '.join(record.validation_errors)}"
        for record in metric_records
        if not record.valid and record.validation_errors
    ]
    if invalid_metrics:
        warnings.append(
            "Some extracted metrics were rejected by validation rules: "
            + "; ".join(invalid_metrics[:5])
            + "."
        )

    missing_sections = [
        section_name
        for section_name, _ in SECTION_PATTERNS
        if section_name not in section_snippets
    ]
    if missing_sections:
        warnings.append(
            "Some standard 10-K sections were not detected cleanly in the extracted text: "
            + ", ".join(missing_sections)
            + "."
        )

    context = json.dumps(investor_summary_input, ensure_ascii=False, indent=2)
    if len(context) > max_chars:
        context = _smart_trim(document_text=context, limit=max_chars).rstrip()
        warnings.append(
            f"Investor summary bundle exceeded the target budget and was trimmed to {max_chars} characters."
        )

    if len(document.text) > len(context):
        coverage = round(min(100.0, (len(context) / max(len(document.text), 1)) * 100), 1)
        warnings.append(
            f"Prepared investor summary bundle retains {coverage}% of raw filing characters after structured compression."
        )
        if coverage > 12:
            warnings.append(
                "Prepared bundle is still larger than the target 5% to 12% range and may need further summarization rules."
            )

    return PreparedContext(
        document=document,
        context=context,
        financial_facts=financial_facts,
        financial_metric_map=financial_metric_map,
        metric_records=metric_records,
        section_snippets=section_snippets,
        narrative_chunks=narrative_chunks,
        table_chunks=table_chunks,
        note_chunks=note_chunks,
        investor_summary_layer=investor_summary_layer,
        company_profile=company_profile,
        financial_snapshot=financial_snapshot,
        key_explanations=key_explanations,
        key_risks=key_risks,
        accounting_flags=accounting_flags,
        outlook_signals=outlook_signals,
        investor_summary_input=investor_summary_input,
        warnings=warnings,
    )


def _make_context_block(
    *,
    title: str,
    text: str,
    priority: int,
    min_chars: int,
    max_chars: int,
    order: int,
) -> dict[str, object]:
    cleaned = text.strip()
    actual_max = min(len(cleaned), max_chars)
    return {
        "title": title,
        "text": cleaned,
        "priority": priority,
        "min_chars": min(min_chars, actual_max),
        "max_chars": actual_max,
        "order": order,
    }


def _allocate_context_blocks(
    blocks: list[dict[str, object]],
    *,
    budget: int,
) -> tuple[list[dict[str, object]], list[str]]:
    if budget <= 0:
        return [], [str(block["title"]).strip("[]") for block in blocks]

    sorted_by_priority = sorted(
        blocks,
        key=lambda block: (int(block["priority"]), -int(block["order"])),
        reverse=True,
    )

    selected: list[dict[str, object]] = []
    omitted: list[str] = []
    reserved = 0

    for block in sorted_by_priority:
        block_min = max(0, int(block["min_chars"]))
        block_overhead = len(str(block["title"])) + 2
        needed = block_min + block_overhead
        if selected and reserved + needed > budget:
            omitted.append(str(block["title"]).strip("[]"))
            continue

        if not selected and needed > budget:
            fallback = dict(block)
            fallback["allocated"] = max(0, budget - block_overhead)
            if int(fallback["allocated"]) > 0:
                selected.append(fallback)
            else:
                omitted.append(str(block["title"]).strip("[]"))
            break

        chosen = dict(block)
        chosen["allocated"] = block_min
        selected.append(chosen)
        reserved += needed

    remaining = max(0, budget - reserved)
    expandable = [block for block in selected if int(block["max_chars"]) > int(block["allocated"])]

    while remaining > 0 and expandable:
        total_weight = sum(max(1, int(block["priority"])) for block in expandable)
        spent_this_round = 0
        for block in expandable:
            capacity = int(block["max_chars"]) - int(block["allocated"])
            if capacity <= 0:
                continue
            share = max(1, remaining * max(1, int(block["priority"])) // total_weight)
            addition = min(capacity, share, remaining)
            block["allocated"] = int(block["allocated"]) + addition
            remaining -= addition
            spent_this_round += addition
            if remaining == 0:
                break

        if spent_this_round == 0:
            break

        expandable = [block for block in selected if int(block["max_chars"]) > int(block["allocated"])]

    finalized = []
    for block in sorted(selected, key=lambda item: int(item["order"])):
        excerpt = _smart_trim(
            document_text=str(block["text"]),
            limit=max(0, int(block["allocated"])),
        )
        if excerpt:
            finalized.append(
                {
                    "title": block["title"],
                    "excerpt": excerpt,
                }
            )

    return finalized, omitted


def _extract_closing_excerpt(
    text: str,
    *,
    closing_excerpt_chars: int,
    opening_excerpt_chars: int,
) -> str:
    if closing_excerpt_chars <= 0 or len(text) <= opening_excerpt_chars + 1200:
        return ""

    start = max(opening_excerpt_chars, len(text) - closing_excerpt_chars)
    excerpt = text[start:].strip()
    return excerpt


def _extract_financial_metric_map(text: str, limit: int = 12) -> dict[str, str]:
    candidate_units = _extract_metric_candidate_units(text)
    metrics: dict[str, str] = {}
    seen: set[str] = set()

    for label, pattern in FINANCIAL_FACT_PATTERNS:
        best_candidate: str | None = None
        best_score = -1
        for unit in candidate_units:
            if not pattern.search(unit):
                continue

            candidate = _format_financial_fact(label=label, snippet=unit)
            if not candidate:
                continue

            score = _financial_fact_score(candidate, label=label)
            if score > best_score:
                best_candidate = candidate
                best_score = score

        if not best_candidate:
            continue

        normalized = re.sub(r"\W+", "", best_candidate.lower())
        if normalized in seen:
            continue

        seen.add(normalized)
        metrics[label] = best_candidate
        if len(metrics) >= limit:
            break

    return metrics


def _extract_metric_candidate_units(text: str) -> list[str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    units: list[str] = []
    current: list[str] = []
    current_len = 0

    for line in lines:
        projected = current_len + len(line) + 1
        if current and (_looks_like_subheading(line) or projected > 420):
            units.append(" ".join(current))
            current = [line]
            current_len = len(line)
            continue
        current.append(line)
        current_len = projected

    if current:
        units.append(" ".join(current))

    return [_clean_chunk_text(unit) for unit in units if unit.strip()]


def _extract_financial_fact_snippets(text: str, limit: int = 12) -> list[str]:
    return list(_extract_financial_metric_map(text, limit=limit).values())


def _extract_section_snippets(
    text: str,
    *,
    section_excerpt_chars: int,
) -> dict[str, str]:
    section_matches = _find_section_matches(text)

    section_matches.sort(key=lambda item: item[1])
    snippets: dict[str, str] = {}

    for index, (name, start) in enumerate(section_matches):
        next_start = (
            section_matches[index + 1][1]
            if index + 1 < len(section_matches)
            else len(text)
        )
        raw_section = text[start:next_start].strip()
        if not raw_section:
            continue
        snippets[name] = _smart_trim(raw_section, section_excerpt_chars)

    return snippets


def _build_metric_records(
    *,
    selected_file: Path,
    file_type: str,
    section_snippets: dict[str, str],
    narrative_chunks: list[FilingChunk],
    table_chunks: list[FilingChunk],
    note_chunks: list[FilingChunk],
    reporting_period: str,
) -> list[MetricRecord]:
    table_metric_hits = _extract_metric_values_from_table_chunks(table_chunks, reporting_period)
    ixbrl_metric_hits = (
        _extract_metric_values_from_ixbrl(selected_file, reporting_period)
        if file_type in {"html", "htm"}
        else {}
    )
    records: list[MetricRecord] = []
    for metric_name, spec in CORE_METRIC_SPECS.items():
        table_hit = ixbrl_metric_hits.get(metric_name) or table_metric_hits.get(metric_name)
        corroborating_sources, corroborating_evidence = _find_metric_corroboration(
            metric_name=metric_name,
            value=table_hit["value"] if table_hit else None,
            narrative_chunks=narrative_chunks,
        )
        if table_hit:
            sources = [str(table_hit["source"]), *corroborating_sources]
            confidence = "high" if len(sources) >= 2 else "medium"
            validation_errors = _validate_metric_value(
                metric=metric_name,
                metric_type=str(spec["metric_type"]),
                value=table_hit["value"],
                unit=str(table_hit["unit"]),
            )
            records.append(
                MetricRecord(
                    metric=metric_name,
                    metric_type=str(spec["metric_type"]),
                    value=table_hit["value"],
                    unit=str(table_hit["unit"]),
                    period=str(table_hit["period"]),
                    source=str(table_hit["source"]),
                    sources=sources,
                    canonical_source_chunk_id=str(table_hit.get("chunk_id", "")),
                    canonical_source_table_name=str(table_hit.get("table_name", "")),
                    explanatory_chunk_ids=[entry["chunk_id"] for entry in corroborating_evidence],
                    canonical_numeric_source={
                        "chunk_id": str(table_hit.get("chunk_id", "")),
                        "table_name": str(table_hit.get("table_name", "")),
                        "source": str(table_hit["source"]),
                        "current_value": table_hit.get("current_value"),
                        "previous_value": table_hit.get("previous_value"),
                        "period": str(table_hit.get("period", "")),
                        "evidence": str(table_hit["evidence"]),
                    },
                    explanatory_sources=corroborating_evidence,
                    confidence="low" if validation_errors else confidence,
                    valid=not validation_errors,
                    validation_errors=validation_errors,
                    evidence=str(table_hit["evidence"]),
                )
            )
            continue

        narrative_hit = _extract_metric_from_narrative_chunks(
            metric_name=metric_name,
            narrative_chunks=narrative_chunks,
            reporting_period=reporting_period,
        )
        if narrative_hit:
            validation_errors = _validate_metric_value(
                metric=metric_name,
                metric_type=str(spec["metric_type"]),
                value=narrative_hit["value"],
                unit=str(narrative_hit["unit"]),
            )
            records.append(
                MetricRecord(
                    metric=metric_name,
                    metric_type=str(spec["metric_type"]),
                    value=narrative_hit["value"],
                    unit=str(narrative_hit["unit"]),
                    period=str(narrative_hit["period"]),
                    source=str(narrative_hit["source"]),
                    sources=[str(narrative_hit["source"])],
                    canonical_source_chunk_id=str(narrative_hit.get("chunk_id", "")),
                    canonical_source_table_name=str(narrative_hit.get("table_name", "")),
                    explanatory_chunk_ids=[],
                    canonical_numeric_source={
                        "chunk_id": str(narrative_hit.get("chunk_id", "")),
                        "table_name": str(narrative_hit.get("table_name", "")),
                        "source": str(narrative_hit["source"]),
                        "current_value": narrative_hit.get("current_value"),
                        "previous_value": narrative_hit.get("previous_value"),
                        "period": str(narrative_hit.get("period", "")),
                        "evidence": str(narrative_hit["evidence"]),
                    },
                    explanatory_sources=[],
                    confidence="low" if validation_errors else "medium",
                    valid=not validation_errors,
                    validation_errors=validation_errors,
                    evidence=str(narrative_hit["evidence"]),
                )
            )
            continue

        missing_error = "missing structured table value"
        canonical_numeric_source: dict[str, object] = {}
        evidence = "Not found in structured financial statements."
        if metric_name == "lease_liabilities":
            related_note_chunk_ids = [
                chunk.chunk_id
                for chunk in note_chunks
                if "note 11" in chunk.section_path.lower()
                or "lease" in chunk.section_path.lower()
                or "lease" in chunk.text.lower()
            ][:6]
            canonical_numeric_source = {
                "not_extracted_reason": "Lease liabilities were not extracted into a stable single metric from the current table/note parser.",
                "related_note_chunk_ids": related_note_chunk_ids,
                "manual_review_needed": True,
            }
            evidence = "Lease disclosures were found, but a stable consolidated lease liability total was not extracted."
        records.append(
            MetricRecord(
                metric=metric_name,
                metric_type=str(spec["metric_type"]),
                value=None,
                unit=str(spec["unit"]),
                period=_extract_period_from_text(reporting_period) or reporting_period,
                source="missing",
                sources=[],
                canonical_source_chunk_id="",
                canonical_source_table_name="",
                explanatory_chunk_ids=[],
                canonical_numeric_source=canonical_numeric_source,
                explanatory_sources=[],
                confidence="low",
                valid=False,
                validation_errors=[missing_error],
                evidence=evidence,
            )
        )
    return records


def _build_narrative_chunks(section_snippets: dict[str, str]) -> list[FilingChunk]:
    chunks: list[FilingChunk] = []
    order = 0
    for section_name, section_text in section_snippets.items():
        parent_chunk_id = _make_chunk_id(section_name, 0, prefix="section")
        for index, block in enumerate(_split_section_into_blocks(section_text)):
            chunk_type = _chunk_type_for_section(section_name)
            section_path = section_name
            subsection_title = section_name
            metadata: dict[str, object] = {}
            if section_name == "Item 7 MD&A":
                subtopic = _classify_item7_subtopic(block)
                section_path = f"{section_name}/{subtopic}"
                metadata["subtopic"] = subtopic
                subsection_title = subtopic.replace("_", " ")
                if subtopic == "outlook":
                    chunk_type = "outlook_chunk"
            chunk_id = _make_chunk_id(section_name, index)
            chunks.append(
                FilingChunk(
                    chunk_id=chunk_id,
                    chunk_type=chunk_type,
                    section_path=section_path,
                    order=order,
                    text=block,
                    item_number=section_name.split()[1] if section_name.startswith("Item ") else "",
                    subsection_title=subsection_title,
                    token_count=_estimate_token_count(block),
                    importance_score=_importance_score_for_chunk(section_path, chunk_type),
                    year=_extract_year_int(block),
                    source_page="",
                    source_anchor=f"section:{section_name}:{index+1}",
                    parent_chunk_id=parent_chunk_id,
                    is_sentence_complete=_is_sentence_complete_text(block),
                    is_numeric_dense=_is_numeric_dense_text(block),
                    has_toc_noise=_contains_toc_noise(block),
                    has_table_structure=False,
                    extraction_confidence="medium",
                    parser_source="pre_split_parser",
                    validation_status="ok",
                    metadata=metadata,
                )
            )
            order += 1
    return chunks


def _build_table_chunks(text: str) -> list[FilingChunk]:
    search_space, region_start = _extract_item8_region(text)
    chunks: list[FilingChunk] = []
    if search_space:
        chunks.extend(
            _collect_table_chunks_from_search_space(
                search_space=search_space,
                region_start=region_start,
                patterns=TABLE_HEADING_PATTERNS,
                section_name="Item 8 Financial Statements",
                item_number="Item 8",
                prefix="table",
            )
        )

    # Some large-cap tech filings expose the key summary tables more reliably in Item 7
    # than in HTML-rendered Item 8 tables. Use them as a structured fallback source.
    fallback_chunks = _collect_table_chunks_from_search_space(
        search_space=text,
        region_start=0,
        patterns=FALLBACK_TABLE_HEADING_PATTERNS,
        section_name="Item 7 MD&A",
        item_number="Item 7",
        prefix="mdna_table",
    )
    chunks.extend(
        chunk
        for chunk in fallback_chunks
        if chunk.metadata.get("table_title") not in {
            existing.metadata.get("table_title") for existing in chunks
        }
    )
    return chunks


def _extract_metric_values_from_ixbrl(
    selected_file: Path,
    reporting_period: str,
) -> dict[str, dict[str, object]]:
    if selected_file.suffix.lower() not in {".html", ".htm"}:
        return {}

    raw = selected_file.read_text(encoding="utf-8", errors="ignore")
    soup = BeautifulSoup(raw, "html.parser")
    contexts = _parse_ixbrl_contexts(soup)
    extracted: dict[str, dict[str, object]] = {}
    report_year = _extract_period_from_text(reporting_period) or reporting_period

    for metric_name, fact_names in IXBRL_METRIC_FACT_NAMES.items():
        candidates: list[dict[str, object]] = []
        for fact_name in fact_names:
            for tag in soup.find_all("ix:nonfraction", attrs={"name": fact_name}):
                parsed = _parse_ixbrl_fact(
                    tag,
                    contexts=contexts,
                    metric_name=metric_name,
                    target_unit=str(CORE_METRIC_SPECS[metric_name]["unit"]),
                )
                if parsed:
                    candidates.append(parsed)
        selected = _select_ixbrl_metric_series(metric_name, candidates, fallback_period=report_year)
        if selected:
            extracted[metric_name] = selected

    debt_series = _extract_ixbrl_total_debt_series(contexts, soup, fallback_period=report_year)
    if debt_series:
        extracted["total_debt"] = debt_series

    if "gross_profit" in extracted and "revenue" in extracted and "gross_margin" not in extracted:
        revenue = float(extracted["revenue"]["value"] or 0)
        gross_profit = float(extracted["gross_profit"]["value"] or 0)
        previous_revenue = _to_float(extracted["revenue"].get("previous_value"))
        previous_gross_profit = _to_float(extracted["gross_profit"].get("previous_value"))
        if revenue > 0:
            extracted["gross_margin"] = {
                "value": round(gross_profit / revenue, 6),
                "current_value": round(gross_profit / revenue, 6),
                "previous_value": round(previous_gross_profit / previous_revenue, 6)
                if previous_revenue and previous_gross_profit is not None
                else None,
                "unit": "ratio",
                "period": report_year,
                "source": "Inline XBRL facts",
                "chunk_id": "",
                "table_name": "Derived from Inline XBRL revenue and gross profit",
                "evidence": (
                    f"Computed from Inline XBRL facts revenue={revenue} and gross_profit={gross_profit}."
                ),
            }
    if "operating_income" in extracted and "revenue" in extracted and "operating_margin" not in extracted:
        revenue = float(extracted["revenue"]["value"] or 0)
        operating_income = float(extracted["operating_income"]["value"] or 0)
        previous_revenue = _to_float(extracted["revenue"].get("previous_value"))
        previous_operating_income = _to_float(extracted["operating_income"].get("previous_value"))
        if revenue > 0:
            extracted["operating_margin"] = {
                "value": round(operating_income / revenue, 6),
                "current_value": round(operating_income / revenue, 6),
                "previous_value": round(previous_operating_income / previous_revenue, 6)
                if previous_revenue and previous_operating_income is not None
                else None,
                "unit": "ratio",
                "period": report_year,
                "source": "Inline XBRL facts",
                "chunk_id": "",
                "table_name": "Derived from Inline XBRL revenue and operating income",
                "evidence": (
                    f"Computed from Inline XBRL facts revenue={revenue} and operating_income={operating_income}."
                ),
            }
    if "operating_cash_flow" in extracted and "capital_expenditures" in extracted and "free_cash_flow" not in extracted:
        current_ocf = _to_float(extracted["operating_cash_flow"].get("value"))
        current_capex = _to_float(extracted["capital_expenditures"].get("value"))
        previous_ocf = _to_float(extracted["operating_cash_flow"].get("previous_value"))
        previous_capex = _to_float(extracted["capital_expenditures"].get("previous_value"))
        if current_ocf is not None and current_capex is not None:
            extracted["free_cash_flow"] = {
                "value": round(current_ocf - current_capex, 6),
                "current_value": round(current_ocf - current_capex, 6),
                "previous_value": round(previous_ocf - previous_capex, 6)
                if previous_ocf is not None and previous_capex is not None
                else None,
                "unit": "USD_million",
                "period": report_year,
                "source": "Inline XBRL facts",
                "chunk_id": "",
                "table_name": "Derived from Inline XBRL operating cash flow and capex",
                "evidence": (
                    f"Computed from Inline XBRL facts operating_cash_flow={current_ocf} and capex={current_capex}."
                ),
            }

    return extracted


def _parse_ixbrl_contexts(soup: BeautifulSoup) -> dict[str, dict[str, object]]:
    contexts: dict[str, dict[str, object]] = {}
    for tag in soup.find_all("xbrli:context"):
        context_id = str(tag.get("id") or "").strip()
        if not context_id:
            continue
        start_date = _extract_ixbrl_context_date(tag, "startdate")
        end_date = _extract_ixbrl_context_date(tag, "enddate")
        instant_date = _extract_ixbrl_context_date(tag, "instant")
        has_dimensions = bool(
            tag.find(
                lambda child: isinstance(getattr(child, "name", None), str)
                and child.name.lower().endswith(("explicitmember", "typedmember"))
            )
        )
        contexts[context_id] = {
            "start_date": start_date,
            "end_date": end_date,
            "instant_date": instant_date,
            "has_dimensions": has_dimensions,
            "duration_days": _context_duration_days(start_date, end_date),
        }
    return contexts


def _extract_ixbrl_context_date(tag: object, suffix: str) -> str:
    if not hasattr(tag, "find"):
        return ""
    found = tag.find(
        lambda child: isinstance(getattr(child, "name", None), str)
        and child.name.lower().endswith(suffix)
    )
    if not found:
        return ""
    return str(found.get_text(" ", strip=True) or "").strip()


def _context_duration_days(start_date: str, end_date: str) -> int | None:
    if not start_date or not end_date:
        return None
    try:
        return (date.fromisoformat(end_date) - date.fromisoformat(start_date)).days
    except ValueError:
        return None


def _parse_ixbrl_fact(
    tag: object,
    *,
    contexts: dict[str, dict[str, object]],
    metric_name: str,
    target_unit: str,
) -> dict[str, object] | None:
    if not hasattr(tag, "get") or not hasattr(tag, "get_text"):
        return None
    context_id = str(tag.get("contextref") or "").strip()
    context = contexts.get(context_id)
    if not context or context.get("has_dimensions"):
        return None

    raw_text = str(tag.get_text(" ", strip=True) or "").strip()
    if not raw_text or not re.search(r"\d", raw_text):
        return None

    numeric_value = _parse_ixbrl_numeric_text(raw_text)
    if numeric_value is None:
        return None
    if str(tag.get("sign") or "").strip() == "-":
        numeric_value = -abs(numeric_value)

    try:
        scale = int(str(tag.get("scale") or "0").strip())
    except ValueError:
        scale = 0
    normalized_value = _normalize_ixbrl_value(
        numeric_value,
        scale=scale,
        target_unit=target_unit,
    )
    if normalized_value is None:
        return None

    period_key = ""
    if metric_name in IXBRL_DURATION_METRICS:
        period_key = str(context.get("end_date") or "")
    else:
        period_key = str(context.get("instant_date") or context.get("end_date") or "")

    return {
        "fact_name": str(tag.get("name") or "").strip(),
        "context_id": context_id,
        "period_kind": "duration" if metric_name in IXBRL_DURATION_METRICS else "instant",
        "period_key": period_key,
        "start_date": context.get("start_date"),
        "end_date": context.get("end_date"),
        "instant_date": context.get("instant_date"),
        "duration_days": context.get("duration_days"),
        "value": normalized_value,
        "evidence": raw_text,
    }


def _parse_ixbrl_numeric_text(text: str) -> float | None:
    cleaned = str(text).strip()
    if not cleaned:
        return None
    negative = cleaned.startswith("(") and cleaned.endswith(")")
    cleaned = cleaned.strip("()").replace(",", "")
    cleaned = cleaned.replace("$", "").replace("%", "")
    cleaned = re.sub(r"\s+", "", cleaned)
    if cleaned in {"", "-", "—", "–"}:
        return None
    try:
        value = float(cleaned)
    except ValueError:
        return None
    return -value if negative else value


def _normalize_ixbrl_value(
    numeric_value: float,
    *,
    scale: int,
    target_unit: str,
) -> float | None:
    actual_value = numeric_value * (10**scale)
    if target_unit == "USD_million":
        return actual_value / 1_000_000
    if target_unit == "USD_per_share":
        return actual_value
    if target_unit == "ratio":
        return actual_value
    return actual_value


def _select_ixbrl_metric_series(
    metric_name: str,
    candidates: list[dict[str, object]],
    *,
    fallback_period: str,
) -> dict[str, object] | None:
    if not candidates:
        return None

    if metric_name in IXBRL_DURATION_METRICS:
        preferred = [
            candidate
            for candidate in candidates
            if candidate.get("period_kind") == "duration"
            and isinstance(candidate.get("duration_days"), int)
            and int(candidate["duration_days"]) >= 300
        ]
        pool = preferred or [candidate for candidate in candidates if candidate.get("period_kind") == "duration"] or candidates
        ordered = sorted(
            pool,
            key=lambda candidate: (
                str(candidate.get("end_date") or ""),
                int(candidate.get("duration_days") or 0),
            ),
            reverse=True,
        )
    else:
        pool = [candidate for candidate in candidates if candidate.get("period_kind") == "instant"] or candidates
        ordered = sorted(
            pool,
            key=lambda candidate: str(candidate.get("instant_date") or candidate.get("end_date") or ""),
            reverse=True,
        )

    distinct: list[dict[str, object]] = []
    seen_keys: set[str] = set()
    for candidate in ordered:
        period_key = str(candidate.get("period_key") or "")
        if not period_key or period_key in seen_keys:
            continue
        seen_keys.add(period_key)
        distinct.append(candidate)
        if len(distinct) >= 2:
            break
    if not distinct:
        return None

    current = distinct[0]
    previous = distinct[1] if len(distinct) > 1 else None
    fact_name = str(current.get("fact_name") or "")
    return {
        "value": current.get("value"),
        "current_value": current.get("value"),
        "previous_value": previous.get("value") if previous else None,
        "unit": str(CORE_METRIC_SPECS.get(metric_name, {}).get("unit", "")),
        "period": str(current.get("period_key") or fallback_period),
        "source": "Inline XBRL facts",
        "chunk_id": "",
        "table_name": fact_name or "Inline XBRL fact",
        "evidence": f"{fact_name} ({current.get('context_id')}): {current.get('evidence')}",
    }


def _extract_ixbrl_total_debt_series(
    contexts: dict[str, dict[str, object]],
    soup: BeautifulSoup,
    *,
    fallback_period: str,
) -> dict[str, object] | None:
    current_candidates = []
    for fact_name in ("us-gaap:LongTermDebtCurrent", "us-gaap:LongTermDebtAndCapitalLeaseObligationsCurrent"):
        for tag in soup.find_all("ix:nonfraction", attrs={"name": fact_name}):
            parsed = _parse_ixbrl_fact(
                tag,
                contexts=contexts,
                metric_name="cash_and_equivalents",
                target_unit="USD_million",
            )
            if parsed:
                current_candidates.append(parsed)

    noncurrent_candidates = []
    for fact_name in ("us-gaap:LongTermDebtNoncurrent", "us-gaap:LongTermDebtAndCapitalLeaseObligations"):
        for tag in soup.find_all("ix:nonfraction", attrs={"name": fact_name}):
            parsed = _parse_ixbrl_fact(
                tag,
                contexts=contexts,
                metric_name="cash_and_equivalents",
                target_unit="USD_million",
            )
            if parsed:
                noncurrent_candidates.append(parsed)

    current_by_period = {
        str(candidate.get("period_key") or ""): candidate
        for candidate in current_candidates
        if candidate.get("period_key")
    }
    noncurrent_by_period = {
        str(candidate.get("period_key") or ""): candidate
        for candidate in noncurrent_candidates
        if candidate.get("period_key")
    }
    combined_candidates: list[dict[str, object]] = []
    for period_key in sorted(set(current_by_period) & set(noncurrent_by_period), reverse=True):
        current = current_by_period[period_key]
        noncurrent = noncurrent_by_period[period_key]
        combined_candidates.append(
            {
                "fact_name": "Inline XBRL total debt",
                "period_key": period_key,
                "instant_date": period_key,
                "value": _to_float(current.get("value")) + _to_float(noncurrent.get("value")),
                "context_id": period_key,
                "evidence": (
                    f"LongTermDebtCurrent={current.get('evidence')}; "
                    f"LongTermDebtNoncurrent={noncurrent.get('evidence')}"
                ),
            }
        )

    if not combined_candidates:
        return None

    current = combined_candidates[0]
    previous = combined_candidates[1] if len(combined_candidates) > 1 else None
    return {
        "value": current.get("value"),
        "current_value": current.get("value"),
        "previous_value": previous.get("value") if previous else None,
        "unit": "USD_million",
        "period": str(current.get("period_key") or fallback_period),
        "source": "Inline XBRL facts",
        "chunk_id": "",
        "table_name": "Inline XBRL total debt (current + non-current)",
        "evidence": str(current.get("evidence") or ""),
    }


def _extract_ixbrl_revenue_composition(
    selected_file: Path,
    reporting_period: str,
    total_revenue: float,
) -> dict[str, object] | None:
    if selected_file.suffix.lower() not in {".html", ".htm"} or total_revenue <= 0:
        return None

    raw = selected_file.read_text(encoding="utf-8", errors="ignore")
    soup = BeautifulSoup(raw, "html.parser")
    contexts = _parse_ixbrl_contexts(soup)
    candidates: list[dict[str, object]] = []
    axes_priority = (
        "srt:ProductOrServiceAxis",
        "us-gaap:StatementBusinessSegmentsAxis",
    )

    for fact_name in IXBRL_METRIC_FACT_NAMES["revenue"]:
        for tag in soup.find_all("ix:nonfraction", attrs={"name": fact_name}):
            context_id = str(tag.get("contextref") or "").strip()
            context = contexts.get(context_id)
            if not context or context.get("has_dimensions") is not True:
                continue
            dimension_members = _extract_ixbrl_dimension_members(soup, context_id)
            if not dimension_members:
                continue
            label = ""
            for axis_name in axes_priority:
                member = dimension_members.get(axis_name)
                if member:
                    label = _normalize_ixbrl_dimension_label(member)
                    if label:
                        break
            if not label:
                continue
            period_key = str(context.get("end_date") or "")
            if period_key != (_extract_period_iso(reporting_period) or period_key):
                if context.get("duration_days") and int(context["duration_days"]) < 300:
                    continue
            numeric_value = _parse_ixbrl_numeric_text(str(tag.get_text(" ", strip=True) or ""))
            if numeric_value is None:
                continue
            try:
                scale = int(str(tag.get("scale") or "0").strip())
            except ValueError:
                scale = 0
            value = _normalize_ixbrl_value(
                numeric_value,
                scale=scale,
                target_unit="USD_million",
            )
            if value is None or value <= 0 or value > total_revenue:
                continue
            candidates.append(
                {
                    "label": label,
                    "value": value,
                    "period_key": period_key,
                    "context_id": context_id,
                }
            )

    if not candidates:
        return None

    current_period = _extract_period_iso(reporting_period)
    period_candidates = [
        candidate for candidate in candidates if candidate.get("period_key") == current_period
    ] or candidates

    deduped: dict[str, dict[str, object]] = {}
    for candidate in sorted(period_candidates, key=lambda item: float(item["value"]), reverse=True):
        deduped.setdefault(str(candidate["label"]), candidate)
    unique_candidates = list(deduped.values())
    chosen = _choose_revenue_composition_subset(unique_candidates, total_revenue=total_revenue)
    if len(chosen) < 2:
        return None

    return {
        "segments": [{"name": str(item["label"]), "revenue": float(item["value"])} for item in chosen],
        "source_label": "Inline XBRL revenue composition",
    }


def _extract_ixbrl_profit_flow_totals(
    selected_file: Path,
    reporting_period: str,
) -> dict[str, float] | None:
    if selected_file.suffix.lower() not in {".html", ".htm"}:
        return None

    raw = selected_file.read_text(encoding="utf-8", errors="ignore")
    soup = BeautifulSoup(raw, "html.parser")
    contexts = _parse_ixbrl_contexts(soup)
    current_period = _extract_period_iso(reporting_period)

    def pick_fact_value(fact_names: tuple[str, ...]) -> float | None:
        candidates: list[dict[str, object]] = []
        for fact_name in fact_names:
            for tag in soup.find_all("ix:nonfraction", attrs={"name": fact_name}):
                parsed = _parse_ixbrl_fact(
                    tag,
                    contexts=contexts,
                    metric_name="revenue",
                    target_unit="USD_million",
                )
                if not parsed:
                    continue
                if parsed.get("period_key") != current_period:
                    continue
                candidates.append(parsed)
        if not candidates:
            return None
        chosen = sorted(
            candidates,
            key=lambda item: (
                str(item.get("period_key") or ""),
                int(item.get("duration_days") or 0),
            ),
            reverse=True,
        )[0]
        return _to_float(chosen.get("value"))

    cost_of_revenue = pick_fact_value(("us-gaap:CostOfRevenue",))
    costs_and_expenses = pick_fact_value(("us-gaap:CostsAndExpenses",))
    operating_expenses = pick_fact_value(("us-gaap:OperatingExpenses",))

    if cost_of_revenue is None and costs_and_expenses is None and operating_expenses is None:
        return None

    payload: dict[str, float] = {}
    if cost_of_revenue is not None:
        payload["cost_of_revenue"] = cost_of_revenue
    if operating_expenses is not None:
        payload["operating_expenses"] = operating_expenses
    if costs_and_expenses is not None:
        payload["costs_and_expenses"] = costs_and_expenses
    return payload


def _extract_ixbrl_dimension_members(
    soup: BeautifulSoup,
    context_id: str,
) -> dict[str, str]:
    context = soup.find("xbrli:context", attrs={"id": context_id})
    if not context:
        return {}
    members: dict[str, str] = {}
    for member in context.find_all(
        lambda child: isinstance(getattr(child, "name", None), str)
        and child.name.lower().endswith("explicitmember")
    ):
        axis = str(member.get("dimension") or "").strip()
        value = str(member.get_text(" ", strip=True) or "").strip()
        if axis and value:
            members[axis] = value
    return members


def _normalize_ixbrl_dimension_label(raw_member: str) -> str:
    text = str(raw_member or "").strip()
    if not text:
        return ""
    token = text.split(":")[-1]
    token = re.sub(r"Member$", "", token)
    replacements = {
        "GoogleSearchOther": "Google Search & Other",
        "YouTubeAdvertisingRevenue": "YouTube Ads",
        "GoogleNetwork": "Google Network",
        "SubscriptionsPlatformsAndDevicesRevenue": "Subscriptions, Platforms & Devices",
        "GoogleServices": "Google Services",
        "GoogleCloud": "Google Cloud",
        "AllOtherSegments": "Other Bets",
        "IPhone": "iPhone",
        "IPad": "iPad",
        "WearablesHomeandAccessories": "Wearables, Home & Accessories",
        "Mac": "Mac",
        "Service": "Services",
        "Product": "Products",
        "ProductivityAndBusinessProcesses": "Productivity & Business",
        "IntelligentCloud": "Intelligent Cloud",
        "MorePersonalComputing": "Personal Computing",
    }
    if token in replacements:
        return replacements[token]
    token = re.sub(r"([a-z])([A-Z])", r"\1 \2", token)
    token = token.replace("And", "&")
    return " ".join(token.split()).strip()


def _choose_revenue_composition_subset(
    candidates: list[dict[str, object]],
    *,
    total_revenue: float,
) -> list[dict[str, object]]:
    if not candidates:
        return []
    if len(candidates) > 12:
        candidates = sorted(candidates, key=lambda item: float(item["value"]), reverse=True)[:12]

    tolerance = max(1.0, total_revenue * 0.015)
    best_subset: list[dict[str, object]] = []
    best_score: tuple[float, int, float] | None = None

    for size in range(2, len(candidates) + 1):
        for subset in combinations(candidates, size):
            total = sum(float(item["value"]) for item in subset)
            diff = abs(total_revenue - total)
            if diff > tolerance:
                continue
            generic_penalty = sum(
                1.0
                for item in subset
                if str(item["label"]) in {"Products", "Services", "Google Services"}
            )
            score = (diff, -len(subset), generic_penalty)
            if best_score is None or score < best_score:
                best_score = score
                best_subset = list(subset)

    if best_subset:
        return sorted(best_subset, key=lambda item: float(item["value"]), reverse=True)

    fallback = [item for item in candidates if float(item["value"]) / total_revenue >= 0.05]
    return sorted(fallback, key=lambda item: float(item["value"]), reverse=True)


def _extract_period_iso(reporting_period: str) -> str:
    text = str(reporting_period or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return text
    for fmt in ("%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except Exception:
            continue
    match = re.search(r"(20\d{2}-\d{2}-\d{2})", text)
    return match.group(1) if match else ""


def _collect_table_chunks_from_search_space(
    *,
    search_space: str,
    region_start: int,
    patterns: tuple[re.Pattern[str], ...],
    section_name: str,
    item_number: str,
    prefix: str,
) -> list[FilingChunk]:
    chunks: list[FilingChunk] = []
    for pattern in patterns:
        if "notes to consolidated financial statements" in pattern.pattern.lower():
            continue
        matches = [
            match
            for match in pattern.finditer(search_space)
            if _is_heading_like_match(search_space, match.start())
            and not _looks_like_toc_excerpt(search_space[match.start() : match.start() + 900])
        ]
        if not matches:
            matches = [
                match
                for match in pattern.finditer(search_space)
                if not _looks_like_toc_excerpt(search_space[match.start() : match.start() + 900])
            ]
        if not matches:
            continue

        candidate_pool = matches
        if len(candidate_pool) > 1:
            later_matches = [
                match
                for match in candidate_pool
                if match.start() - candidate_pool[0].start() > 400
            ]
            if later_matches:
                candidate_pool = later_matches

        start = candidate_pool[0].start()
        end = _find_next_table_boundary(search_space, start + 1, patterns=patterns)
        excerpt = _clean_chunk_text(search_space[start:end])
        if len(excerpt) < 120:
            continue
        if _looks_like_toc_excerpt(excerpt[:700]):
            continue
        lowered_excerpt = excerpt[:1200].lower()
        if "we have audited the accompanying" in lowered_excerpt or "in our opinion" in lowered_excerpt:
            continue
        table_title = _first_line(excerpt)
        table_groups = _split_table_into_groups(excerpt)
        parent_chunk_id = _make_chunk_id(f"{section_name} {table_title}", 0, prefix=f"{prefix}_parent")
        for group_index, group_text in enumerate(table_groups):
            chunk_id = _make_chunk_id(section_name, len(chunks), prefix=prefix)
            chunks.append(
                FilingChunk(
                    chunk_id=chunk_id,
                    chunk_type="table_chunk",
                    section_path=f"{section_name}/{table_title}",
                    order=len(chunks),
                    text=group_text,
                    item_number=item_number,
                    subsection_title=table_title,
                    token_count=_estimate_token_count(group_text),
                    importance_score=0.95,
                    year=_extract_year_int(excerpt),
                    source_page=_extract_nearby_page_number(search_space, start),
                    source_anchor=f"char:{region_start + start}",
                    parent_chunk_id=parent_chunk_id,
                    is_sentence_complete=True,
                    is_numeric_dense=True,
                    has_toc_noise=_contains_toc_noise(group_text),
                    has_table_structure=True,
                    extraction_confidence="high",
                    validation_status="ok",
                    metadata={
                        "statement_name": table_title,
                        "table_title": table_title,
                        "unit": _extract_table_unit(excerpt),
                        "fiscal_year_columns": _extract_fiscal_year_columns(excerpt),
                        "group_index": group_index + 1,
                        "group_count": len(table_groups),
                    },
                )
            )
    return chunks


def _build_note_chunks(text: str) -> list[FilingChunk]:
    item8_region, region_start = _extract_item8_region(text)
    if not item8_region:
        return []

    notes_match = re.search(r"notes to consolidated financial statements", item8_region, re.IGNORECASE)
    if not notes_match:
        return []

    note_space = item8_region[notes_match.start() :]
    note_matches = _find_note_heading_matches(note_space)
    if not note_matches:
        return []

    chunks: list[FilingChunk] = []
    for idx, (note_number, note_title, note_start) in enumerate(note_matches):
        next_start = note_matches[idx + 1][2] if idx + 1 < len(note_matches) else len(note_space)
        note_text = _clean_chunk_text(note_space[note_start:next_start])
        if len(note_text) < 120:
            continue
        parent_chunk_id = _make_chunk_id(f"Item 8 Note {note_number}", 0, prefix="note_parent")
        subchunks = _split_note_into_subchunks(note_text, note_number=note_number, note_title=note_title)
        for sub_index, subchunk in enumerate(subchunks):
            chunk_id = _make_chunk_id(f"Item 8 Note {note_number}", sub_index, prefix="note")
            source_anchor = f"char:{region_start + notes_match.start() + note_start}"
            chunks.append(
                FilingChunk(
                    chunk_id=chunk_id,
                    chunk_type="note_table_chunk" if subchunk["has_table_structure"] else "note_chunk",
                    section_path=f"Item 8 Notes/Note {note_number}/{subchunk['subsection_title']}",
                    order=len(chunks),
                    text=subchunk["text"],
                    item_number="Item 8",
                    subsection_title=subchunk["subsection_title"],
                    token_count=_estimate_token_count(subchunk["text"]),
                    importance_score=0.9,
                    year=_extract_year_int(subchunk["text"]),
                    source_page=_extract_nearby_page_number(note_space, note_start),
                    source_anchor=source_anchor,
                    parent_chunk_id=parent_chunk_id,
                    is_sentence_complete=subchunk["is_sentence_complete"],
                    is_numeric_dense=subchunk["is_numeric_dense"],
                    has_toc_noise=_contains_toc_noise(subchunk["text"]),
                    has_table_structure=subchunk["has_table_structure"],
                    extraction_confidence="medium",
                    validation_status="ok",
                    metadata={
                        "note_number": note_number,
                        "note_title": note_title,
                        "related_statement": _infer_related_statement(subchunk["text"]),
                        "related_line_items": _infer_related_line_items(subchunk["text"]),
                    },
                )
            )

    return chunks


def _find_section_matches(text: str) -> list[tuple[str, int]]:
    matches: list[tuple[str, int]] = []
    for name, pattern in SECTION_PATTERNS:
        candidates = list(pattern.finditer(text))
        if not candidates:
            continue

        heading_candidates = [
            match for match in candidates if _is_heading_like_match(text, match.start())
        ]
        candidate_pool = heading_candidates or candidates
        if len(candidate_pool) > 1:
            later_candidates = [
                match
                for match in candidate_pool
                if match.start() - candidate_pool[0].start() > 1200
            ]
            if later_candidates:
                candidate_pool = later_candidates
        chosen = max(
            candidate_pool,
            key=lambda match: _section_match_score(text, match.start()),
        )
        matches.append((name, chosen.start()))

    return matches


def _extract_item8_region(text: str) -> tuple[str, int]:
    section_matches = _find_section_matches(text)
    item8_start = next(
        (start for name, start in section_matches if name == "Item 8 Financial Statements"),
        -1,
    )
    if item8_start == -1:
        return "", 0

    next_item_start = next(
        (start for name, start in section_matches if start > item8_start),
        len(text),
    )
    region = text[item8_start:next_item_start]
    if _looks_like_toc_excerpt(region[:1200]):
        return "", 0
    return region, item8_start


def _find_note_heading_matches(note_space: str) -> list[tuple[str, str, int]]:
    lines = note_space.splitlines()
    offsets: list[int] = []
    cursor = 0
    for line in lines:
        offsets.append(cursor)
        cursor += len(line) + 1

    matches: list[tuple[str, str, int]] = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        match = NOTE_HEADING_INLINE.match(stripped)
        if not match:
            continue
        note_number = match.group(1).strip()
        inline_title = (match.group(2) or "").strip(" -–,:")
        note_title = inline_title
        if not note_title:
            for next_line in lines[index + 1 : index + 4]:
                next_stripped = next_line.strip()
                if next_stripped and not next_stripped.lower().startswith("note "):
                    note_title = next_stripped
                    break
        if not note_title:
            note_title = f"Note {note_number}"
        matches.append((note_number, note_title, offsets[index]))
    return matches


def _split_note_into_subchunks(
    note_text: str,
    *,
    note_number: str,
    note_title: str,
) -> list[dict[str, object]]:
    blocks = _split_section_into_blocks(note_text)
    subchunks: list[dict[str, object]] = []
    for index, block in enumerate(blocks):
        subsection_title = note_title if index == 0 else _derive_subsection_title(block, default=note_title)
        has_table = _looks_like_table_block(block)
        subchunks.append(
            {
                "subsection_title": subsection_title,
                "text": block,
                "has_table_structure": has_table,
                "is_numeric_dense": _is_numeric_dense_text(block),
                "is_sentence_complete": _is_sentence_complete_text(block) or has_table,
            }
        )
    return subchunks


def _split_table_into_groups(table_text: str) -> list[str]:
    lines = [line.strip() for line in table_text.splitlines() if line.strip()]
    if not lines:
        return []

    header_lines = lines[: min(8, len(lines))]
    body_lines = lines[min(8, len(lines)) :]
    if not body_lines:
        return [_clean_chunk_text("\n".join(header_lines))]

    groups: list[list[str]] = []
    current: list[str] = []
    for line in body_lines:
        current.append(line)
        if len(current) >= 14:
            groups.append(current)
            current = []
    if current:
        groups.append(current)

    output = []
    for group in groups:
        chunk_text = _clean_chunk_text("\n".join([*header_lines, *group]))
        if len(chunk_text) >= 120:
            output.append(chunk_text)
    return output or [_clean_chunk_text(table_text)]


def _extract_table_unit(text: str) -> str:
    match = re.search(r"\(in\s+([^)]+)\)", text, re.IGNORECASE)
    return match.group(1).strip() if match else ""


def _extract_fiscal_year_columns(text: str) -> list[str]:
    years = re.findall(r"\b20\d{2}\b", text)
    ordered: list[str] = []
    seen: set[str] = set()
    for year in years:
        if year not in seen:
            seen.add(year)
            ordered.append(year)
    return ordered[:4]


def _extract_nearby_page_number(text: str, start: int) -> str:
    prefix = text[max(0, start - 80) : start + 120]
    match = re.search(r"\b(\d{1,3})\b", prefix)
    return match.group(1) if match else ""


def _contains_toc_noise(text: str) -> bool:
    lowered = text.lower()
    return "table of contents" in lowered or lowered.count("item ") >= 3


def _looks_like_table_block(text: str) -> bool:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 4:
        return False
    numeric_lines = sum(1 for line in lines if re.fullmatch(r"[\$\(\)\d,\.\- ]+", line))
    return numeric_lines >= max(3, len(lines) // 3)


def _is_numeric_dense_text(text: str) -> bool:
    digit_count = sum(char.isdigit() for char in text)
    return digit_count >= 20


def _is_sentence_complete_text(text: str) -> bool:
    stripped = text.strip()
    return stripped.endswith((".", "!", "?", ")", "”")) or _looks_like_table_block(stripped)


def _derive_subsection_title(text: str, *, default: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if _looks_like_subheading(stripped):
            return stripped
    return default


def _infer_related_statement(text: str) -> str:
    lowered = text.lower()
    if "revenue" in lowered or "net income" in lowered or "gross profit" in lowered:
        return "Consolidated Statements of Operations"
    if "cash flow" in lowered or "capital expenditures" in lowered:
        return "Consolidated Statements of Cash Flows"
    if "cash and cash equivalents" in lowered or "inventory" in lowered or "debt" in lowered:
        return "Consolidated Balance Sheets"
    return ""


def _infer_related_line_items(text: str) -> list[str]:
    candidates = [
        "revenue",
        "gross profit",
        "operating income",
        "net income",
        "cash and cash equivalents",
        "capital expenditures",
        "inventory",
        "leases",
        "debt",
        "income taxes",
        "segment information",
    ]
    lowered = text.lower()
    return [item for item in candidates if item in lowered]


def _split_section_into_blocks(section_text: str) -> list[str]:
    cleaned = _clean_chunk_text(section_text)
    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    if not lines:
        return []

    blocks: list[str] = []
    current: list[str] = []
    current_len = 0

    for line in lines:
        is_heading = _looks_like_subheading(line)
        projected = current_len + len(line) + 1
        if current and (is_heading or projected > 900):
            blocks.append(_clean_chunk_text("\n".join(current)))
            current = [line]
            current_len = len(line)
            continue
        current.append(line)
        current_len = projected

    if current:
        blocks.append(_clean_chunk_text("\n".join(current)))

    refined: list[str] = []
    for block in blocks:
        if len(block) <= 1400:
            refined.append(block)
            continue
        refined.extend(_split_long_block(block, limit=1200))

    return [block for block in refined if len(block) >= 80]


def _split_long_block(text: str, *, limit: int) -> list[str]:
    sentences = re.split(r"(?<=[.!?])\s+", text)
    blocks: list[str] = []
    current = ""
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        candidate = f"{current} {sentence}".strip() if current else sentence
        if current and len(candidate) > limit:
            blocks.append(current.strip())
            current = sentence
        else:
            current = candidate
    if current.strip():
        blocks.append(current.strip())
    return blocks


def _clean_chunk_text(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _looks_like_subheading(line: str) -> bool:
    stripped = line.strip()
    if not stripped or len(stripped) > 90:
        return False
    if stripped.upper() == stripped and len(stripped.split()) <= 12:
        return True
    return bool(re.fullmatch(r"[A-Z][A-Za-z0-9&/(),.'\- ]{2,80}", stripped)) and not stripped.endswith(".")


def _classify_item7_subtopic(block: str) -> str:
    lowered = block.lower()
    for name, keywords in ITEM7_SUBTOPICS:
        if any(keyword in lowered for keyword in keywords):
            return name
    return "financial_summary"


def _chunk_type_for_section(section_name: str) -> str:
    if section_name == "Item 1 Business":
        return "business_chunk"
    if section_name == "Item 1A Risk Factors":
        return "risk_chunk"
    if section_name == "Item 7 MD&A":
        return "mdna_chunk"
    if section_name == "Item 8 Financial Statements":
        return "footnote_chunk"
    return "narrative_chunk"


def _make_chunk_id(section_name: str, index: int, *, prefix: str = "chunk") -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", section_name.lower()).strip("_")
    return f"{prefix}_{slug}_{index + 1:02d}"


def _find_next_table_boundary(
    text: str,
    start: int,
    *,
    patterns: tuple[re.Pattern[str], ...] | None = None,
) -> int:
    next_positions: list[int] = []
    for pattern in patterns or TABLE_HEADING_PATTERNS:
        match = pattern.search(text, start)
        if match:
            next_positions.append(match.start())
    end = min(next_positions) if next_positions else min(len(text), start + 2200)
    return end


def _extract_metric_values_from_table_chunks(
    table_chunks: list[FilingChunk],
    reporting_period: str,
) -> dict[str, dict[str, object]]:
    extracted: dict[str, dict[str, object]] = {}
    report_year = _extract_period_from_text(reporting_period) or reporting_period
    table_groups = _group_table_chunks(table_chunks)
    for table_key, table_payload in table_groups.items():
        table_text = str(table_payload["text"])
        representative = table_payload["chunk"]
        fiscal_years = representative.metadata.get("fiscal_year_columns") or []
        for metric, pattern in TABLE_METRIC_PATTERNS.items():
            if metric in extracted:
                continue
            match = pattern.search(table_text)
            if not match:
                continue
            current_value = _parse_table_number(match.group(1))
            row_values = _extract_row_values_from_match(table_text, match)
            previous_value = row_values[1] if len(row_values) > 1 else None
            if previous_value == current_value:
                distinct_following_values = [
                    value for value in row_values[1:] if value is not None and value != current_value
                ]
                if distinct_following_values:
                    previous_value = distinct_following_values[0]
            evidence = _smart_trim(
                table_text[match.start() : min(len(table_text), match.end() + 220)],
                280,
            )
            extracted[metric] = {
                "value": current_value,
                "current_value": current_value,
                "previous_value": previous_value,
                "unit": str(CORE_METRIC_SPECS.get(metric, {}).get("unit", "USD_million")),
                "period": fiscal_years[0] if fiscal_years else report_year,
                "source": representative.section_path,
                "chunk_id": representative.chunk_id,
                "table_name": str(representative.metadata.get("table_title") or representative.subsection_title),
                "evidence": evidence,
            }

    income_payload = (
        _find_table_payload(table_groups, "statements of income")
        or _find_table_payload(table_groups, "statements of operations")
        or _find_table_payload(table_groups, "statements of earnings")
        or _find_table_payload(table_groups, "summary results of operations")
    )
    if income_payload:
        income_text = str(income_payload["text"])
        income_chunk = income_payload["chunk"]
        income_years = income_chunk.metadata.get("fiscal_year_columns") or []
        income_rows = {
            "revenue": (
                r"^revenue$",
                r"^revenues$",
                r"^total revenue$",
                r"^total revenues$",
            ),
            "gross_profit": (r"^gross profit$", r"^gross margin$"),
            "operating_income": (
                r"^income from operations$",
                r"^operating income$",
            ),
            "net_income": (
                r"^net income$",
                r"^net income attributable to .*",
            ),
            "diluted_eps": (
                r"^diluted earnings per share$",
                r"^diluted earnings per share of common stock$",
                r"^diluted eps$",
            ),
        }
        for metric_name, label_patterns in income_rows.items():
            row_hit = _extract_row_series_by_labels(income_text, label_patterns)
            if not row_hit:
                continue
            extracted[metric_name] = _build_metric_entry_from_row_hit(
                metric_name=metric_name,
                row_hit=row_hit,
                representative=income_chunk,
                fiscal_years=income_years,
                fallback_period=report_year,
            )

    balance_payload = _find_table_payload(table_groups, "balance sheets")
    if balance_payload:
        balance_text = str(balance_payload["text"])
        balance_chunk = balance_payload["chunk"]
        balance_years = balance_chunk.metadata.get("fiscal_year_columns") or []
        for metric_name, label_patterns in {
            "cash_and_equivalents": (r"^cash and cash equivalents$",),
            "short_term_investments": (r"^short-term investments$", r"^short term investments$", r"^marketable securities$"),
            "accounts_receivable": (r"^accounts receivable, net$",),
            "inventory": (r"^inventory$",),
        }.items():
            row_hit = _extract_row_series_by_labels(balance_text, label_patterns)
            if row_hit:
                extracted[metric_name] = _build_metric_entry_from_row_hit(
                    metric_name=metric_name,
                    row_hit=row_hit,
                    representative=balance_chunk,
                    fiscal_years=balance_years,
                    fallback_period=report_year,
                )

        current_debt_row = _extract_row_series_by_labels(balance_text, (r"^debt and finance leases$",))
        if not current_debt_row:
            current_debt_row = _extract_row_series_by_labels(
                balance_text,
                (r"^current portion of debt and finance leases$",),
            )
        noncurrent_debt_row = _extract_row_series_by_labels(
            balance_text,
            (r"^debt and finance leases, net of current portion$",),
        )
        if current_debt_row and noncurrent_debt_row:
            current_value = current_debt_row["current_value"] + noncurrent_debt_row["current_value"]
            previous_value = None
            if (
                current_debt_row.get("previous_value") is not None
                and noncurrent_debt_row.get("previous_value") is not None
            ):
                previous_value = current_debt_row["previous_value"] + noncurrent_debt_row["previous_value"]
            extracted["total_debt"] = {
                "value": current_value,
                "current_value": current_value,
                "previous_value": previous_value,
                "unit": "USD_million",
                "period": balance_years[0] if balance_years else report_year,
                "source": balance_chunk.section_path,
                "chunk_id": balance_chunk.chunk_id,
                "table_name": "Debt and finance leases (current + non-current)",
                "evidence": (
                    f"{current_debt_row['row_text']}\n{noncurrent_debt_row['row_text']}"
                ),
            }

    cash_flow_payload = (
        _find_table_payload(table_groups, "statements of cash flows")
        or _find_table_payload(table_groups, "cash flows statements")
        or _find_table_payload(table_groups, "cash flows")
    )
    if cash_flow_payload:
        cash_flow_text = str(cash_flow_payload["text"])
        cash_flow_chunk = cash_flow_payload["chunk"]
        cash_flow_years = cash_flow_chunk.metadata.get("fiscal_year_columns") or []
        for metric_name, label_patterns in {
            "operating_cash_flow": (
                r"^net cash from operations$",
                r"^net cash from operating activities$",
                r"^net cash provided by operations$",
                r"^net cash provided by operating activities$",
            ),
        }.items():
            row_hit = _extract_row_series_by_labels(cash_flow_text, label_patterns)
            if row_hit:
                extracted[metric_name] = _build_metric_entry_from_row_hit(
                    metric_name=metric_name,
                    row_hit=row_hit,
                    representative=cash_flow_chunk,
                    fiscal_years=cash_flow_years,
                    fallback_period=report_year,
                )

    if "gross_profit" in extracted and "revenue" in extracted and "gross_margin" not in extracted:
        revenue = float(extracted["revenue"]["value"] or 0)
        gross_profit = float(extracted["gross_profit"]["value"] or 0)
        previous_revenue = _to_float(extracted["revenue"].get("previous_value"))
        previous_gross_profit = _to_float(extracted["gross_profit"].get("previous_value"))
        if revenue > 0:
            extracted["gross_margin"] = {
                "value": round(gross_profit / revenue, 6),
                "current_value": round(gross_profit / revenue, 6),
                "previous_value": round(previous_gross_profit / previous_revenue, 6)
                if previous_revenue and previous_gross_profit is not None
                else None,
                "unit": "ratio",
                "period": report_year,
                "source": extracted["gross_profit"]["source"],
                "chunk_id": extracted["gross_profit"]["chunk_id"],
                "table_name": extracted["gross_profit"]["table_name"],
                "evidence": (
                    f"Computed from gross_profit={gross_profit} and revenue={revenue} "
                    f"from {extracted['gross_profit']['source']}"
                ),
            }
    if "operating_income" in extracted and "revenue" in extracted and "operating_margin" not in extracted:
        revenue = float(extracted["revenue"]["value"] or 0)
        operating_income = float(extracted["operating_income"]["value"] or 0)
        previous_revenue = _to_float(extracted["revenue"].get("previous_value"))
        previous_operating_income = _to_float(extracted["operating_income"].get("previous_value"))
        if revenue > 0:
            extracted["operating_margin"] = {
                "value": round(operating_income / revenue, 6),
                "current_value": round(operating_income / revenue, 6),
                "previous_value": round(previous_operating_income / previous_revenue, 6)
                if previous_revenue and previous_operating_income is not None
                else None,
                "unit": "ratio",
                "period": report_year,
                "source": extracted["operating_income"]["source"],
                "chunk_id": extracted["operating_income"]["chunk_id"],
                "table_name": extracted["operating_income"]["table_name"],
                "evidence": (
                    f"Computed from operating_income={operating_income} and revenue={revenue} "
                    f"from {extracted['operating_income']['source']}"
                ),
            }
    if "operating_cash_flow" in extracted and "capital_expenditures" in extracted and "free_cash_flow" not in extracted:
        current_ocf = _to_float(extracted["operating_cash_flow"].get("value"))
        current_capex = _to_float(extracted["capital_expenditures"].get("value"))
        previous_ocf = _to_float(extracted["operating_cash_flow"].get("previous_value"))
        previous_capex = _to_float(extracted["capital_expenditures"].get("previous_value"))
        if current_ocf is not None and current_capex is not None:
            extracted["free_cash_flow"] = {
                "value": round(current_ocf - current_capex, 6),
                "current_value": round(current_ocf - current_capex, 6),
                "previous_value": round(previous_ocf - previous_capex, 6)
                if previous_ocf is not None and previous_capex is not None
                else None,
                "unit": "USD_million",
                "period": report_year,
                "source": extracted["operating_cash_flow"]["source"],
                "chunk_id": extracted["operating_cash_flow"]["chunk_id"],
                "table_name": "Derived from operating cash flow and capital expenditures",
                "evidence": (
                    f"Computed from operating_cash_flow={current_ocf} and capital_expenditures={current_capex}."
                ),
            }
    return extracted


def _group_table_chunks(table_chunks: list[FilingChunk]) -> dict[str, dict[str, object]]:
    grouped: dict[str, dict[str, object]] = {}
    for chunk in table_chunks:
        key = str(chunk.metadata.get("table_title") or chunk.section_path)
        payload = grouped.setdefault(key, {"text_parts": [], "chunk": chunk})
        payload["text_parts"].append(_strip_repeated_table_header(chunk.text, first=not payload["text_parts"]))
    return {
        key: {
            "text": "\n".join(value["text_parts"]),
            "chunk": value["chunk"],
        }
        for key, value in grouped.items()
    }


def _find_table_payload(
    table_groups: dict[str, dict[str, object]],
    keyword: str,
) -> dict[str, object] | None:
    lowered_keyword = keyword.lower()
    for table_name, payload in table_groups.items():
        if lowered_keyword in table_name.lower():
            return payload
    return None


def _extract_row_series_by_labels(
    table_text: str,
    label_patterns: tuple[str, ...],
    *,
    max_following_lines: int = 10,
) -> dict[str, object] | None:
    lines = [line.strip() for line in table_text.splitlines() if line.strip()]
    compiled = [re.compile(pattern, re.IGNORECASE) for pattern in label_patterns]
    for index, line in enumerate(lines):
        normalized = re.sub(r"\s+", " ", line).strip()
        if not any(pattern.search(normalized) for pattern in compiled):
            continue
        row_lines = [normalized]
        numeric_started = False
        for next_line in lines[index + 1 : index + 1 + max_following_lines]:
            next_normalized = re.sub(r"\s+", " ", next_line).strip()
            if not next_normalized:
                continue
            if re.search(r"[A-Za-z]", next_normalized) and numeric_started:
                break
            row_lines.append(next_normalized)
            if re.search(r"\d", next_normalized):
                numeric_started = True
        row_text = "\n".join(row_lines)
        values = _extract_ordered_numeric_values(row_text)
        if not values:
            continue
        return {
            "label": normalized,
            "current_value": abs(values[0]),
            "previous_value": abs(values[1]) if len(values) > 1 else None,
            "row_text": row_text,
        }
    return None


def _extract_ordered_numeric_values(text: str) -> list[float]:
    values: list[float] = []
    pattern = re.compile(r"\(\s*(\d[\d,]*(?:\.\d+)?)\s*\)|(\d[\d,]*(?:\.\d+)?)")
    for match in pattern.finditer(text):
        if match.group(1):
            values.append(-_parse_table_number(match.group(1)))
        elif match.group(2):
            values.append(_parse_table_number(match.group(2)))
    cleaned = [value for value in values if not 1900 <= abs(value) <= 2100 or abs(value) >= 2101]
    return cleaned


def _build_metric_entry_from_row_hit(
    *,
    metric_name: str,
    row_hit: dict[str, object],
    representative: FilingChunk,
    fiscal_years: list[str],
    fallback_period: str,
) -> dict[str, object]:
    return {
        "value": row_hit["current_value"],
        "current_value": row_hit["current_value"],
        "previous_value": row_hit.get("previous_value"),
        "unit": str(CORE_METRIC_SPECS.get(metric_name, {}).get("unit", "USD_million")),
        "period": fiscal_years[0] if fiscal_years else fallback_period,
        "source": representative.section_path,
        "chunk_id": representative.chunk_id,
        "table_name": str(representative.metadata.get("table_title") or representative.subsection_title),
        "evidence": _smart_trim(str(row_hit["row_text"]), 280),
    }


def _extract_metric_from_narrative_chunks(
    *,
    metric_name: str,
    narrative_chunks: list[FilingChunk],
    reporting_period: str,
) -> dict[str, object] | None:
    if metric_name == "operating_cash_flow":
        return _extract_operating_cash_flow_from_narrative(narrative_chunks, reporting_period)
    if metric_name == "cash_and_equivalents":
        return _extract_liquidity_total_from_narrative(narrative_chunks, reporting_period)
    return None


def _extract_operating_cash_flow_from_narrative(
    narrative_chunks: list[FilingChunk],
    reporting_period: str,
) -> dict[str, object] | None:
    patterns = (
        re.compile(
            r"cash from operations increased .*? to \$\s*([\d.]+)\s*billion .*? fiscal year 20(\d{2})",
            re.IGNORECASE,
        ),
        re.compile(
            r"net cash from operations\s+([\d,]+)",
            re.IGNORECASE,
        ),
    )
    for chunk in narrative_chunks:
        if not chunk.section_path.startswith("Item 7 MD&A"):
            continue
        normalized = re.sub(r"\s+", " ", chunk.text).strip()
        for pattern in patterns:
            match = pattern.search(normalized)
            if not match:
                continue
            if "billion" in pattern.pattern:
                current_value = float(match.group(1)) * 1000
                current_year = f"20{match.group(2)}"
                previous_match = re.search(
                    r"increased \$\s*([\d.]+)\s*billion to \$\s*([\d.]+)\s*billion",
                    normalized,
                    re.IGNORECASE,
                )
                previous_value = None
                if previous_match:
                    previous_value = round((float(previous_match.group(2)) - float(previous_match.group(1))) * 1000, 3)
            else:
                current_value = _parse_table_number(match.group(1))
                current_year = _extract_period_from_text(reporting_period) or reporting_period
                previous_value = None
            return {
                "value": current_value,
                "current_value": current_value,
                "previous_value": previous_value,
                "unit": "USD_million",
                "period": current_year,
                "source": chunk.section_path,
                "chunk_id": chunk.chunk_id,
                "table_name": "Narrative cash flow disclosure",
                "evidence": _smart_trim(normalized, 280),
            }
    return None


def _extract_liquidity_total_from_narrative(
    narrative_chunks: list[FilingChunk],
    reporting_period: str,
) -> dict[str, object] | None:
    pattern = re.compile(
        r"cash, cash equivalents, and short-term investments totaled \$\s*([\d.]+)\s*billion and \$\s*([\d.]+)\s*billion",
        re.IGNORECASE,
    )
    for chunk in narrative_chunks:
        if "cash, cash equivalents, and short-term investments" not in chunk.text.lower():
            continue
        normalized = re.sub(r"\s+", " ", chunk.text).strip()
        match = pattern.search(normalized)
        if not match:
            continue
        return {
            "value": float(match.group(1)) * 1000,
            "current_value": float(match.group(1)) * 1000,
            "previous_value": float(match.group(2)) * 1000,
            "unit": "USD_million",
            "period": _extract_period_from_text(reporting_period) or reporting_period,
            "source": chunk.section_path,
            "chunk_id": chunk.chunk_id,
            "table_name": "Narrative liquidity disclosure",
            "evidence": _smart_trim(normalized, 280),
        }
    return None


def _strip_repeated_table_header(text: str, *, first: bool) -> str:
    if first:
        return text
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) <= 8:
        return text
    return "\n".join(lines[8:])


def _extract_row_values_from_match(table_text: str, match: re.Match[str]) -> list[float]:
    start = max(0, table_text.rfind("\n", 0, match.start()))
    end = table_text.find("\n", match.end())
    if end == -1:
        end = len(table_text)
    line = table_text[start:end].strip()
    values = re.findall(r"\(?\$?(\d[\d,]*(?:\.\d+)?)\)?", line)
    if len(values) < 2:
        window = table_text[match.start() : min(len(table_text), match.end() + 220)]
        values = re.findall(r"\(?\$?(\d[\d,]*(?:\.\d+)?)\)?", window)
    parsed = [_parse_table_number(value) for value in values]
    if len(parsed) >= 4:
        parsed = [value for value in parsed if not 1900 <= value <= 2100]
    return [value for value in parsed if value is not None]


def _parse_table_number(value: str) -> float:
    cleaned = value.replace(",", "").replace("(", "-").replace(")", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _validate_metric_value(
    *,
    metric: str,
    metric_type: str,
    value: float | None,
    unit: str,
) -> list[str]:
    errors: list[str] = []
    if value is None:
        return ["missing value"]

    if metric_type == "amount":
        if value <= 100:
            errors.append("amount too small to be a reliable core financial metric")
        if 1900 <= value <= 2100 and metric not in {"stock_based_compensation"}:
            errors.append("looks like a year, not an amount")
        if not unit.startswith("USD"):
            errors.append("amount metric is missing normalized USD unit")
    elif metric_type == "ratio":
        if not 0 <= value <= 1:
            errors.append("ratio must be between 0 and 1")
    elif metric_type == "per_share":
        if abs(value) >= 100:
            errors.append("per-share metric is implausibly large")
    elif metric_type == "count":
        if value < 1 or int(value) != value:
            errors.append("count metric must be a positive integer")

    if metric == "revenue" and value < 1_000:
        errors.append("revenue failed sanity check")
    if metric == "operating_cash_flow" and abs(value) < 100:
        errors.append("cash flow failed sanity check")

    return errors


def _find_metric_corroboration(
    *,
    metric_name: str,
    value: float | None,
    narrative_chunks: list[FilingChunk],
) -> tuple[list[str], list[dict[str, object]]]:
    if value is None:
        return [], []

    aliases = {
        "revenue": ("revenue", "revenues", "sales"),
        "gross_profit": ("gross profit",),
        "net_income": ("net income", "net income attributable"),
        "operating_income": ("income from operations", "operating income"),
        "gross_margin": ("gross margin",),
        "operating_margin": ("operating margin",),
        "diluted_eps": ("diluted", "per share"),
        "operating_cash_flow": ("cash flows from operating activities", "operating activities"),
        "cash_and_equivalents": ("cash and cash equivalents",),
        "short_term_investments": ("short-term investments", "marketable securities", "investments"),
        "inventory": ("inventory", "inventories"),
        "accounts_receivable": ("accounts receivable",),
        "total_debt": ("debt", "borrowings"),
        "lease_liabilities": ("lease liabilities", "leases"),
        "capital_expenditures": ("capital expenditures", "capex"),
        "free_cash_flow": ("free cash flow",),
        "stock_based_compensation": ("stock-based compensation", "share-based compensation"),
    }
    numeric_tokens = _metric_numeric_tokens(metric_name, value)
    sources: list[str] = []
    evidence: list[dict[str, object]] = []
    for chunk in narrative_chunks:
        lowered = chunk.text.lower()
        if not any(alias in lowered for alias in aliases.get(metric_name, ())):
            continue
        if not any(token in lowered for token in numeric_tokens):
            continue
        sources.append(chunk.section_path)
        evidence.append(
            {
                "chunk_id": chunk.chunk_id,
                "section_path": chunk.section_path,
                "text": _smart_trim(chunk.text, 220),
            }
        )
        if len(sources) >= 1:
            break
    return sources, evidence


def _metric_numeric_tokens(metric_name: str, value: float) -> list[str]:
    tokens = {str(int(value)) if float(value).is_integer() else f"{value:.2f}".rstrip("0").rstrip(".")}
    if metric_name in {
        "revenue",
        "gross_profit",
        "net_income",
        "operating_income",
        "operating_cash_flow",
        "cash_and_equivalents",
        "short_term_investments",
        "inventory",
        "accounts_receivable",
        "total_debt",
        "lease_liabilities",
        "capital_expenditures",
        "free_cash_flow",
        "stock_based_compensation",
    }:
        tokens.add(f"{value:,.0f}")
        tokens.add(f"{value/1000:.2f}".rstrip("0").rstrip("."))
        tokens.add(f"{value/1000:.2f} billion".lower())
    elif metric_name in {"gross_margin", "operating_margin"}:
        percent_value = value * 100
        tokens.add(f"{percent_value:.1f}".rstrip("0").rstrip("."))
        tokens.add(f"{percent_value:.1f}%".lower())
    elif metric_name == "diluted_eps":
        tokens.add(f"{value:.2f}".rstrip("0").rstrip("."))
    return [token.lower() for token in tokens if token]


def _format_metric_record(record: MetricRecord) -> str:
    value_text = "missing"
    if record.value is not None:
        value_text = str(record.value)
    return (
        f"{record.metric}: value={value_text}, unit={record.unit}, period={record.period}, "
        f"confidence={record.confidence}, source={record.source}"
    )


def _estimate_token_count(text: str) -> int:
    return max(1, len(text.split()))


def _importance_score_for_chunk(section_path: str, chunk_type: str) -> float:
    if chunk_type in {"risk_chunk", "table_chunk", "note_table_chunk"}:
        return 0.95
    if section_path.startswith("Item 7 MD&A/financial_summary") or section_path.startswith("Item 7 MD&A/cashflow_capex"):
        return 0.9
    if chunk_type in {"business_chunk", "mdna_chunk", "outlook_chunk"}:
        return 0.8
    return 0.65


def _extract_year_int(text: str) -> int | None:
    year = _extract_period_from_text(text)
    return int(year) if year else None


def _extract_business_summary(text: str, max_chars: int = 420) -> str:
    if not text:
        return ""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    content_lines = [
        line
        for line in lines
        if not re.match(r"^item\s+1\b", line, re.IGNORECASE)
        and line.lower() not in {"our company", "overview"}
        and not _looks_like_toc_line(line)
        and "table of contents" not in line.lower()
    ]
    joined = " ".join(content_lines).strip()
    if not joined:
        return ""
    summary = joined
    if summary.upper().startswith("GENERAL "):
        summary = summary[8:].strip()
    first_sentence_break = re.search(r"(?<=[.!?])\s+", summary)
    if first_sentence_break:
        first_part = summary[: first_sentence_break.end()].strip()
        second_part = summary[first_sentence_break.end() :].strip()
        summary = first_part
        if second_part:
            summary = f"{summary} {second_part}"
    if not summary:
        return ""
    if len(summary) <= max_chars:
        return summary
    return summary[: max_chars - 3].rstrip() + "..."


def _extract_focus_sentences(text: str, *, keywords: tuple[str, ...], max_chars: int = 420) -> str:
    if not text:
        return ""
    normalized = re.sub(r"\s+", " ", text).strip()
    if not normalized:
        return ""
    sentences = re.split(r"(?<=[.!?])\s+", normalized)
    selected: list[str] = []
    for sentence in sentences:
        lowered = sentence.lower()
        if "table of contents" in lowered:
            continue
        if _looks_like_toc_line(sentence):
            continue
        if any(keyword in lowered for keyword in keywords):
            selected.append(sentence.strip())
        if len(" ".join(selected)) >= max_chars:
            break
    if not selected:
        return ""
    summary = " ".join(selected).strip()
    if len(summary) <= max_chars:
        return summary
    return summary[: max_chars - 3].rstrip() + "..."


def _extract_segment_summary(text: str, max_chars: int = 700) -> str:
    if not text:
        return ""
    normalized = re.sub(r"\s+", " ", text).strip()
    sentences = [sentence.strip() for sentence in re.split(r"(?<=[.!?])\s+", normalized) if sentence.strip()]
    priority_groups = (
        (
            "we operate our business and report our financial performance using",
            "we report financial results for",
            "reportable segments",
            "segments:",
        ),
        (
            "generate substantially all of our revenue",
            "advertising placements",
            "commercial cloud",
            "products and cloud services",
            "family of apps",
            "reality labs",
        ),
        ("costs and expenses were recognized", "operate at a loss"),
    )
    selected: list[str] = []
    for group in priority_groups:
        for sentence in sentences:
            lowered = sentence.lower()
            if "table of contents" in lowered:
                continue
            if _looks_like_toc_line(sentence):
                continue
            if any(keyword in lowered for keyword in group) and sentence not in selected:
                selected.append(sentence)
                break
    if not selected:
        return ""
    summary = " ".join(selected).strip()
    if len(summary) <= max_chars:
        return summary
    return summary[: max_chars - 3].rstrip() + "..."


def _build_company_profile(
    *,
    document: ParsedDocument,
    section_snippets: dict[str, str],
) -> CompanyProfile:
    business_text = section_snippets.get("Item 1 Business", "")
    mdna_text = section_snippets.get("Item 7 MD&A", "")
    lowered = business_text.lower()
    business_summary = _extract_business_summary(business_text)
    monetization_summary = _extract_focus_sentences(
        "\n".join([business_text, mdna_text]),
        keywords=(
            "generate substantially all of our revenue",
            "generate revenue from",
            "generate revenue by",
            "revenue increased",
            "products and cloud services",
            "commercial cloud",
            "subscription revenue",
            "search and news advertising",
            "office commercial",
            "azure",
            "selling advertising placements",
            "subscription revenue",
            "license revenue",
            "services revenue",
            "advertising revenue",
            "family of apps",
            "reality labs",
        ),
        max_chars=420,
    )
    segment_summary = _extract_segment_summary("\n".join([business_text, mdna_text]), max_chars=700)
    if not segment_summary:
        normalized_document_text = re.sub(r"\s+", " ", document.text)
        explicit_segment_match = re.search(
            r"We operate our business and report our financial performance using three segments: .*?More Personal Computing\.",
            normalized_document_text,
            re.IGNORECASE,
        )
        if explicit_segment_match:
            segment_summary = explicit_segment_match.group(0).strip()
    if not segment_summary:
        segment_summary = _extract_focus_sentences(
            document.text,
            keywords=(
                "we operate our business and report our financial performance using three segments",
                "productivity and business processes",
                "intelligent cloud",
                "more personal computing",
                "family of apps",
                "reality labs",
            ),
            max_chars=520,
        )
    fields: dict[str, list[str]] = {}
    for field_name, options in COMPANY_PROFILE_RULES.items():
        values: list[str] = []
        for label, keywords in options:
            hit_count = sum(1 for keyword in keywords if keyword in lowered)
            min_hits = 1
            if field_name == "segments":
                min_hits = 2
            if field_name == "major_products":
                min_hits = 2
            elif field_name == "manufacturing_regions":
                min_hits = 2
            if hit_count >= min_hits:
                values.append(label)
        fields[field_name] = values[:12]
    return CompanyProfile(
        company_name=document.company_name,
        reporting_period=document.reporting_period,
        business_summary=business_summary,
        monetization_summary=monetization_summary,
        segment_summary=segment_summary,
        segments=fields.get("segments", [])[:4],
        major_products=fields.get("major_products", [])[:6],
        manufacturing_regions=fields.get("manufacturing_regions", [])[:4],
        strategic_themes=fields.get("strategic_themes", [])[:5],
    )


def _build_financial_snapshot(
    metric_records: list[MetricRecord],
) -> dict[str, dict[str, object]]:
    snapshot: dict[str, dict[str, object]] = {}
    valid_map = {record.metric: record for record in metric_records if record.valid}
    for metric_name, spec in CORE_METRIC_SPECS.items():
        record = valid_map.get(metric_name)
        if not record:
            continue
        canonical = record.canonical_numeric_source or {}
        current_value = _to_float(canonical.get("current_value"))
        previous_value = _to_float(canonical.get("previous_value"))
        yoy_change_value = None
        yoy_change_pct = None
        if current_value is not None and previous_value is not None:
            yoy_change_value = round(current_value - previous_value, 6)
            if previous_value:
                yoy_change_pct = round(((current_value - previous_value) / previous_value) * 100, 4)
        snapshot[metric_name] = {
            "metric_name": metric_name,
            "metric_type": spec["metric_type"],
            "value": record.value,
            "current_value": current_value,
            "previous_value": previous_value,
            "unit": record.unit,
            "period": record.period,
            "yoy_change_value": yoy_change_value,
            "yoy_change_pct": yoy_change_pct,
            "canonical_source_chunk_id": record.canonical_source_chunk_id,
            "canonical_source_table_name": record.canonical_source_table_name,
        }

    ocf = snapshot.get("operating_cash_flow")
    capex = snapshot.get("capital_expenditures")
    if ocf and capex and "free_cash_flow" not in snapshot:
        current_ocf = _to_float(ocf.get("value"))
        current_capex = _to_float(capex.get("value"))
        prev_ocf = None
        prev_capex = None
        if current_ocf is not None and _to_float(ocf.get("yoy_change_value")) is not None:
            prev_ocf = current_ocf - _to_float(ocf.get("yoy_change_value"))
        if current_capex is not None and _to_float(capex.get("yoy_change_value")) is not None:
            prev_capex = current_capex - _to_float(capex.get("yoy_change_value"))
        fcf_value = None if current_ocf is None or current_capex is None else round(current_ocf - current_capex, 6)
        prev_fcf = None if prev_ocf is None or prev_capex is None else round(prev_ocf - prev_capex, 6)
        yoy_change_value = None
        yoy_change_pct = None
        if fcf_value is not None and prev_fcf is not None:
            yoy_change_value = round(fcf_value - prev_fcf, 6)
            if prev_fcf:
                yoy_change_pct = round(((fcf_value - prev_fcf) / prev_fcf) * 100, 4)
        snapshot["free_cash_flow"] = {
            "metric_name": "free_cash_flow",
            "metric_type": "amount",
            "value": fcf_value,
            "current_value": fcf_value,
            "previous_value": prev_fcf,
            "unit": "USD_million",
            "period": str(ocf.get("period") or capex.get("period") or ""),
            "yoy_change_value": yoy_change_value,
            "yoy_change_pct": yoy_change_pct,
            "canonical_source_chunk_id": str(ocf.get("canonical_source_chunk_id") or capex.get("canonical_source_chunk_id") or ""),
            "canonical_source_table_name": "Derived from operating cash flow and capital expenditures",
        }
    return snapshot


def _build_key_explanations(narrative_chunks: list[FilingChunk]) -> list[SummaryCard]:
    candidates: list[SummaryCard] = []
    for chunk in narrative_chunks:
        if not chunk.section_path.startswith("Item 7 MD&A/"):
            continue
        lowered = chunk.text.lower()
        if any(keyword in lowered for keyword in ACCOUNTING_POLICY_KEYWORDS):
            if "valuation allowance" not in lowered and "deferred tax" not in lowered:
                continue
        if not _is_high_value_explanation_chunk(chunk.text):
            continue
        linked_metrics = _infer_linked_metrics(chunk.text)
        explanation_type = _classify_explanation_type(chunk.text, linked_metrics)
        topic = _classify_explanation_topic(chunk.text, linked_metrics, explanation_type)
        summary = _compress_text_for_summary(chunk.text, max_chars=340)
        if topic == "accounting_comparability":
            summary = _extract_keyword_focused_summary(
                chunk.text,
                keywords=("valuation allowance", "deferred tax", "net income"),
                max_chars=260,
            )
        elif topic == "capex_change":
            summary = _extract_keyword_focused_summary(
                chunk.text,
                keywords=("capital expenditures", "capex"),
                max_chars=260,
            )
        elif topic == "cash_flow_change":
            summary = _extract_keyword_focused_summary(
                chunk.text,
                keywords=("cash flows provided by operating activities", "cash flow", "compared to", "increase"),
                max_chars=260,
            )
        elif topic == "demand_pricing":
            summary = _extract_keyword_focused_summary(
                chunk.text,
                keywords=("pricing", "order rate", "interest rates", "consumer spending", "operating margin"),
                max_chars=260,
            )
        if not summary:
            continue
        candidates.append(
            SummaryCard(
                topic=topic,
                summary=summary,
                source_chunk_id=chunk.chunk_id,
                importance=_importance_label(chunk.importance_score),
                linked_metrics=linked_metrics,
                explanation_type=explanation_type,
                why_it_matters=_why_it_matters_for_topic(topic),
            )
        )
    return _dedupe_summary_cards(candidates, limit=14)


def _build_key_risks(narrative_chunks: list[FilingChunk]) -> list[RiskCard]:
    candidates: list[RiskCard] = []
    for chunk in narrative_chunks:
        if chunk.chunk_type != "risk_chunk":
            continue
        lowered = chunk.text.lower()
        if "you should carefully consider the risks described below" in lowered:
            continue
        risk_name, impact_area = _classify_risk(chunk.text)
        if risk_name == "general operating risk":
            continue
        summary = RISK_SUMMARY_TEMPLATES.get(
            risk_name,
            _compress_text_for_summary(chunk.text, max_chars=220),
        )
        candidates.append(
            RiskCard(
                risk_name=risk_name,
                short_summary=summary,
                impact_area=impact_area,
                source_chunk_id=chunk.chunk_id,
                importance=_importance_label(chunk.importance_score),
                severity=_importance_label(chunk.importance_score),
            )
        )
    return _dedupe_risk_cards(candidates, limit=8)


def _build_accounting_flags(
    *,
    note_chunks: list[FilingChunk],
    narrative_chunks: list[FilingChunk],
) -> list[SummaryCard]:
    candidates: list[SummaryCard] = []
    deferred_tax_chunk = next(
        (
            chunk
            for chunk in narrative_chunks
            if "valuation allowance" in chunk.text.lower() or "deferred tax" in chunk.text.lower()
        ),
        None,
    )
    if deferred_tax_chunk:
        candidates.append(
            SummaryCard(
                topic="Deferred tax comparability effect",
                summary=_compress_text_for_summary(deferred_tax_chunk.text, max_chars=240),
                source_chunk_id=deferred_tax_chunk.chunk_id,
                importance="high",
                linked_metrics=["net_income"],
                flag_type="comparability",
                why_it_matters="A large deferred tax adjustment can make year-over-year net income comparisons misleading.",
            )
        )
    for chunk in note_chunks:
        lowered = chunk.text.lower()
        note_scope = " ".join(
            [
                str(chunk.metadata.get("note_title") or ""),
                str(chunk.subsection_title or ""),
                lowered[:400],
            ]
        ).lower()
        if "lawsuit" in lowered or "litigation" in lowered:
            continue
        label = ""
        for flag_name, keywords in ACCOUNTING_FLAG_RULES:
            if any(keyword in lowered for keyword in keywords):
                label = flag_name
                break
        if not label:
            continue
        if label == "Debt and leases" and not any(
            keyword in note_scope
            for keyword in ("lease", "leases", "debt", "borrowings", "convertible")
        ):
            continue
        if label == "Revenue recognition" and not any(
            keyword in note_scope
            for keyword in (
                "revenue recognition",
                "recognized over time",
                "recognized at a point in time",
                "performance obligation",
                "advertising placements",
            )
        ):
            continue
        if label == "Income tax comparability" and not any(
            keyword in note_scope
            for keyword in ("deferred tax", "valuation allowance", "income taxes")
        ):
            continue
        summary = _build_accounting_flag_summary(chunk, label)
        if len(summary) < 40:
            continue
        flag_topic, flag_type, why_it_matters = _classify_accounting_flag(chunk, label)
        candidates.append(
            SummaryCard(
                topic=flag_topic,
                summary=summary,
                source_chunk_id=chunk.chunk_id,
                importance=_importance_label(chunk.importance_score),
                linked_metrics=_infer_linked_metrics(chunk.text),
                flag_type=flag_type,
                why_it_matters=why_it_matters,
            )
        )
    return _dedupe_summary_cards_by_topic(candidates, limit=6)


def _build_outlook_signals(narrative_chunks: list[FilingChunk]) -> list[SummaryCard]:
    candidates: list[SummaryCard] = []
    outlook_markers = (
        "we expect",
        "we currently expect",
        "we plan",
        "guidance",
        "outlook",
        "following fiscal",
        "following two fiscal years",
        "capital expenditures in 2025",
        "capital expenditure in 2025",
        "will continue",
    )
    for chunk in narrative_chunks:
        lowered = chunk.text.lower()
        if not chunk.section_path.startswith("Item 7 MD&A/"):
            continue
        if not any(marker in lowered for marker in outlook_markers):
            continue
        summary = _extract_keyword_focused_summary(
            chunk.text,
            keywords=("we expect", "we plan", "2025", "following two fiscal years", "will continue"),
            max_chars=240,
        )
        if len(summary) < 40:
            continue
        guidance_type = _classify_guidance_type(chunk.text)
        candidates.append(
            SummaryCard(
                topic=_classify_explanation_topic(chunk.text, _infer_linked_metrics(chunk.text), "guidance"),
                summary=summary,
                source_chunk_id=chunk.chunk_id,
                importance=_importance_label(max(0.82, chunk.importance_score)),
                linked_metrics=_infer_linked_metrics(chunk.text),
                explanation_type="guidance",
                why_it_matters="Forward-looking guidance shapes expectations for future revenue, margin, capex, or cash needs.",
                time_horizon=_extract_time_horizon(chunk.text),
                guidance_type=guidance_type,
                certainty_level=_classify_certainty_level(chunk.text),
            )
        )
    return _dedupe_summary_cards_by_topic(candidates, limit=6)


def _build_investor_summary_input(
    *,
    company_profile: CompanyProfile,
    financial_snapshot: dict[str, dict[str, object]],
    key_explanations: list[SummaryCard],
    key_risks: list[RiskCard],
    accounting_flags: list[SummaryCard],
    outlook_signals: list[SummaryCard],
) -> dict[str, object]:
    return {
        "company_profile": {
            "company_name": company_profile.company_name,
            "reporting_period": company_profile.reporting_period,
            "segments": company_profile.segments,
            "major_products": company_profile.major_products,
            "manufacturing_regions": company_profile.manufacturing_regions,
            "strategic_themes": company_profile.strategic_themes,
        },
        "financial_snapshot": financial_snapshot,
        "key_explanations": [_summary_card_to_dict(card) for card in key_explanations],
        "key_risks": [_risk_card_to_dict(card) for card in key_risks],
        "accounting_flags": [_summary_card_to_dict(card) for card in accounting_flags],
        "outlook_signals": [_summary_card_to_dict(card) for card in outlook_signals],
    }


def _build_investor_summary_layer(
    *,
    metric_records: list[MetricRecord],
    narrative_chunks: list[FilingChunk],
    note_chunks: list[FilingChunk],
) -> dict[str, object]:
    valid_metrics = [record.metric for record in metric_records if record.valid]
    return {
        "Financial Performance": {
            "metrics": [metric for metric in valid_metrics if metric in {"revenue", "net_income", "operating_income"}],
            "chunk_ids": [chunk.chunk_id for chunk in narrative_chunks if chunk.section_path.startswith("Item 7 MD&A/financial_summary")][:6],
        },
        "Margin and Profitability": {
            "metrics": [metric for metric in valid_metrics if metric in {"gross_margin", "diluted_eps"}],
            "chunk_ids": [chunk.chunk_id for chunk in note_chunks if "revenue" in chunk.text.lower() or "margin" in chunk.text.lower()][:6],
        },
        "Cash Flow and Capex": {
            "metrics": [metric for metric in valid_metrics if metric in {"operating_cash_flow", "capital_expenditures"}],
            "chunk_ids": [chunk.chunk_id for chunk in narrative_chunks if chunk.section_path.startswith("Item 7 MD&A/cashflow_capex")][:6],
        },
        "Balance Sheet and Liquidity": {
            "metrics": [metric for metric in valid_metrics if metric in {"cash_and_equivalents"}],
            "chunk_ids": [chunk.chunk_id for chunk in note_chunks if "cash" in chunk.text.lower() or "liquidity" in chunk.text.lower()][:6],
        },
        "Management Explanation": {
            "chunk_ids": [chunk.chunk_id for chunk in narrative_chunks if chunk.section_path.startswith("Item 7 MD&A/")][:10],
        },
        "Key Risks": {
            "chunk_ids": [chunk.chunk_id for chunk in narrative_chunks if chunk.chunk_type == "risk_chunk"][:8],
        },
        "One-off / Accounting Effects": {
            "chunk_ids": [chunk.chunk_id for chunk in note_chunks if "accounting" in chunk.text.lower() or "digital assets" in chunk.text.lower()][:8],
        },
    }


def _summary_card_to_dict(card: SummaryCard) -> dict[str, object]:
    return {
        "topic": card.topic,
        "summary": card.summary,
        "source_chunk_id": card.source_chunk_id,
        "importance": card.importance,
        "linked_metrics": card.linked_metrics,
        "explanation_type": card.explanation_type,
        "flag_type": card.flag_type,
        "why_it_matters": card.why_it_matters,
        "time_horizon": card.time_horizon,
        "guidance_type": card.guidance_type,
        "certainty_level": card.certainty_level,
    }


def _risk_card_to_dict(card: RiskCard) -> dict[str, object]:
    return {
        "risk_name": card.risk_name,
        "short_summary": card.short_summary,
        "impact_area": card.impact_area,
        "source_chunk_id": card.source_chunk_id,
        "importance": card.importance,
        "severity": card.severity,
    }


def _compress_text_for_summary(text: str, *, max_chars: int) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned:
        return ""
    sentences = re.split(r"(?<=[.!?])\s+", cleaned)
    selected: list[str] = []
    length = 0
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        if _is_low_signal_sentence(sentence):
            continue
        if selected and length + len(sentence) + 1 > max_chars:
            break
        selected.append(sentence)
        length += len(sentence) + 1
        if len(selected) >= 2:
            break
    summary = " ".join(selected).strip()
    if not summary:
        summary = cleaned[:max_chars].rstrip()
    if len(summary) > max_chars:
        summary = summary[: max_chars - 3].rstrip() + "..."
    return summary


def _extract_keyword_focused_summary(
    text: str,
    *,
    keywords: tuple[str, ...],
    max_chars: int,
) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    sentences = re.split(r"(?<=[.!?])\s+", cleaned)
    selected = [
        sentence.strip()
        for sentence in sentences
        if sentence.strip()
        and any(keyword in sentence.lower() for keyword in keywords)
        and not _is_low_signal_sentence(sentence)
    ]
    if not selected:
        return _compress_text_for_summary(text, max_chars=max_chars)
    summary = " ".join(selected[:2]).strip()
    if len(summary) > max_chars:
        summary = summary[: max_chars - 3].rstrip() + "..."
    return summary


def _classify_summary_topic(text: str, rules: tuple[tuple[str, tuple[str, ...]], ...], *, fallback: str) -> str:
    lowered = text.lower()
    for label, keywords in rules:
        if any(keyword in lowered for keyword in keywords):
            return label
    return fallback


def _is_high_value_explanation_chunk(text: str) -> bool:
    lowered = text.lower()
    if not any(keyword in lowered for keyword in EXPLANATION_SIGNAL_KEYWORDS):
        return False
    if any(
        keyword in lowered
        for keyword in (
            "for a full description",
            "non-gaap measure",
            "non-gaap measures",
            "constant currency basis",
        )
    ):
        return False
    if any(keyword in lowered for keyword in ("revenue recognition", "asc 606", "performance obligation", "lessor perspective")):
        return False
    return True


def _classify_explanation_type(text: str, linked_metrics: list[str]) -> str:
    lowered = text.lower()
    if any(keyword in lowered for keyword in ("valuation allowance", "deferred tax", "comparability")):
        return "accounting"
    if any(keyword in lowered for keyword in ("interest rates", "consumer spending", "tariff", "macroeconomic", "industry trends")):
        return "macro"
    if any(keyword in lowered for keyword in ("expect", "plan", "guidance", "outlook", "future")):
        return "guidance"
    if any(metric in {"operating_cash_flow", "capital_expenditures", "cash_and_equivalents"} for metric in linked_metrics):
        return "capital_allocation"
    return "operational"


def _classify_explanation_topic(text: str, linked_metrics: list[str], explanation_type: str) -> str:
    lowered = text.lower()
    if explanation_type == "guidance":
        return "management_outlook"
    if explanation_type == "accounting":
        return "accounting_comparability"
    if "pricing" in lowered or "demand" in lowered or "order rate" in lowered:
        return "demand_pricing"
    if "production" in lowered or "factory" in lowered or "supply chain" in lowered:
        return "production_supply"
    if "capital expenditures" in lowered or "capex" in lowered:
        return "capex_change"
    if any(metric == "operating_cash_flow" for metric in linked_metrics):
        return "cash_flow_change"
    if any(metric == "net_income" for metric in linked_metrics):
        return "net_income_change"
    if any(metric in {"gross_margin", "operating_margin", "gross_profit", "operating_income"} for metric in linked_metrics):
        return "margin_change"
    if any(metric == "revenue" for metric in linked_metrics):
        return "revenue_change"
    return "management_explanation"


def _why_it_matters_for_topic(topic: str) -> str:
    mapping = {
        "revenue_change": "Revenue changes usually drive the top-line narrative and help explain demand strength or weakness.",
        "margin_change": "Margin changes explain how efficiently the company is converting sales into profit.",
        "net_income_change": "Net income changes shape bottom-line comparability and investor perception of earnings quality.",
        "cash_flow_change": "Cash flow changes matter because they affect self-funding capacity and balance-sheet flexibility.",
        "capex_change": "Capex changes indicate how much the company is spending to expand capacity or support future growth.",
        "accounting_comparability": "Accounting comparability items can distort year-over-year comparisons if investors read headline profit alone.",
        "demand_pricing": "Demand and pricing commentary helps explain whether revenue or margin pressure is cyclical or company-specific.",
        "production_supply": "Production and supply commentary matters when delivery growth depends on factories, suppliers, and ramp execution.",
        "management_outlook": "Forward-looking management commentary sets expectations for future growth, spending, and risk.",
    }
    return mapping.get(topic, "This explanation helps connect reported numbers with management's stated drivers.")


def _infer_linked_metrics(text: str) -> list[str]:
    lowered = text.lower()
    mapping = (
        ("revenue", ("revenue", "sales", "deliveries", "deployment")),
        ("gross_profit", ("gross profit",)),
        ("gross_margin", ("gross margin", "margin")),
        ("operating_income", ("operating income", "income from operations")),
        ("net_income", ("net income", "earnings", "tax")),
        ("operating_cash_flow", ("cash flow", "operating activities", "working capital")),
        ("capital_expenditures", ("capex", "capital expenditures", "property and equipment")),
        ("cash_and_equivalents", ("cash and cash equivalents", "liquidity")),
        ("stock_based_compensation", ("stock-based compensation", "share-based compensation")),
    )
    linked = [metric for metric, keywords in mapping if any(keyword in lowered for keyword in keywords)]
    return linked[:4]


def _classify_risk(text: str) -> tuple[str, list[str]]:
    lowered = text.lower()
    for risk_name, keywords, impact_area in RISK_RULES:
        if any(keyword in lowered for keyword in keywords):
            return risk_name, impact_area
    return "general operating risk", _infer_linked_metrics(text)[:2]


def _build_accounting_flag_summary(chunk: FilingChunk, label: str) -> str:
    lowered = chunk.text.lower()
    note_title = str(chunk.metadata.get("note_title") or chunk.subsection_title or "").strip()
    if "valuation allowance" in lowered or "deferred tax" in lowered:
        return "A deferred tax valuation allowance release can materially distort year-over-year net income comparability."
    if label == "Income tax comparability":
        return "Income tax items, including deferred tax effects, can materially distort year-over-year net income comparability."
    if label == "Revenue recognition":
        return "Some revenue is recognized over time for software, services, or related obligations rather than entirely at delivery."
    if label == "Inventory valuation":
        return "Inventory carrying values depend on expected selling economics and can create write-down risk when pricing or demand weakens."
    if label == "Warranty reserve":
        return "Warranty reserves rely on management estimates and can shift reported cost of revenue and gross margin."
    if label == "Stock-based compensation":
        return "Stock-based compensation is a sizable non-cash expense that affects operating expense comparability and cash flow reconciliation."
    if label == "Segment information":
        return "Segment disclosures are limited, so investors cannot fully assess segment assets, liabilities, or profitability detail."
    if label == "Debt and leases":
        if "lease" in note_title.lower() or "lease" in lowered:
            return "Lease accounting affects fixed obligations and can change how investors interpret long-term operating commitments."
        return "Debt and finance lease disclosures affect how investors interpret leverage, fixed commitments, and future cash obligations."
    if label == "Cash and investments":
        return "Cash and investment classifications matter because liquidity may be split across cash, investments, and other near-cash balances."
    return _build_note_summary_fallback(chunk, label)


def _classify_accounting_flag(chunk: FilingChunk, label: str) -> tuple[str, str, str]:
    lowered = chunk.text.lower()
    note_title = str(chunk.metadata.get("note_title") or chunk.subsection_title or "").strip()
    if "valuation allowance" in lowered or "deferred tax" in lowered:
        return (
            "Deferred tax comparability effect",
            "comparability",
            "A large tax adjustment can make current earnings look better or worse than underlying operations.",
        )
    if label == "Income tax comparability":
        return (
            "Deferred tax comparability effect",
            "comparability",
            "Income tax adjustments can distort period-to-period earnings comparisons even when underlying operations move differently.",
        )
    if label == "Revenue recognition":
        return (
            "Revenue recognition timing",
            "recognition_timing",
            "Timing rules can shift revenue across periods, so investors should separate demand from accounting timing.",
        )
    if label == "Inventory valuation":
        return (
            "Inventory valuation sensitivity",
            "estimate_sensitivity",
            "Inventory write-downs can affect both gross margin and the interpretation of demand conditions.",
        )
    if label == "Warranty reserve":
        return (
            "Warranty reserve sensitivity",
            "estimate_sensitivity",
            "Warranty estimates directly affect cost of revenue and can change margin quality.",
        )
    if label == "Stock-based compensation":
        return (
            "Stock-based compensation treatment",
            "non_cash_expense",
            "SBC is non-cash but still economically relevant, so it matters for cost comparability and dilution awareness.",
        )
    if label == "Segment information":
        return (
            "Segment reporting limitation",
            "disclosure_limit",
            "Limited segment detail constrains how precisely investors can attribute revenue or profit drivers.",
        )
    if label == "Debt and leases" and ("lease" in note_title.lower() or "lease" in lowered):
        return (
            "Lease accounting effects",
            "fixed_commitment",
            "Lease accounting affects fixed obligations and future cash commitments that do not always show up in headline debt.",
        )
    return (
        "Debt and finance lease structure",
        "balance_sheet_structure",
        "Debt structure affects leverage, refinancing needs, and the interpretation of fixed financing obligations.",
    )


def _classify_guidance_type(text: str) -> str:
    lowered = text.lower()
    if "capital expenditures" in lowered or "capex" in lowered:
        return "capex"
    if "production" in lowered or "factory" in lowered or "ramp" in lowered:
        return "production"
    if "pricing" in lowered or "demand" in lowered or "sales" in lowered:
        return "demand"
    if "autonomy" in lowered or "ai" in lowered or "robotaxi" in lowered:
        return "strategy"
    return "general"


def _extract_time_horizon(text: str) -> str:
    lowered = text.lower()
    if "2025" in lowered:
        return "2025"
    if "following two fiscal years" in lowered:
        return "next 2-3 years"
    if "upcoming periods" in lowered or "future" in lowered:
        return "upcoming periods"
    return "not specified"


def _classify_certainty_level(text: str) -> str:
    lowered = text.lower()
    if "currently expect" in lowered or "we expect" in lowered or "guidance" in lowered:
        return "high"
    if "will continue" in lowered or "plan" in lowered:
        return "medium"
    return "low"


def _importance_label(score: float) -> str:
    if score >= 0.9:
        return "high"
    if score <= 0.68:
        return "low"
    return "medium"


def _dedupe_summary_cards(cards: list[SummaryCard], *, limit: int) -> list[SummaryCard]:
    deduped: list[SummaryCard] = []
    seen: set[tuple[str, str]] = set()
    sorted_cards = sorted(cards, key=lambda card: (card.importance != "high", len(card.summary)))
    for card in sorted_cards:
        key = (card.topic.strip().lower(), card.summary.strip().lower())
        if not card.summary or key in seen:
            continue
        seen.add(key)
        deduped.append(card)
        if len(deduped) >= limit:
            break
    return deduped


def _dedupe_summary_cards_by_topic(cards: list[SummaryCard], *, limit: int) -> list[SummaryCard]:
    deduped: list[SummaryCard] = []
    seen_topics: set[str] = set()
    certainty_rank = {"high": 0, "medium": 1, "low": 2, "": 3}
    horizon_rank = {"2025": 0, "next 2-3 years": 1, "upcoming periods": 2, "not specified": 3, "": 4}
    sorted_cards = sorted(
        cards,
        key=lambda card: (
            card.importance != "high",
            certainty_rank.get(card.certainty_level, 3),
            horizon_rank.get(card.time_horizon, 4),
            len(card.summary),
        ),
    )
    for card in sorted_cards:
        topic = card.topic.strip().lower()
        if not topic or topic in seen_topics or not card.summary:
            continue
        seen_topics.add(topic)
        deduped.append(card)
        if len(deduped) >= limit:
            break
    return deduped


def _dedupe_risk_cards(cards: list[RiskCard], *, limit: int) -> list[RiskCard]:
    deduped: list[RiskCard] = []
    seen_risk_names: set[str] = set()
    for card in cards:
        key = card.risk_name.strip().lower()
        if not card.short_summary or key in seen_risk_names:
            continue
        seen_risk_names.add(key)
        deduped.append(card)
        if len(deduped) >= limit:
            break
    return deduped


def _to_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace(",", "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _is_low_signal_sentence(sentence: str) -> bool:
    stripped = sentence.strip(" .,:;()-")
    if len(stripped) < 20:
        return True
    alpha_count = sum(char.isalpha() for char in stripped)
    digit_count = sum(char.isdigit() for char in stripped)
    if alpha_count < 8:
        return True
    if digit_count > alpha_count and alpha_count < 16:
        return True
    return False


def _needs_note_summary_fallback(summary: str) -> bool:
    stripped = summary.strip()
    if not stripped:
        return True
    lowered = stripped.lower()
    return stripped[0].isdigit() or lowered.startswith(
        ("for the years ended", "year period", "million for", "million as of")
    )


def _build_note_summary_fallback(chunk: FilingChunk, label: str) -> str:
    note_title = str(chunk.metadata.get("note_title") or chunk.subsection_title or "").strip()
    line_items = chunk.metadata.get("related_line_items") or []
    line_item_text = ", ".join(str(item) for item in line_items[:3] if str(item).strip())
    if note_title and line_item_text:
        return f"{label}: note '{note_title}' includes investor-relevant detail tied to {line_item_text}."
    if note_title:
        return f"{label}: note '{note_title}' contains accounting detail that may affect comparability."
    return f"{label}: this note contains accounting detail that may affect the interpretation of reported numbers."


def _first_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return "Untitled Table"


def _is_heading_like_match(text: str, start: int) -> bool:
    if start == 0:
        return True

    prefix = text[max(0, start - 12) : start]
    stripped = prefix.strip()
    if not stripped:
        return True
    return "\n" in prefix


def _section_match_score(text: str, start: int) -> tuple[int, int]:
    excerpt = text[start : start + 1200]
    score = 0
    if _is_heading_like_match(text, start):
        score += 5
    if not _looks_like_toc_excerpt(excerpt):
        score += 20
    if not any(_looks_like_toc_line(line) for line in excerpt.splitlines()[:10]):
        score += 8
    if len(re.findall(r"[A-Za-z]", excerpt[:400])) >= 180:
        score += 4
    return (score, start)


def _looks_like_toc_excerpt(text: str) -> bool:
    lowered = text.lower()
    item_count = len(re.findall(r"\bitem\s+\d+[a-z]?\b", lowered))
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    toc_like_lines = sum(1 for line in lines[:12] if _looks_like_toc_line(line))
    return (
        item_count >= 3
        or toc_like_lines >= 3
        or ("table of contents" in lowered[:220])
        or ("index" in lowered[:120] and "page" in lowered[:120])
    )


def _looks_like_toc_line(line: str) -> bool:
    normalized = re.sub(r"\s+", " ", line).strip()
    if not normalized:
        return False
    if re.search(r"\bitem\s+\d+[a-z]?\b", normalized, re.IGNORECASE) and re.search(r"\b\d{1,3}\b\s*$", normalized):
        return True
    if len(re.findall(r"\b\d{1,3}\b", normalized)) >= 2 and len(normalized) <= 140:
        return True
    if "information about our executive officers" in normalized.lower():
        return True
    return False


def _format_financial_fact(*, label: str, snippet: str) -> str | None:
    cleaned = re.sub(r"\s+", " ", snippet).strip()
    cleaned = cleaned.replace(" ,", ",").replace(" .", ".")
    if not cleaned:
        return None

    number_match = re.search(
        r"[$€¥£]?\s?\d[\d,]*(?:\.\d+)?(?:\s?(?:million|billion|trillion|M|B|bn|%)|%)?",
        cleaned,
        re.IGNORECASE,
    )
    if not number_match:
        return None

    focus_start = 0
    label_match = re.search(re.escape(label), cleaned, re.IGNORECASE)
    if label_match:
        focus_start = max(0, label_match.start() - 30)
    else:
        focus_start = max(0, number_match.start() - 100)

    focus = cleaned[focus_start : min(len(cleaned), number_match.end() + 180)]
    focus = focus.strip(" ,;:-")
    focus = re.sub(r"\s+", " ", focus)
    if len(focus) > 240:
        focus = focus[:237].rstrip() + "..."

    return f"{label}: {focus}"


def _extract_numeric_value_and_unit(text: str) -> tuple[float | None, str]:
    match = re.search(
        r"([$€¥£]?)\s?(\d[\d,]*(?:\.\d+)?)\s*(million|billion|trillion|M|B|bn|%)?",
        text,
        re.IGNORECASE,
    )
    if not match:
        return None, ""

    raw_value = float(match.group(2).replace(",", ""))
    suffix = (match.group(3) or "").lower()
    currency = match.group(1)

    unit = ""
    if suffix in {"billion", "b", "bn"}:
        unit = "USD_billion" if currency == "$" else "billion"
    elif suffix in {"million", "m"}:
        unit = "USD_million" if currency == "$" else "million"
    elif suffix == "%":
        unit = "percent"
    elif currency == "$":
        unit = "USD"

    return raw_value, unit


def _extract_period_from_text(text: str) -> str:
    match = re.search(r"\b(20\d{2})\b", text)
    return match.group(1) if match else ""


def _infer_metric_source(evidence: str, section_snippets: dict[str, str]) -> str:
    lowered = evidence.lower()
    for section_name, snippet in section_snippets.items():
        if lowered[:80] and lowered[:80] in snippet.lower():
            return section_name
    if "cash flow" in lowered or "capital expenditures" in lowered:
        return "Item 7 MD&A"
    if "diluted" in lowered or "income from operations" in lowered:
        return "Item 8 Financial Statements"
    return "Prepared extraction"


def _financial_fact_score(candidate: str, *, label: str) -> int:
    lowered = candidate.lower()
    score = 0
    score += len(re.findall(r"\d", candidate))
    score += 4 * len(re.findall(r"[$€¥£]", candidate))
    score += 3 * len(re.findall(r"\b\d+(?:\.\d+)?%\b", candidate))
    if "billion" in lowered or "million" in lowered or "bn" in lowered:
        score += 6
    if "increased" in lowered or "decreased" in lowered or "compared to" in lowered:
        score += 5
    if label.lower() in lowered:
        score += 3
    if "apple inc." in lowered or "form 10-k" in lowered:
        score -= 3
    return score


def _smart_trim(document_text: str, limit: int) -> str:
    text = document_text.strip()
    if limit <= 0 or not text:
        return ""
    if len(text) <= limit:
        return text

    cutoff = max(0, limit - len("\n[excerpt truncated]"))
    window_start = max(int(cutoff * 0.65), 0)
    candidate = text[:cutoff]

    for separator in ("\n\n", "\n", ". ", "; "):
        split_at = candidate.rfind(separator, window_start)
        if split_at != -1:
            trimmed = candidate[: split_at + len(separator)].strip()
            if trimmed:
                return f"{trimmed}\n[excerpt truncated]"

    return f"{candidate.rstrip()}\n[excerpt truncated]"


def _pick_input_file(source_path: Path) -> Path:
    if source_path.is_file():
        return source_path

    if not source_path.is_dir():
        raise FileNotFoundError(source_path)

    for name in SEC_CANDIDATE_NAMES:
        candidate = source_path / name
        if candidate.exists():
            return candidate

    for suffix in ("*.html", "*.htm", "*.txt", "*.pdf"):
        matches = sorted(source_path.glob(suffix))
        if matches:
            return matches[0]

    raise FileNotFoundError(f"No supported filing file found in {source_path}")


def _parse_html(path: Path) -> str:
    raw = path.read_text(encoding="utf-8", errors="ignore")
    soup = BeautifulSoup(raw, "html.parser")

    for tag in soup(["script", "style"]):
        tag.decompose()

    for tag in list(soup.find_all(True)):
        name = (tag.name or "").lower()
        attrs = tag.attrs or {}
        style = str(attrs.get("style") or "").replace(" ", "").lower()
        if name == "ix:header" or "display:none" in style:
            tag.decompose()

    text = soup.get_text("\n", strip=True)
    return _cleanup_text(text)


def _parse_text(path: Path) -> str:
    raw = path.read_text(encoding="utf-8", errors="ignore")
    return _cleanup_text(raw)


def _parse_pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except Exception as exc:  # pragma: no cover - dependency failure
        raise RuntimeError(
            "pypdf is required for PDF parsing. Install project dependencies first."
        ) from exc

    reader = PdfReader(str(path))
    pages = [page.extract_text() or "" for page in reader.pages]
    return _cleanup_text("\n\n".join(pages))


def _cleanup_text(text: str) -> str:
    text = html.unescape(text)
    text = text.replace("\xa0", " ")
    text = re.sub(r"\b([A-Z])\n([A-Z]{2,})\b", r"\1\2", text)
    text = re.sub(r"\b([A-Z]{2,10})\n([A-Z]{1,10})\b", r"\1\2", text)
    text = re.sub(r"\bPART([IVX]+)ITEM\b", r"PART \1\nITEM", text)
    text = re.sub(r"\b([IVX]+)ITEM\b", r"\1\nITEM", text)
    text = re.sub(r"\bPART([IVX]+)\b", r"PART \1", text)
    text = re.sub(r"\bBUSINESSGENERAL\b", "BUSINESS\nGENERAL", text)
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_company_name(text: str, selected_file: Path) -> str:
    candidate = _extract_registrant_name(text)
    if candidate:
        return candidate

    patterns = (
        re.compile(r"COMPANY CONFORMED NAME:\s*(.+)", re.IGNORECASE),
        re.compile(r"^([\w .,&'-]+)\s+\|\s+\d{4}\s+Form\s+10-K", re.MULTILINE),
    )
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            candidate = _clean_company_name(match.group(1))
            if (
                candidate
                and candidate.lower() not in {"10-k", "form 10-k"}
                and not _looks_like_date_label(candidate)
            ):
                return candidate

    ticker = _extract_ticker_from_path(selected_file)
    if ticker:
        return ticker

    if selected_file.parts:
        for part in reversed(selected_file.parts):
            if part.isupper() and 1 < len(part) <= 6:
                return part

    return "Unknown Company"


def _extract_reporting_period(text: str, selected_file: Path) -> str:
    match = re.search(r"CONFORMED PERIOD OF REPORT:\s*(\d{8})", text, re.IGNORECASE)
    if match:
        raw = match.group(1)
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"

    normalized_text = re.sub(r"\s+", " ", text)
    match = re.search(
        r"for the fiscal year ended\s+([A-Za-z]+\s+\d{1,2}\s*,\s*20\d{2})",
        normalized_text,
        re.IGNORECASE,
    )
    if match:
        return re.sub(r"\s*,\s*", ", ", match.group(1)).strip()

    match = re.search(r"(20\d{2})\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日", text)
    if match:
        return match.group(0)

    match = re.search(r"\b(20\d{2})\s+Form\s+10-K\b", text)
    if match:
        return match.group(1)

    return selected_file.stem


def _extract_registrant_name(text: str) -> str:
    lines = [line.strip(" _") for line in text.splitlines()]
    marker = "exact name of registrant as specified in its charter"
    for index, line in enumerate(lines):
        if marker not in line.lower():
            continue
        candidate_lines: list[str] = []
        scan = index - 1
        while scan >= 0 and len(candidate_lines) < 3:
            current = lines[scan].strip()
            if not current:
                scan -= 1
                continue
            if any(char.isdigit() for char in current):
                break
            if len(current) > 100:
                break
            candidate_lines.insert(0, current)
            scan -= 1
        candidate = _clean_company_name(" ".join(candidate_lines))
        if candidate and not _looks_like_date_label(candidate):
            return candidate
    return ""


def _clean_company_name(value: str) -> str:
    candidate = " ".join(str(value).split()).strip(" ,.-")
    replacements = {
        r"\bCORP\s+ORATION\b": "CORPORATION",
        r"\bINC\s+ORPORATED\b": "INCORPORATED",
        r"\bTECH\s+NOLOGIES\b": "TECHNOLOGIES",
        r"\bHOLD\s+INGS\b": "HOLDINGS",
    }
    for pattern, replacement in replacements.items():
        candidate = re.sub(pattern, replacement, candidate, flags=re.IGNORECASE)
    candidate = re.sub(r"\s{2,}", " ", candidate).strip(" ,.-")
    return candidate


def _extract_ticker_from_path(selected_file: Path) -> str | None:
    parts = list(selected_file.parts)
    if "sec-edgar-filings" in parts:
        index = parts.index("sec-edgar-filings")
        if index + 1 < len(parts):
            ticker = parts[index + 1].strip()
            if ticker:
                return ticker
    return None


def _looks_like_date_label(value: str) -> bool:
    return bool(
        re.fullmatch(
            r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2}(?:,\s*\d{4})?",
            value.strip(),
            re.IGNORECASE,
        )
    )
