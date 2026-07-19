"""运行真实五智能体流程，并把结果保存为演示数据。"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

load_dotenv(PROJECT_ROOT / ".env")

from src.models.output import AgentRole, AgentStatus
from src.intake.hydrate import hydrate_sources_for_analysis
from src.pipeline import run_pipeline_custom


PRESETS = {
    "ai-coding": {
        "track": "AI 代码助手",
        "cache_dir": PROJECT_ROOT / "data" / "competitor-data" / "ai-coding",
        "output": PROJECT_ROOT / "data" / "demo-fallback.json",
        "threat_target": {
            "name": "GitHub Copilot",
            "positioning": "面向企业和专业开发者的 AI 代码助手",
            "target_users": "企业开发团队和专业开发者",
            "core_capabilities": "代码补全、对话、代码审查、IDE 与 GitHub 工作流集成",
            "competitive_concern": "Agent 编码工具和 AI 原生 IDE 正在分流专业开发者",
        },
    },
    "milktea": {
        "track": "新茶饮",
        "cache_dir": PROJECT_ROOT / "data" / "competitor-data" / "milktea",
        "output": PROJECT_ROOT / "data" / "demo-fallback-milktea.json",
        "threat_target": {
            "name": "霸王茶姬",
            "positioning": "以原叶鲜奶茶和国风体验为核心的新茶饮品牌",
            "target_users": "注重品质、健康和品牌体验的年轻消费者",
            "core_capabilities": "原叶鲜奶茶、品牌体验、门店扩张和供应链运营",
            "competitive_concern": "低价品牌上探与中高端品牌下沉同时加剧竞争",
        },
    },
}


def load_competitors(cache_dir: Path, target_name: str) -> list[dict]:
    """读取指定赛道缓存，并排除威胁目标自身。"""
    competitors: list[dict] = []
    for path in sorted(cache_dir.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        company = str(data.get("company") or path.stem).strip()
        if company.casefold() == target_name.casefold():
            continue
        competitors.append(data)
    return competitors


def validate_outputs(outputs) -> None:
    """确保演示数据至少包含成功的 Collector、QA 和 Writer。"""
    by_role = {output.role: output for output in outputs}
    required = (AgentRole.COLLECTOR, AgentRole.QA, AgentRole.WRITER)
    failed = [
        role.value
        for role in required
        if role not in by_role or by_role[role].status != AgentStatus.COMPLETED
    ]
    if failed:
        raise RuntimeError(f"关键 Agent 未成功完成：{', '.join(failed)}")


async def run_preset(name: str, enable_agent_tools: bool = False) -> Path:
    """运行一个预设并返回写入的结果文件。"""
    preset = PRESETS[name]
    threat_target = preset["threat_target"]
    competitors = load_competitors(preset["cache_dir"], threat_target["name"])
    if not competitors:
        raise RuntimeError(f"没有在 {preset['cache_dir']} 找到竞品缓存")

    competitors = await hydrate_sources_for_analysis(competitors, preset["track"])
    for competitor in competitors:
        metrics = competitor.get("metadata", {}).get("evidence_relevance", {})
        print(
            f"[{competitor.get('company', '未知竞品')}] "
            f"验收={metrics.get('accepted_sources', 0)}/{metrics.get('evaluated_sources', 0)}，"
            f"P@5={float(metrics.get('precision_at_5', 0) or 0):.0%}，"
            f"低质泄漏={float(metrics.get('bad_domain_leakage', 0) or 0):.0%}"
        )

    outputs = await run_pipeline_custom(
        preset["track"],
        competitors,
        threat_target,
        enable_agent_tools=enable_agent_tools,
    )
    validate_outputs(outputs)
    output_path = preset["output"]
    output_path.write_text(
        json.dumps([output.model_dump() for output in outputs], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return output_path


def cli(argv: list[str] | None = None) -> int:
    """解析命令行参数并运行选定预设。"""
    parser = argparse.ArgumentParser(description="运行 RivalTrackAgent 真实五智能体流程")
    parser.add_argument("--preset", choices=sorted(PRESETS), default="ai-coding")
    parser.add_argument("--agent-tools", action="store_true", help="启用 Collector ToolNode")
    args = parser.parse_args(argv)
    output_path = asyncio.run(run_preset(args.preset, args.agent_tools))
    print(f"已保存到：{output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
