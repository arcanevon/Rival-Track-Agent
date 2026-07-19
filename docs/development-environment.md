# Development Environment

## Prerequisites

- Python 3.12
- conda (Miniconda or Anaconda)
- Git
- DeepSeek API key (from [DeepSeek Platform](https://platform.deepseek.com))
- Optional: Bocha Search API key (from [Bocha AI Open Platform](https://open.bocha.cn)) for live web search

## Setup

### 1. Create conda environment

```powershell
conda create -n rivltrack python=3.12 -y
conda activate rivltrack
```

### 2. Install dependencies

```powershell
pip install -r requirements.txt
```

Optional: Crawl4AI for better page extraction (browser-rendered JS pages):

```powershell
pip install -r requirements-crawl.txt
crawl4ai-setup
```

### 3. Configure environment

```powershell
cp .env.example .env
```

Edit `.env` with your keys:

```ini
DEEPSEEK_API_KEY=sk-your-deepseek-key
BOCHA_SEARCH_API_KEY=sk-your-bocha-key   # optional, enables live search
```

### 4. Verify setup

```powershell
# Check imports
python -c "from src.main import app; print('OK')"

# Run tests
pytest -q
# Expected: 92 passed in 7 files

# Check frontend syntax
node --check src/frontend/app.js
```

## Running the app

```powershell
conda activate rivltrack
python src/main.py
```

Opens HTTP server on `:8080` and WebSocket on `:8765`. Browser opens automatically.

### Custom ports

```powershell
$env:HTTP_PORT="8081"
$env:WS_PORT="8766"
python src/main.py
```

### Demo autorun (skip manual interaction)

```powershell
$env:RIVALTRACK_AUTORUN="true"
python src/main.py
```

## Running tests

```powershell
# Full suite (92 tests, 7 files)
pytest -q

# Specific test files
pytest tests/test_models.py -q
pytest tests/test_pipeline_integration.py -q
pytest tests/test_source_intake.py -q

# With coverage
pip install pytest-cov
pytest --cov=src -q
```

Tests use monkeypatch for external API mocking — no API keys needed.

## Validation commands

For Windows, set UTF-8 output first to avoid GBK encoding failures:

```powershell
$env:PYTHONIOENCODING='utf-8'
$env:CONDA_REPORT_ERRORS='false'
conda run -n rivltrack python -m pytest -q
```

Python compilation check:

```powershell
python -m py_compile src/main.py
python -m py_compile src/pipeline/dag.py
python -m py_compile src/agents/prompts.py
python -m py_compile src/client/deepseek.py
python -m py_compile src/server/ws.py
python -m py_compile src/models/output.py
```

## Project layout

```
src/
├── main.py                 # Entry point — HTTP + WebSocket server
├── config/                 # Per-industry YAML configs, loader, industry router
├── intake/                 # Source discovery, search, fetch, quality, enrichment
├── pipeline/               # 5-agent LangGraph DAG orchestration
├── agents/prompts.py       # System + user prompts for all 5 agents
├── models/                 # Pydantic data models (output, contracts)
├── client/deepseek.py      # DeepSeek API client with retry + JSON parsing
├── server/ws.py            # WebSocket server for real-time dashboard updates
├── frontend/               # Dashboard UI (HTML, JS, CSS, vendor libs)
└── tools/                  # CLI utilities (benchmark, pipeline replay)
```

## Troubleshooting

- **conda not found**: Install Miniconda from https://docs.conda.io/en/latest/miniconda.html
- **DeepSeek API 401**: Check `DEEPSEEK_API_KEY` in `.env` — key may have expired
- **Import errors after git pull**: The project was restructured from `backend/` to `src/` on 2026-05-29. Run `pip install -e .` or ensure `src/` is on PYTHONPATH (pytest's `conftest.py` handles this for tests)
- **Port already in use**: See [runtime-checklist.md](runtime-checklist.md) for port troubleshooting
- **GBK encoding errors in pytest output**: Set `$env:PYTHONIOENCODING='utf-8'` before running
- **WebSocket connection refused**: Ensure nothing is running on port 8765. Check with `netstat -ano | findstr :8765`
