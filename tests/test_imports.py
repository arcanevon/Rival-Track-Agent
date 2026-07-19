"""Smoke test: verify all backend modules import cleanly."""


def test_import_models():
    from src import models
    assert hasattr(models, "AgentNodeOutput")
    assert hasattr(models, "EvidenceRef")
    assert hasattr(models, "WSMessage")


def test_import_deepseek_client():
    import src.client.deepseek as deepseek_client
    assert hasattr(deepseek_client, "call_and_parse")
    assert hasattr(deepseek_client, "parse_agent_output")


def test_import_agent_prompts():
    import src.agents.prompts as agent_prompts
    assert hasattr(agent_prompts, "COLLECTOR_SYSTEM")
    assert hasattr(agent_prompts, "WRITER_USER")


def test_import_ws_server():
    import src.server.ws as ws_server
    assert hasattr(ws_server, "broadcast_node_update")
    assert hasattr(ws_server, "start_server")


def test_import_pipeline():
    from src import pipeline
    assert pipeline.__all__ == [
        "build_pipeline_dag",
        "run_pipeline",
        "run_pipeline_custom",
    ]
    assert not hasattr(pipeline, "_format_threat_target")
    assert not hasattr(pipeline, "load_fallback")


def test_import_intake():
    from src import intake
    assert intake.__all__ == [
        "build_cache_from_user_data",
        "build_source_candidates",
        "build_source_candidates_with_search",
        "enrich_competitor_inputs_with_search",
        "hydrate_sources_for_analysis",
    ]
    assert not hasattr(intake, "_check_path_match")
    assert not hasattr(intake, "assess_source_quality")
