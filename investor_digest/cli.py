from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from investor_digest.config import Settings
from investor_digest.pipeline import analyze_path, prepare_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Investor Digest local pipeline")
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare_cmd = subparsers.add_parser(
        "prepare-path", help="Prepare filing context without calling the model"
    )
    prepare_cmd.add_argument("--path", required=True, help="Path to file or filing folder")

    analyze_cmd = subparsers.add_parser(
        "analyze-path", help="Analyze a filing through the local model runtime"
    )
    analyze_cmd.add_argument("--path", required=True, help="Path to file or filing folder")
    analyze_cmd.add_argument("--audience", help="Audience override")
    analyze_cmd.add_argument("--language", help="Language override, default zh-Hans")

    serve_cmd = subparsers.add_parser("serve", help="Run the local API")
    serve_cmd.add_argument("--host", default="127.0.0.1")
    serve_cmd.add_argument("--port", type=int, default=8008)

    args = parser.parse_args()
    settings = Settings.from_env()

    if args.command == "prepare-path":
        prepared = prepare_path(args.path, settings=settings)
        payload = {
            "company_name": prepared.document.company_name,
            "reporting_period": prepared.document.reporting_period,
            "selected_file": str(prepared.document.selected_file),
            "warnings": prepared.warnings,
            "financial_facts": prepared.financial_facts,
            "financial_metric_map": prepared.financial_metric_map,
            "metric_records": [asdict(record) for record in prepared.metric_records],
            "company_profile": asdict(prepared.company_profile)
            if prepared.company_profile
            else {},
            "financial_snapshot": prepared.financial_snapshot,
            "key_explanations": [asdict(card) for card in prepared.key_explanations],
            "key_risks": [asdict(card) for card in prepared.key_risks],
            "accounting_flags": [asdict(card) for card in prepared.accounting_flags],
            "outlook_signals": [asdict(card) for card in prepared.outlook_signals],
            "investor_summary_layer": prepared.investor_summary_layer,
            "investor_summary_input": prepared.investor_summary_input,
            "context": prepared.context,
            "debug_counts": {
                "section_count": len(prepared.section_snippets),
                "narrative_chunk_count": len(prepared.narrative_chunks),
                "table_chunk_count": len(prepared.table_chunks),
                "note_chunk_count": len(prepared.note_chunks),
            },
        }
        saved_to = _save_output(
            payload,
            company_name=prepared.document.company_name,
            reporting_period=prepared.document.reporting_period,
            suffix="prepared",
        )
        print(
            json.dumps(
                {
                    "saved_to": str(saved_to),
                    "company_name": prepared.document.company_name,
                    "reporting_period": prepared.document.reporting_period,
                    "selected_file": str(prepared.document.selected_file),
                    "warnings": prepared.warnings,
                    "financial_metric_keys": list(prepared.financial_metric_map.keys()),
                    "section_keys": list(prepared.section_snippets.keys()),
                    "metric_record_count": len(prepared.metric_records),
                    "validated_metric_count": sum(
                        1 for record in prepared.metric_records if record.valid
                    ),
                    "financial_snapshot_keys": list(prepared.financial_snapshot.keys()),
                    "key_explanations_count": len(prepared.key_explanations),
                    "key_risks_count": len(prepared.key_risks),
                    "accounting_flags_count": len(prepared.accounting_flags),
                    "outlook_signals_count": len(prepared.outlook_signals),
                    "narrative_chunk_count": len(prepared.narrative_chunks),
                    "table_chunk_count": len(prepared.table_chunks),
                    "note_chunk_count": len(prepared.note_chunks),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    if args.command == "analyze-path":
        digest = analyze_path(
            args.path,
            settings=settings,
            audience=args.audience,
            language=args.language,
        )
        payload = digest.model_dump()
        saved_to = _save_output(
            payload,
            company_name=digest.company_name,
            reporting_period=digest.reporting_period,
            suffix="digest",
        )
        print(
            json.dumps(
                {
                    "saved_to": str(saved_to),
                    **payload,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    if args.command == "serve":
        import uvicorn

        from investor_digest.app import create_app

        app = create_app(settings)
        uvicorn.run(app, host=args.host, port=args.port)
        return 0

    parser.error("Unknown command")
    return 2


def _save_output(
    payload: dict,
    *,
    company_name: str,
    reporting_period: str,
    suffix: str,
) -> Path:
    output_dir = Path.cwd() / "outputs"
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    report_year = _extract_report_year(reporting_period)
    slug = _slugify_company_name(company_name)
    filename = f"{slug}_{report_year}_{timestamp}_{suffix}.json"
    destination = output_dir / filename
    destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return destination


def _extract_report_year(reporting_period: str) -> str:
    years = re.findall(r"(20\d{2})", reporting_period or "")
    return years[-1] if years else "unknown-year"


def _slugify_company_name(company_name: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", company_name or "").strip("_").lower()
    return normalized or "unknown-company"
