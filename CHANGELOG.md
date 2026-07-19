# Changelog

## [1.0.2.0] - 2026-05-29

The project structure graduates from a flat `backend/` layout to a proper `src/` package. Two monolithic modules (962-line `pipeline.py` and 1742-line `source_intake.py`) are decomposed into focused submodules. Search scoring and query templates now support 8 industry types with per-industry YAML configs and keyword-based routing. SSRF protection hardens all external URL fetch paths.

### Changed
- Project restructured: `backend/` → `src/` with `config/`, `intake/`, `pipeline/`, `agents/`, `models/`, `client/`, `server/`, `tools/` sub-packages
- `backend/pipeline.py` decomposed into `src/pipeline/dag.py`, `nodes.py`, `format.py`, `coverage.py`, `cache.py`
- `backend/source_intake.py` decomposed into `src/intake/search.py`, `discovery.py`, `hydrate.py`, `quality.py`, `plan.py`, `enrich.py`, `constants.py`
- `frontend/` moved to `src/frontend/` with text helpers extracted to `text-helpers.js`

### Added
- Industry routing: `src/config/router.py` — keyword match on track + product name with LLM fallback, covering 8 industry types
- Per-industry scoring configs: `scoring_automotive.yaml`, `consumer_hardware`, `fintech`, `gaming`, `healthcare`, `platform_social`, `retail_fmcg`, `software_saas`
- Per-industry query templates matching the same 8 industry types
- SSRF protection: `src/intake/url_security.py` — private-IP blocklist for all external URL fetch paths
- Path traversal hardening: Unicode normalization + backslash detection in static file server
- `CONTRIBUTING.md` — contribution guide for open-source readiness

### Fixed
- Hardcoded "oracle" keyword penalty → configurable via `scoring.yaml` (`oracle_false_positive_penalty`)
- Content farm domain penalty → configurable via `scoring.yaml` (`content_farm_marker_penalty`)
- `tests/conftest.py` no longer inserts the deleted `backend/` path
- Error responses no longer leak internal exception details to callers
- Hydration quality notes no longer store raw exception messages
- CLAUDE.md industry table updated: 6 industries marked "(future)" now reference actual config files

### Security
- All external URL fetches validated against RFC 1918 / loopback / link-local / cloud-metadata IP ranges
- `/api/fetch-source` rejects internal hostnames before issuing outbound requests
- Jina Reader proxy, Crawl4AI, and sitemap discovery all routed through the shared URL safety module

## [1.0.1.1] - 2026-05-29

You can now tune the collection agent's search scoring and query templates without touching code. Download pages and search entry URLs are pre-filtered before they waste fetch quota. A standalone benchmark tool measures evidence coverage across tracks.

### Added
- Configurable search scoring: `src/config/scoring.yaml` controls domain bonuses, path penalties, and content-farm suppression
- Configurable query templates: `src/config/query_templates.yaml` defines per-evidence-slot search queries with exclude terms
- `src/tools/benchmark_links.py` — standalone collection agent link discovery benchmark (table + JSON report)
- Sitemap discovery: official domains are probed for `/sitemap.xml` to surface feature/pricing/changelog URLs
- Download page pre-filtering: `/download`, `/install` paths and bare homepages are screened out before fetching

### Changed
- Search result scoring: official domain bonus reduced from +80 to +40; content paths (+30), review paths (+15), download paths (-50), aggregator domains (-30)
- Source hydration: per-bucket fetch cap raised from 2 to 4; weak/failed fetches no longer count against quota
- Evidence acquisition plans now render search queries from YAML templates with `{name}`/`{track}` placeholders

### Added (tests)
- 20 new tests for config loading, path matching, pre-filter, evidence slot templates, and hydration flow (92 total, up from 72)

## [1.0.1.0] - 2026-05-28

