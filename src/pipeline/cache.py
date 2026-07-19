"""Cache loading and enrichment for the pipeline DAG.

Loads competitor cache JSON files, attaches evidence acquisition plans,
and prepares enriched cache data for downstream agent prompts.
"""

import json
import logging
from pathlib import Path

from src.intake.quality import source_coverage_for_competitor
from src.intake.plan import build_evidence_acquisition_plans

logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "competitor-data"


def load_cache() -> dict[str, dict]:
    """Load competitor cache JSON files from data/competitor-data/**/."""
    cache_data: dict[str, dict] = {}
    if not CACHE_DIR.exists():
        logger.warning("Cache directory not found: %s", CACHE_DIR)
        return cache_data

    for fpath in sorted(CACHE_DIR.glob("**/*.json")):
        try:
            data = json.loads(fpath.read_text(encoding="utf-8"))
            company = data.get("company", fpath.stem)
            cache_data[company] = data
            logger.info("Loaded cache: %s (%d official, %d benchmark, %d community, %d leading sources)",
                        company,
                        len(data.get("official_sources", [])),
                        len(data.get("benchmark_sources", [])),
                        len(data.get("community_sources", [])),
                        len(data.get("leading_sources", [])))
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning("Skipping %s: %s", fpath, e)

    return cache_data


def _cache_with_evidence_plans(cache_data: dict[str, dict], track: str = "") -> dict[str, dict]:
    """Attach evidence acquisition plans without mutating caller-owned cache data."""
    plans = build_evidence_acquisition_plans(cache_data, track)
    enriched: dict[str, dict] = {}
    for company, data in cache_data.items():
        if not isinstance(data, dict):
            continue
        metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        enriched[company] = {
            **data,
            "metadata": {
                **metadata,
                "source_coverage": metadata.get("source_coverage") or source_coverage_for_competitor(data),
                "evidence_acquisition_plan": metadata.get("evidence_acquisition_plan") or plans.get(company, {}),
            },
        }
    return enriched
