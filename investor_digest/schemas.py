from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class GlossaryItem(BaseModel):
    term: str
    plain_explanation: str


class ChartSeries(BaseModel):
    name: str
    unit: str | None = None
    values: list[float] = Field(default_factory=list)


class SankeyNode(BaseModel):
    name: str
    value: float | None = None
    item_style_color: str | None = None


class SankeyLink(BaseModel):
    source: str
    target: str
    value: float


class ChartSpec(BaseModel):
    title: str
    chart_type: Literal["bar", "line", "area", "stacked_bar", "donut", "sankey"]
    why_it_matters: str = ""
    x_axis_label: str = ""
    categories: list[str] = Field(default_factory=list)
    series: list[ChartSeries] = Field(default_factory=list)
    flow_nodes: list[SankeyNode] = Field(default_factory=list)
    flow_links: list[SankeyLink] = Field(default_factory=list)
    palette: list[str] = Field(default_factory=list)
    source_snippet: str = ""
    confidence: Literal["high", "medium", "low"] = "medium"


class InvestorDigest(BaseModel):
    company_name: str
    reporting_period: str
    analysis_language: str
    audience: str
    one_sentence_takeaway: str
    overview_markdown: str
    key_points: list[str] = Field(default_factory=list)
    positives: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    watchlist: list[str] = Field(default_factory=list)
    glossary: list[GlossaryItem] = Field(default_factory=list)
    chart_specs: list[ChartSpec] = Field(default_factory=list)
    risk_disclaimer: str
    warnings: list[str] = Field(default_factory=list)


class AnalyzePathRequest(BaseModel):
    path: str
    audience: str | None = None
    language: str | None = None


@dataclass(slots=True)
class ParsedDocument:
    source_path: Path
    selected_file: Path
    file_type: str
    text: str
    company_name: str = "Unknown Company"
    reporting_period: str = "Unknown Period"
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class MetricRecord:
    metric: str
    metric_type: str
    value: float | None = None
    unit: str = ""
    period: str = ""
    source: str = ""
    sources: list[str] = field(default_factory=list)
    canonical_source_chunk_id: str = ""
    canonical_source_table_name: str = ""
    explanatory_chunk_ids: list[str] = field(default_factory=list)
    canonical_numeric_source: dict[str, object] = field(default_factory=dict)
    explanatory_sources: list[dict[str, object]] = field(default_factory=list)
    confidence: str = "medium"
    valid: bool = False
    validation_errors: list[str] = field(default_factory=list)
    evidence: str = ""


@dataclass(slots=True)
class FilingChunk:
    chunk_id: str
    chunk_type: str
    section_path: str
    order: int
    text: str
    item_number: str = ""
    subsection_title: str = ""
    token_count: int = 0
    importance_score: float = 0.0
    year: int | None = None
    source_page: str = ""
    source_anchor: str = ""
    parent_chunk_id: str | None = None
    is_sentence_complete: bool = True
    is_numeric_dense: bool = False
    has_toc_noise: bool = False
    has_table_structure: bool = False
    extraction_confidence: str = "medium"
    parser_source: str = "pre_split_parser"
    validation_status: str = "ok"
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class CompanyProfile:
    company_name: str
    reporting_period: str
    segments: list[str] = field(default_factory=list)
    major_products: list[str] = field(default_factory=list)
    manufacturing_regions: list[str] = field(default_factory=list)
    strategic_themes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SummaryCard:
    topic: str
    summary: str
    source_chunk_id: str = ""
    importance: str = "medium"
    linked_metrics: list[str] = field(default_factory=list)
    explanation_type: str = ""
    flag_type: str = ""
    why_it_matters: str = ""
    time_horizon: str = ""
    guidance_type: str = ""
    certainty_level: str = ""


@dataclass(slots=True)
class RiskCard:
    risk_name: str
    short_summary: str
    impact_area: list[str] = field(default_factory=list)
    source_chunk_id: str = ""
    importance: str = "medium"
    severity: str = "medium"


@dataclass(slots=True)
class PreparedContext:
    document: ParsedDocument
    context: str
    financial_facts: list[str] = field(default_factory=list)
    financial_metric_map: dict[str, str] = field(default_factory=dict)
    metric_records: list[MetricRecord] = field(default_factory=list)
    section_snippets: dict[str, str] = field(default_factory=dict)
    narrative_chunks: list[FilingChunk] = field(default_factory=list)
    table_chunks: list[FilingChunk] = field(default_factory=list)
    note_chunks: list[FilingChunk] = field(default_factory=list)
    investor_summary_layer: dict[str, object] = field(default_factory=dict)
    company_profile: CompanyProfile | None = None
    financial_snapshot: dict[str, dict[str, object]] = field(default_factory=dict)
    key_explanations: list[SummaryCard] = field(default_factory=list)
    key_risks: list[RiskCard] = field(default_factory=list)
    accounting_flags: list[SummaryCard] = field(default_factory=list)
    outlook_signals: list[SummaryCard] = field(default_factory=list)
    investor_summary_input: dict[str, object] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
