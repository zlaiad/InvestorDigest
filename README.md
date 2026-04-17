# Investor Digest MVP

This project converts company filings into structured summaries and chart-ready outputs for a browser-based report view.

## What this MVP does

- Accepts a local `pdf`, `html`, `htm`, `txt`, or a directory containing SEC filing files
- Extracts readable text and key filing sections
- Sends a curated context window to a configured LLM endpoint
- Returns:
  - plain-language summary
  - positives and risks
  - glossary
  - chart configuration suggestions with friendly palettes

## What it does not do yet

- Scanned-PDF OCR fallback through a VLM page-by-page pipeline
- Full XBRL fact extraction
- Polished multi-page workflow or authentication

For scanned PDFs, the current parser will warn when text extraction is weak. The next iteration should add page rendering plus a vision model call.

## Setup

1. Install dependencies:

```bash
python3 -m pip install -e .
```

2. Configure the model endpoint.

Local OpenAI-compatible runtime example:

```bash
export LLM_BASE_URL=http://127.0.0.1:1234/v1
export LLM_MODEL=Qwen3.5-9B
export LLM_API_KEY=lm-studio
```

OpenAI API example:

```bash
export LLM_PROVIDER=openai
export OPENAI_API_KEY=your_api_key
export OPENAI_MODEL=gpt-4.1-mini
```

## CLI

Inspect the prepared filing context without calling the model:

```bash
python main.py prepare-path \
  --path sec_filings/sec-edgar-filings/AAPL/10-K/0000320193-23-000106
```

Run the full analysis:

```bash
python main.py analyze-path \
  --path sec_filings/sec-edgar-filings/AAPL/10-K/0000320193-23-000106
```

## API

Run the API:

```bash
python main.py serve --host 127.0.0.1 --port 8008
```

Then open [http://127.0.0.1:8008/](http://127.0.0.1:8008/) for the investor-friendly report UI.

Analyze an existing path:

```bash
curl -X POST http://127.0.0.1:8008/api/analyze/path \
  -H 'Content-Type: application/json' \
  -d '{
    "path": "sec_filings/sec-edgar-filings/AAPL/10-K/0000320193-23-000106"
  }'
```

Upload a file:

```bash
curl -X POST http://127.0.0.1:8008/api/analyze/file \
  -F "file=@/absolute/path/to/report.pdf"
```

The upload endpoint also accepts `audience` and `language` form fields, which the bundled UI sends automatically.

## Output shape

The API and CLI both produce a structured JSON payload with:

- `one_sentence_takeaway`
- `overview_markdown`
- `key_points`
- `positives`
- `risks`
- `watchlist`
- `glossary`
- `chart_specs`
- `warnings`

Each chart spec is designed to be rendered directly into a frontend charting library.
