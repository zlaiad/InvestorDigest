# FinSight

FinSight is a local web app for turning 10-K filings into investor-friendly reports.

## Run

Install dependencies:

```bash
python3 -m pip install -e .
```

Configure an LLM API key. For DeepSeek:

```bash
export LLM_PROVIDER=deepseek
export DEEPSEEK_API_KEY=your_api_key
export DEEPSEEK_MODEL=deepseek-chat
```

Start the web app:

```bash
python3 main.py serve --host 127.0.0.1 --port 8008
```

Open:

```text
http://127.0.0.1:8008/
```

If you do not set environment variables, you can also enter the provider, model, and API key in the optional API settings panel on the web page.

