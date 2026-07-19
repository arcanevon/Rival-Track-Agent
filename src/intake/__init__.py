"""证据接入流程的稳定公开入口。"""

from .discovery import build_source_candidates, build_source_candidates_with_search
from .enrich import build_cache_from_user_data, enrich_competitor_inputs_with_search
from .hydrate import hydrate_sources_for_analysis


__all__ = [
    "build_cache_from_user_data",
    "build_source_candidates",
    "build_source_candidates_with_search",
    "enrich_competitor_inputs_with_search",
    "hydrate_sources_for_analysis",
]