### Fixed
- Dashboard no longer displays fake threat scores when backend returns no data — shows N/A instead
- Save and restore failures now surface visible warnings instead of failing silently
- Missing CORS headers prevented cross-origin frontend access — added aiohttp middleware
- Demo autorun path leaked stale state between pipeline runs — now resets on each start
- Concurrent pipeline runs no longer risk data corruption from shared model mutation — models use defensive copy pattern

### Changed
- Pipeline agents now share a consistent token budget and timeout, reducing truncated output when analyzing many competitors
- API retry warnings appear in structured logs instead of raw console output
- PipelineState TypedDict renamed to _PipelineGraphState to resolve naming collision with models.py

### Removed
- Deduplicated source intake: search ranking, source quality scoring, and caching now live in a single module (817 lines removed from main.py)
- Removed 3 dead frontend functions (updateNode, setEdgeActive, setEdgeConflict) and 11 orphaned call sites — graph rendering is simpler and faster
- Trimmed stale deterministic search entries (qichacha, evidence-slot Bing URLs, taobao, douyin) that were returning empty results

### Added
- Time-based freshness filtering on search API: oneMonth for product/pricing/community slots, oneYear for benchmark/expansion signals
- Recency keywords (最新 + current year) appended to all competitor search queries
- Evidence field validation: warns when LLM returns unrecognized field names in evidence objects
- Pipeline integration test catches 5-agent DAG output regressions before they reach the dashboard

## [1.0.0.1] - 2026-05-24

### Changed
- Updated project documentation: README structure tree, test counts (27→51), dual-track fallback references
- Added project context and environment info to CLAUDE.md
- Expanded v1.0.0.0 CHANGELOG entry to cover dual-market support, frontend split, and API surface
- Added documentation index to README for discoverability

## [1.0.0.0] - 2026-05-21

You can now run a full competitive threat analysis starting from just a product name. Five AI agents collect evidence, debate findings from opposing perspectives, and produce a scored four-dimensional threat matrix with an ordered action list. Open the dashboard, pick a track, and watch the pipeline run in real time.

### Added
- 5-agent DAG pipeline: Collector → Analyst A (VRIO) + Analyst B (SWOT) → QA → Writer
- Framework divergence: Analyst A (VRIO) and Analyst B (SWOT) independently analyze the same O/B/C/L evidence, producing different scores from their methodological lens
- Collector gold-source methodology: official, benchmark, and community source tiers with credibility scoring
- Threat Target concept: all analysis relative to a defined "my product" with four threat dimensions
- Dual-market support: AI code assistant track (7 competitors) + milk tea track (8 competitors)
- Custom analysis API: POST `/api/analyze`, `/api/discover-sources`, `/api/fetch-source`
- DeepSeek API client with retry logic, exponential backoff, and Chinese+English refusal detection
- Real-time WebSocket server pushing agent state to the knowledge graph dashboard
- Cytoscape.js knowledge graph visualization with evidence chains and debate callouts
- Chart.js radar chart (4 threat dimensions + overall) and threat heatmap matrix
- Sample data selector: modal-based track switcher for AI code assistant and milk tea demo data
- Framework-free responsive dashboard: separate `styles.css` + `app.js` + `index.html` with four-panel A3 layout (summary, charts, graph+timeline, actions)
- Local vendor libraries: Cytoscape.js 3.30.4 + Chart.js 4.4.0 (fully offline, no CDN)
- 3-tier degradation: WebSocket → localStorage recovery → dual-track fallback JSON
- Path traversal sandbox for static file serving
- XSS prevention via `escapeHTML()` and `safeURL()` in the frontend
- asyncio orchestration with parallel Analyst A + B execution
- Real pipeline replay scripts: `run_real_pipeline.py` and `run_real_pipeline_milktea.py`
- Pytest suite: 51 tests across 5 files (models, client, imports, source fetch, decision contracts)
- CONTEXT.md domain glossary (Threat Target, Threat Matrix, Threat Score, Recommended Response)
