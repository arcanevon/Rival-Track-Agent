from pathlib import Path

from src.tools.benchmark_domains import aggregate_results, load_scenarios, prepare_competitor, quality_gate


ROOT = Path(__file__).parents[1]


def test_all_configured_industries_have_a_benchmark_scenario() -> None:
    scenarios = load_scenarios(ROOT / "data" / "domain-benchmark-scenarios.json")
    assert {row["industry"] for row in scenarios} == {
        "software_saas", "retail_fmcg", "automotive", "consumer_hardware",
        "gaming", "platform_social", "fintech", "healthcare",
    }


def test_prepare_competitor_separates_search_entries_from_direct_pages() -> None:
    scenario = {"competitor": "示例竞品", "track": "软件服务"}
    entry = prepare_competitor(scenario, [
        {"type": "official-search", "url": "https://bing.com/search?q=test", "direct_evidence": False},
        {"type": "web-search-result", "url": "https://example.com/product", "direct_evidence": True},
    ])
    assert len(entry["metadata"]["candidate_sources"]) == 1
    assert len(entry["official_sources"]) == 1


def test_prepare_competitor_applies_a_fetch_budget_per_bucket() -> None:
    scenario = {"competitor": "示例竞品", "track": "软件服务"}
    sources = [
        {
            "type": "web-search-result", "url": f"https://example{i}.com/product",
            "direct_evidence": True, "search_score": 100 - i,
        }
        for i in range(5)
    ]
    entry = prepare_competitor(scenario, sources, max_per_bucket=2)
    assert len(entry["official_sources"]) == 2
    deferred = [row for row in entry["metadata"]["candidate_sources"] if row.get("deferred_by_budget")]
    assert len(deferred) == 3


def test_quality_gate_requires_multiple_sources_and_buckets() -> None:
    status, reasons = quality_gate({
        "strong_sources": 1, "covered_buckets": 1,
        "precision_at_5": 1.0, "bad_domain_leakage": 0.0,
    })
    assert status == "review"
    assert len(reasons) == 2


def test_aggregate_results_reports_verda_style_quality_metrics() -> None:
    summary = aggregate_results([{
        "gate_status": "pass", "strong_sources": 3, "covered_buckets": 2,
        "precision_at_5": 0.8, "bad_domain_leakage": 0.0, "independent_domains": 3,
    }], 12.5)
    assert summary["pass_rate"] == 1.0
    assert summary["average_bucket_coverage"] == 0.5
    assert summary["independent_domains"] == 3
