# Contributing to RivalTrackAgent

## Setup

```bash
# Create conda environment
conda create -n rivltrack python=3.12 -y
conda activate rivltrack

# Install dependencies
pip install -r requirements.txt

# Optional: Crawl4AI for better page extraction
pip install -r requirements-crawl.txt
crawl4ai-setup

# Configure
cp .env.example .env
# Edit .env with your DEEPSEEK_API_KEY and optionally BOCHA_SEARCH_API_KEY
```

## Development

```bash
# Run the server
python src/main.py

# Run tests
pytest -q

# Benchmark evidence collection
python src/tools/benchmark_links.py --tracks ai-coding milktea --search
```

## Project Structure

- `src/main.py` — entry point (HTTP :8080, WebSocket :8765)
- `src/config/` — per-industry YAML configs, scoring, query templates, industry router
- `src/intake/` — source discovery, search, fetch, quality scoring, evidence planning
- `src/pipeline/` — 5-agent LangGraph DAG (Collector → Analyst A/B → QA → Writer)
- `src/agents/prompts.py` — system + user prompts for all 5 agents
- `src/memory/` — short-term checkpoints, long-term memory, and evidence workspace
- `src/models/` — Pydantic data models (output, contracts)
- `src/reporting/` — annotation-driven report revision
- `src/client/deepseek.py` — DeepSeek API client with retry, JSON parsing
- `src/server/ws.py` — WebSocket server for real-time dashboard updates
- `src/frontend/` — dashboard UI (Cytoscape.js graph, Chart.js radar, timeline)

## Adding a New Industry

1. Add keyword routes to `src/config/industry_routing.yaml`
2. Create `src/config/query_templates_{industry}.yaml` (7 evidence slots)
3. Create `src/config/scoring_{industry}.yaml` (scoring weights + path rules)
4. Add brand data to `src/intake/constants.py` (optional: `SEARCH_DISAMBIGUATION_TERMS`, `OFFICIAL_DOMAIN_HINTS`, `known_direct_sources`)

## Code Style

- Python: follow PEP 8, use type hints on public functions
- JavaScript: ES module syntax preferred for new files, keep functions small
- YAML: 2-space indent, use Chinese for query templates

## Testing

- Tests live in `tests/`; avoid hard-coding a test count because the suite changes frequently.
- `conftest.py` sets up the Python path
- Tests use monkeypatch for external API mocking
- Run `pytest -q` before submitting PRs
