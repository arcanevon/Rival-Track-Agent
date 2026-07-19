# Crawl4AI Integration

`crawl4ai` and Playwright are optional Evidence Resolvers. Search still discovers candidate URLs; the resolver reads concrete pages before the normal `source_quality`, `source_coverage`, and `evidence_gaps` gates run.

## Setup

```powershell
conda activate rivltrack
pip install -r requirements-crawl.txt
crawl4ai-setup
playwright install chromium
```

Disable the resolver without uninstalling it:

```powershell
$env:CRAWL4AI_ENABLED='0'
$env:DYNAMIC_BROWSER_ENABLED='0'
```

## Flow

```text
EvidenceAcquisitionPlan -> Search API -> concrete URL candidates
    -> Crawl4AI markdown extraction
    -> Jina Reader / HTMLParser fallback
    -> detect JavaScript shell -> Playwright visible-text rendering
    -> source_quality + competitor relevance check
    -> Collector
```

For official homepages, the intake layer also reads `robots.txt`, follows up to eight nested sitemap documents, and falls back to same-domain homepage links. Product, announcement, news, release, pricing, documentation, and update paths are ranked before download or generic landing pages.

Playwright is only invoked when the normal readers fail or the returned HTML looks like a JavaScript application shell. Control its page timeout with `DYNAMIC_BROWSER_TIMEOUT_MS` (default: `25000`). Browser-rendered text is still subject to the same entity relevance and evidence-quality gates; successful rendering alone does not make a source scoreable.

Search entry pages, encyclopedias, login-gated social pages, and pages whose body text does not match the competitor remain in `metadata.candidate_sources` and should produce `evidence_gaps`, not scoring evidence.
