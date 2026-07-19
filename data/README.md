# Bundled data

The `data/` directory contains reproducibility fixtures, not a live competitive-intelligence database.

## Contents

- `competitor-data/`: cached competitor inputs grouped by scenario. They let the pipeline and tests run against stable source snapshots.
- `demo-fallback.json`: precomputed five-Agent output for the AI coding scenario.
- `demo-fallback-milktea.json`: precomputed five-Agent output for the milk-tea scenario.
- `domain-benchmark-scenarios.json`: cross-domain collection benchmark definitions.
- `benchmark/`: generated benchmark output; ignored by Git.

## Interpretation rules

- Treat every bundled claim, score, URL, and quote as a historical demo snapshot.
- Do not present fallback output as a current market conclusion.
- Search snippets and candidate URLs are discovery leads, not citable evidence.
- A real analysis must re-read concrete content pages and pass the relevance and source-quality gates.
- Review source terms and redistribution rights before publishing additional captured content.

Runtime long-term memory and the evidence workspace are stored under `logs/`. That directory may contain user-specific analysis history and is never part of the public repository.
