"""Config loading: YAML scoring, query templates, and industry routing."""

from pathlib import Path
import logging
import yaml

logger = logging.getLogger(__name__)

_CONFIG_DIR = Path(__file__).resolve().parent

_scoring_config: dict[str, dict] = {}
_query_templates_config: dict[str, dict] = {}
_routing_config: dict | None = None


def _load_yaml(name: str) -> dict:
    path = _CONFIG_DIR / name
    if not path.is_file():
        logger.warning("Config file not found: %s, using built-in defaults.", path)
        return {}
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_scoring_config(industry_type: str = "software_saas") -> dict:
    """Load scoring config for a given industry type."""
    global _scoring_config
    if not isinstance(_scoring_config, dict):
        _scoring_config = {}
    cache_key = f"scoring_{industry_type}"
    if cache_key not in _scoring_config:
        cfg = _load_yaml(f"scoring_{industry_type}.yaml")
        if not cfg and industry_type != "software_saas":
            cfg = _load_yaml("scoring.yaml")
        if not cfg:
            cfg = _load_yaml("scoring_software_saas.yaml")
        _scoring_config[cache_key] = cfg
    return _scoring_config[cache_key]


def load_query_templates_config(industry_type: str = "software_saas") -> dict:
    """Load query templates config for a given industry type."""
    global _query_templates_config
    if not isinstance(_query_templates_config, dict):
        _query_templates_config = {}
    cache_key = f"query_{industry_type}"
    if cache_key not in _query_templates_config:
        cfg = _load_yaml(f"query_templates_{industry_type}.yaml")
        if not cfg and industry_type != "software_saas":
            cfg = _load_yaml("query_templates.yaml")
        if not cfg:
            cfg = _load_yaml("query_templates_software_saas.yaml")
        _query_templates_config[cache_key] = cfg
    return _query_templates_config[cache_key]


def load_routing_config() -> dict:
    """Load industry routing keyword map."""
    global _routing_config
    if _routing_config is None:
        _routing_config = _load_yaml("industry_routing.yaml")
    return _routing_config
