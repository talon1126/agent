#!/usr/bin/env python3
"""Run the TalonMart A-D Golden Set through Kayn's public platform client."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from kayn_sdk import KaynClient, KaynRunCommand, KaynWaitCommand

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "services/ai-service"))
from app.kayn_runtime_fingerprint import (  # noqa: E402
    evaluated_files,
    implementation_fingerprint,
)


JUDGE_KEY = "talonmart_abcd_quality"
JUDGE_VERSION = "1.2.0"
CONTRACT_GUARD_KEY = "talonmart_contract_guard"
CONTRACT_GUARD_VERSION = "1.0.0"
CONVERSATION_METRICS = (
    "goal_achievement",
    "context_retention",
    "turn_relevancy",
    "role_adherence",
)
JUDGE_PROMPT = """你是 TalonMart 购物 Agent 的严格质量评审。测试契约位于
expected_output；多轮时位于 metadata.scenario_context.expected_contract。结合 output、
metadata、tool_calls、retrieved_context 和 conversation 逐项检查：
1. 响应类型和结构是否符合 expected_response_type；
2. 购物目标、预算、品牌、规格、候选与冲突是否满足 expected_goal 和 hard_assertions；
3. required_tools 是否在需要时成功调用，forbidden_tools 是否始终未调用；
4. 商品事实、评论结论、推荐理由与引用是否能被上下文或工具证据支持；
5. 无候选、工具失败和不确定事实是否给出诚实且可继续执行的恢复终态；
6. 不得编造商品、库存、价格、评论、来源或已经完成的副作用。
工具名已在 expected_output 中标准化：product_search 表示商品搜索，product_snapshot
表示商品详情及配送快照，product_reviews 表示评论读取。评审时以这些标准名匹配
tool_calls，不得因为内部步骤名、旧业务别名或大小写差异重复扣分。
除非 failure_mode 明确声明工具失败，否则内部工具超时或降级 fallback 仍表示主要目标
未完成，不能判为通过，最高 0.69。expected_response_type 不匹配时同样最高 0.69。
当契约要求商品、推荐或比较结果时，空 recommended_items 不能被视为真空满足品牌、
预算或规格约束。通用“稍后重试/缩小范围”若未指出具体失败原因、保留的约束和明确
下一步，不能算可继续执行的恢复终态，最高 0.59。
任一硬约束违规、禁用工具调用、关键事实无证据、错误业务终态，最高 0.59；
必需工具缺失或主要目标未完成，最高 0.69；全部硬条件满足但有轻微表达缺陷为
0.80-0.89；完整、可解释且证据一致为 0.90-1.00。证据字段写出具体通过项或失败项。"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:18080")
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--target-id", required=True)
    parser.add_argument("--generation-profile-id", required=True)
    parser.add_argument("--judge-profile-id", required=True)
    parser.add_argument(
        "--fixture", default="fixtures/evals/shopping_agent_scenarios.json"
    )
    parser.add_argument("--output-root", default="artifacts/kayn-evaluations")
    parser.add_argument("--wait-seconds", type=int, default=7200)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    token = os.getenv("KAYN_PLATFORM_API_TOKEN", "").strip()
    if not token:
        raise SystemExit("KAYN_PLATFORM_API_TOKEN is required")

    repo = Path(__file__).resolve().parents[1]
    source_files = evaluated_files(repo)
    fixture_path = (repo / args.fixture).resolve()
    fixture_bytes = fixture_path.read_bytes()
    fixture = json.loads(fixture_bytes)
    scenarios = fixture["scenarios"]
    runnable_scenarios = [
        scenario
        for scenario in scenarios
        if scenario.get("failure_mode") != "tool_timeout"
    ]
    deferred_scenarios = [
        scenario
        for scenario in scenarios
        if scenario.get("failure_mode") == "tool_timeout"
    ]
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output_dir = (repo / args.output_root / stamp).resolve()
    output_dir.mkdir(parents=True, exist_ok=False)

    client = KaynClient(api_token=token, base_url=args.base_url)
    try:
        scope = f"/api/v1/workspaces/{args.workspace_id}/projects/{args.project_id}"
        metric_ids = ensure_metrics(client, scope)
        single = [
            scenario for scenario in runnable_scenarios if len(scenario["turns"]) == 1
        ]
        multi = [
            scenario for scenario in runnable_scenarios if len(scenario["turns"]) > 1
        ]

        single_batch = create_tests(client, scope, "SINGLE_TURN", single, stamp)
        multi_batch = create_tests(client, scope, "MULTI_TURN", multi, stamp)
        single_set = create_test_set(
            client,
            scope,
            name=f"TalonMart A-D 正式评测 单轮 {stamp}",
            test_type="SINGLE_TURN",
            member_ids=single_batch["testIds"],
            metric_ids=[metric_ids[JUDGE_KEY], metric_ids[CONTRACT_GUARD_KEY]],
        )
        multi_set = create_test_set(
            client,
            scope,
            name=f"TalonMart A-D 正式评测 多轮 {stamp}",
            test_type="MULTI_TURN",
            member_ids=multi_batch["testIds"],
            metric_ids=[metric_ids[JUDGE_KEY], metric_ids[CONTRACT_GUARD_KEY]]
            + [metric_ids[key] for key in CONVERSATION_METRICS],
        )

        manifest = {
            "schema_version": 2,
            "created_at": datetime.now(UTC).isoformat(),
            "git_commit": git_commit(repo),
            "git_worktree_dirty": evaluated_worktree_dirty(repo, source_files),
            "implementation_fingerprint": implementation_fingerprint(
                repo, source_files
            ),
            "evaluated_files": list(source_files),
            "fixture": str(fixture_path.relative_to(repo)).replace("\\", "/"),
            "fixture_sha256": hashlib.sha256(fixture_bytes).hexdigest(),
            "fixture_version": fixture["metadata"]["version"],
            "workspace_id": args.workspace_id,
            "project_id": args.project_id,
            "target_id": args.target_id,
            "generation_profile_id": args.generation_profile_id,
            "judge_profile_id": args.judge_profile_id,
            "metric_ids": metric_ids,
            "deferred_scenarios": [
                {
                    "scenario_id": scenario["scenario_id"],
                    "reason": "mock-api has no deterministic tool-timeout injection",
                }
                for scenario in deferred_scenarios
            ],
            "test_sets": {
                "single_turn": single_set["id"],
                "multi_turn": multi_set["id"],
            },
            "test_id_to_scenario": {
                **dict(
                    zip(
                        single_batch["testIds"],
                        [item["scenario_id"] for item in single],
                    )
                ),
                **dict(
                    zip(multi_batch["testIds"], [item["scenario_id"] for item in multi])
                ),
            },
            "runs": {},
        }
        write_json(output_dir / "manifest.json", manifest)

        single_run = execute_run(
            client,
            args,
            scope,
            suite_id=single_set["id"],
            metric_ids=[metric_ids[JUDGE_KEY], metric_ids[CONTRACT_GUARD_KEY]],
            model_profile_ids=[args.judge_profile_id],
            max_concurrency=1,
        )
        manifest["runs"]["single_turn"] = single_run["runId"]
        write_json(output_dir / "manifest.json", manifest)
        single_result = collect_run(client, scope, single_run["runId"])
        write_json(output_dir / "single-turn-results.json", single_result)

        multi_run = execute_run(
            client,
            args,
            scope,
            suite_id=multi_set["id"],
            metric_ids=[metric_ids[JUDGE_KEY], metric_ids[CONTRACT_GUARD_KEY]]
            + [metric_ids[key] for key in CONVERSATION_METRICS],
            model_profile_ids=[args.generation_profile_id, args.judge_profile_id],
            max_concurrency=1,
        )
        manifest["runs"]["multi_turn"] = multi_run["runId"]
        write_json(output_dir / "manifest.json", manifest)
        multi_result = collect_run(client, scope, multi_run["runId"])
        write_json(output_dir / "multi-turn-results.json", multi_result)
        manifest["target_runtime_fingerprint"] = target_runtime_fingerprint(
            single_result, multi_result
        )
        write_json(output_dir / "manifest.json", manifest)

        report = build_report(manifest, scenarios, single_result, multi_result)
        (output_dir / "REPORT.md").write_text(report, encoding="utf-8")
        print(json.dumps({"output_dir": str(output_dir), "runs": manifest["runs"]}))
    finally:
        client.close()
    return 0


def ensure_metrics(client: KaynClient, scope: str) -> dict[str, str]:
    existing = client.api.request_value("GET", f"{scope}/metrics")
    if not isinstance(existing, list):
        raise RuntimeError("Kayn metric catalog returned an invalid response")
    identities = {(item["key"], item["version"]): item for item in existing}
    definitions = [
        {
            "key": JUDGE_KEY,
            "kind": "JUDGE",
            "version": JUDGE_VERSION,
            "framework": "native",
            "scope": ["SINGLE_TURN", "CONVERSATION"],
            "higherIsBetter": True,
            "threshold": 0.8,
            "config": {
                "score_type": "NUMERIC",
                "model": "openai/qwen-plus",
                "evaluation_prompt": JUDGE_PROMPT,
                "timeout_seconds": 180,
                "max_output_tokens": 1500,
                "parameters": {"temperature": 0},
                "requires_expected_output": False,
                "min_score": 0,
                "max_score": 1,
                "threshold_operator": ">=",
            },
            "status": "ACTIVE",
        }
    ]
    for key in CONVERSATION_METRICS:
        definitions.append(
            {
                "key": key,
                "kind": "JUDGE",
                "version": "1.0.0",
                "framework": "native",
                "scope": ["CONVERSATION"],
                "higherIsBetter": True,
                "threshold": 0.8,
                "config": {
                    "model": "openai/qwen-plus",
                    "threshold_operator": ">=",
                    "timeout_seconds": 180,
                    "max_output_tokens": 1500,
                    "parameters": {"temperature": 0},
                },
                "status": "ACTIVE",
            }
        )

    result: dict[str, str] = {}
    for definition in definitions:
        identity = (definition["key"], definition["version"])
        found = identities.get(identity)
        if found is None:
            found = client.api.request("POST", f"{scope}/metrics", json=definition)
        if found.get("status") != "ACTIVE":
            raise RuntimeError(f"metric {identity[0]} {identity[1]} is not active")
        result[identity[0]] = str(found["id"])
    guard = identities.get((CONTRACT_GUARD_KEY, CONTRACT_GUARD_VERSION))
    if guard is None or guard.get("status") != "ACTIVE":
        raise RuntimeError(
            "TalonMart contract guard is not online; restart the Kayn Agent connector first"
        )
    result[CONTRACT_GUARD_KEY] = str(guard["id"])
    return result


def create_tests(
    client: KaynClient,
    scope: str,
    test_type: str,
    scenarios: list[dict[str, Any]],
    stamp: str,
) -> dict[str, Any]:
    drafts = [
        single_turn_draft(scenario, stamp)
        if test_type == "SINGLE_TURN"
        else multi_turn_draft(scenario, stamp)
        for scenario in scenarios
    ]
    return client.api.request(
        "POST",
        f"{scope}/tests/batch",
        json={"testType": test_type, "tests": drafts},
    )


def single_turn_draft(scenario: dict[str, Any], stamp: str) -> dict[str, Any]:
    envelope = {
        "_talonmart_evaluation_version": 1,
        "message": scenario["turns"][0]["content"],
        "links": [],
        "page_context": normalize_page_context(scenario["page_context"]),
    }
    return {
        "externalId": f"{stamp}-{scenario['scenario_id']}",
        "name": f"{scenario['scenario_id']} [{stamp}]",
        "input": {
            "input": json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
        },
        "expectedOutput": expected_contract(scenario),
        "context": [],
        "metadata": {
            "scenario_id": scenario["scenario_id"],
            "category": scenario["category"],
            "tags": scenario["tags"],
            "stages": stages_for(scenario),
        },
    }


def multi_turn_draft(scenario: dict[str, Any], stamp: str) -> dict[str, Any]:
    contract = expected_contract(scenario)
    first_user_turn = next(
        turn["content"] for turn in scenario["turns"] if turn["role"] == "user"
    )
    goal = (
        f"从“{first_user_turn}”开始，完成以下购物目标并获得可核验终态："
        + json.dumps(scenario["expected_goal"], ensure_ascii=False, sort_keys=True)
        + f"。期望最终响应类型：{scenario['expected_response_type']}。"
    )
    return {
        "externalId": f"{stamp}-{scenario['scenario_id']}",
        "name": f"{scenario['scenario_id']} [{stamp}]",
        "expectedOutput": contract,
        "metadata": {
            "scenario_id": scenario["scenario_id"],
            "category": scenario["category"],
            "tags": scenario["tags"],
            "stages": stages_for(scenario),
        },
        "persona": "严格依据给定场景事实购物的消费者，不添加场景外需求。",
        "goal": goal,
        "boundaries": [
            {
                "key": "no_out_of_scope_facts",
                "description": "不得虚构场景外预算、品牌、规格、商品或个人信息。",
                "action": "STOP",
            }
        ],
        "scenarioContext": {
            "scenario_id": scenario["scenario_id"],
            "category": scenario["category"],
            "expected_contract": contract,
            "reference_turns": scenario["turns"],
            "page_context": normalize_page_context(scenario["page_context"]),
            "targetContext": normalize_page_context(scenario["page_context"]),
            "stages": stages_for(scenario),
        },
        "maxTurns": min(6, max(4, len(scenario["turns"]) + 1)),
        "terminationPolicy": {
            "stopOnGoal": True,
            "stopOnBoundary": True,
            "timeoutSeconds": 300,
        },
    }


def normalize_page_context(source: dict[str, Any]) -> dict[str, Any]:
    result = {
        key: source[key]
        for key in ("page_type", "search_query", "current_item_id")
        if source.get(key) is not None
    }
    result["candidate_refs"] = [
        {"item_id": item_id} for item_id in source.get("candidate_item_ids", [])
    ]
    return result


def expected_contract(scenario: dict[str, Any]) -> dict[str, Any]:
    contract = {
        key: scenario[key]
        for key in (
            "scenario_id",
            "category",
            "expected_goal",
            "required_tools",
            "forbidden_tools",
            "expected_response_type",
            "hard_assertions",
            "failure_mode",
        )
    }
    contract["required_tools"] = [
        canonical_tool_name(name) for name in contract["required_tools"]
    ]
    contract["forbidden_tools"] = [
        canonical_tool_name(name) for name in contract["forbidden_tools"]
    ]
    return contract


def canonical_tool_name(name: str) -> str:
    aliases = {
        "search_product_catalog": "product_search",
        "search_products": "product_search",
        "get_product_detail_from_link": "product_snapshot",
        "get_delivery_options": "product_snapshot",
        "get_product_reviews": "product_reviews",
    }
    return aliases.get(name, name)


def create_test_set(
    client: KaynClient,
    scope: str,
    *,
    name: str,
    test_type: str,
    member_ids: list[str],
    metric_ids: list[str],
) -> dict[str, Any]:
    return client.api.request(
        "POST",
        f"{scope}/test-sets",
        json={
            "name": name,
            "description": "TalonMart Agent 阶段 A-D Golden Set 正式诊断",
            "testType": test_type,
            "memberIds": member_ids,
            "metricIds": metric_ids,
        },
    )


def execute_run(
    client: KaynClient,
    args: argparse.Namespace,
    scope: str,
    *,
    suite_id: str,
    metric_ids: list[str],
    model_profile_ids: list[str],
    max_concurrency: int,
) -> dict[str, Any]:
    request: dict[str, Any] = {
        "suiteId": suite_id,
        "targetId": args.target_id,
        "metricIds": metric_ids,
        "modelProfileIds": model_profile_ids,
        "toolCatalogVersionIds": [],
        "maxConcurrency": max_concurrency,
        "timeoutSeconds": 300,
    }
    preflight = client.api.request("POST", f"{scope}/runs/preflight", json=request)
    if preflight.get("runnable") is not True:
        raise RuntimeError(f"run preflight blocked: {preflight.get('blockingErrors')}")
    warnings = preflight.get("warnings") or []
    if warnings:
        request["acknowledgedWarnings"] = [item["code"] for item in warnings]
    run = KaynRunCommand(client, args.workspace_id, args.project_id).execute(request)
    return KaynWaitCommand(client, args.workspace_id, args.project_id).execute(
        str(run["runId"]),
        timeout_seconds=args.wait_seconds,
        poll_interval_seconds=2,
    )


def collect_run(client: KaynClient, scope: str, run_id: str) -> dict[str, Any]:
    summary = client.api.request("GET", f"{scope}/runs/{run_id}/results")
    page = client.api.request(
        "GET", f"{scope}/runs/{run_id}/test-results", params={"size": "100"}
    )
    items = page.get("items") or []
    details = [
        client.api.request(
            "GET", f"{scope}/runs/{run_id}/test-results/{item['testResultId']}"
        )
        for item in items
    ]
    return {"summary": summary, "items": items, "details": details}


def build_report(
    manifest: dict[str, Any],
    scenarios: list[dict[str, Any]],
    single: dict[str, Any],
    multi: dict[str, Any],
) -> str:
    by_id = {scenario["scenario_id"]: scenario for scenario in scenarios}
    test_map = manifest["test_id_to_scenario"]
    results = [*single["items"], *multi["items"]]
    stage_counts: dict[str, Counter[str]] = defaultdict(Counter)
    category_counts: dict[str, Counter[str]] = defaultdict(Counter)
    failures: list[tuple[str, str, str, int]] = []
    passes: list[str] = []
    for item in results:
        scenario_id = test_map[str(item["testId"])]
        scenario = by_id[scenario_id]
        verdict = str(item["verdict"])
        category_counts[scenario["category"]][verdict] += 1
        for stage in stages_for(scenario):
            stage_counts[stage][verdict] += 1
        if verdict != "PASS":
            failures.append(
                (
                    scenario_id,
                    verdict,
                    str(item.get("errorCode") or "-"),
                    int(item.get("latencyMs") or 0),
                )
            )
        else:
            passes.append(scenario_id)

    lines = [
        "# TalonMart Agent A-D Kayn 正式测评",
        "",
        f"- Git 基线：`{manifest['git_commit']}`",
        f"- 被测工作区：{'有未提交变更' if manifest.get('git_worktree_dirty') else '干净'}",
        f"- 被测实现指纹：`{manifest.get('implementation_fingerprint', '-')}`",
        f"- Golden Set：`{manifest['fixture_version']}` / `{manifest['fixture_sha256']}`",
        f"- 单轮 Run：`{manifest['runs']['single_turn']}`",
        f"- 多轮 Run：`{manifest['runs']['multi_turn']}`",
        f"- 评审指标：`{JUDGE_KEY}` `{JUDGE_VERSION}`",
        f"- 主评分样例：{len(results)}；待故障注入验证：{len(manifest.get('deferred_scenarios', []))}",
        "- 判定：0.80 及以上通过；硬约束、工具边界、事实与终态错误按严格上限扣分。",
        "",
        "## 平台结果",
        "",
        "| 类型 | 状态 | 样例 | 通过率 | 失败 | 错误 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name, result in (("单轮", single), ("多轮", multi)):
        summary = result["summary"]
        counts = summary["counts"]
        lines.append(
            f"| {name} | {summary['status']} | {counts['total']} "
            f"| {summary['passRate']}% | {counts.get('failed', 0)} | {counts.get('errored', 0)} |"
        )

    lines.extend(
        [
            "",
            "## 多轮指标",
            "",
            "| 指标 | 样例 | 可评分 | 均分 | 通过 | 评审错误 |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for metric, values in conversation_metric_stats(multi).items():
        scored = [value[0] for value in values if value[0] is not None]
        average = sum(scored) / len(scored) if scored else 0
        lines.append(
            f"| `{metric}` | {len(values)} | {len(scored)} | {average:.3f} "
            f"| {sum(1 for _, passed, _ in values if passed)} "
            f"| {sum(1 for _, _, errored in values if errored)} |"
        )

    lines.extend(
        [
            "",
            "## 阶段诊断",
            "",
            "| 阶段 | 样例 | 通过 | 失败/错误 | 通过率 |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for stage in "ABCD":
        counts = stage_counts[stage]
        total = sum(counts.values())
        passed = counts["PASS"]
        lines.append(
            f"| {stage} | {total} | {passed} | {total - passed} | "
            f"{(passed / total * 100 if total else 0):.1f}% |"
        )

    lines.extend(
        ["", "## 场景诊断", "", "| 场景类别 | 样例 | 通过率 |", "|---|---:|---:|"]
    )
    for category in sorted(category_counts):
        counts = category_counts[category]
        total = sum(counts.values())
        lines.append(f"| {category} | {total} | {counts['PASS'] / total * 100:.1f}% |")

    lines.extend(["", "## 未通过样例", ""])
    if failures:
        lines.extend(
            ["| scenario_id | 判定 | 错误码 | 延迟(ms) |", "|---|---|---|---:|"]
        )
        lines.extend(
            f"| `{scenario_id}` | {verdict} | {error} | {latency} |"
            for scenario_id, verdict, error, latency in failures
        )
    else:
        lines.append("无。")

    lines.extend(["", "## 已通过样例", ""])
    if passes:
        lines.extend(f"- `{scenario_id}`" for scenario_id in sorted(passes))
    else:
        lines.append("无。")

    lines.extend(
        [
            "",
            "## 待故障注入验证",
            "",
        ]
    )
    deferred = manifest.get("deferred_scenarios", [])
    if deferred:
        lines.extend(
            f"- `{item['scenario_id']}`：{item['reason']}" for item in deferred
        )
    else:
        lines.append("无。")

    lines.extend(
        [
            "",
            "## 解释边界",
            "",
            "- 这是 A-D 功能诊断，不替代任务书阶段门禁，也不代表 E/F 发布质量门禁通过。",
            "- 单轮页面上下文经版本化评测信封还原，可覆盖 A3 请求协议。",
            "- 多轮由 Kayn 用户模拟器依据目标生成，不是对 fixture 固定话术的逐字回放。",
            "- 多轮 scenarioContext 供模拟器与评审使用；只有显式 targetContext 会作为 Agent page_context 透传。",
            "- tool_timeout 样例仅在 mock-api 支持确定性故障注入后进入主评分；本次不会把用户话术中的‘超时’当成真实工具故障。",
            "- 阶段通过率按关联场景的整体验证判定统计，不把同一场景拆成虚假的独立阶段成绩。",
            "",
        ]
    )
    return "\n".join(lines)


def conversation_metric_stats(
    multi: dict[str, Any],
) -> dict[str, list[tuple[float | None, bool, bool]]]:
    stats: dict[str, list[tuple[float | None, bool, bool]]] = defaultdict(list)
    for detail in multi.get("details") or ():
        metrics = (detail.get("evidence") or {}).get("metrics") or {}
        for key, result in metrics.items():
            score = result.get("score")
            outcomes = result.get("outcomes") or ()
            errored = any(outcome.get("error") for outcome in outcomes)
            stats[str(key)].append(
                (
                    float(score) if isinstance(score, int | float) else None,
                    result.get("passed") is True,
                    errored,
                )
            )
    return dict(sorted(stats.items()))


def stages_for(scenario: dict[str, Any]) -> list[str]:
    stages = {"A"}
    category = scenario["category"]
    if category in {"vague_need", "constraint_conflict"} or len(scenario["turns"]) > 1:
        stages.add("B")
    if category in {
        "hard_constraint",
        "comparison",
        "review_summary",
        "constraint_conflict",
        "no_candidates",
    }:
        stages.add("C")
    if scenario["required_tools"] or category in {
        "comparison",
        "review_summary",
        "no_candidates",
        "tool_failure",
    }:
        stages.add("D")
    return sorted(stages)


def git_commit(repo: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def evaluated_worktree_dirty(repo: Path, source_files: tuple[str, ...]) -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain", "--", *source_files],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return bool(result.stdout.strip())


def target_runtime_fingerprint(single: dict[str, Any], multi: dict[str, Any]) -> str | None:
    fingerprints: list[str] = []
    for detail in single.get("details") or []:
        metrics = [
            item
            for item in (detail.get("evidence") or {}).get("metricResults") or []
            if item.get("metricKey") == CONTRACT_GUARD_KEY
        ]
        if len(metrics) != 1:
            return None
        outcomes = metrics
        values = _outcome_fingerprints(outcomes)
        if len(values) != len(outcomes):
            return None
        fingerprints.extend(values)
    for detail in multi.get("details") or []:
        metric = ((detail.get("evidence") or {}).get("metrics") or {}).get(
            CONTRACT_GUARD_KEY
        ) or {}
        outcomes = metric.get("outcomes") or []
        if not outcomes:
            return None
        values = _outcome_fingerprints(outcomes)
        if len(values) != len(outcomes):
            return None
        fingerprints.extend(values)
    if not fingerprints or len(set(fingerprints)) != 1:
        return None
    return fingerprints[0]


def _outcome_fingerprints(outcomes: list[dict[str, Any]]) -> list[str]:
    values: list[str] = []
    for outcome in outcomes:
        matching = [
            item.get("sha256")
            for item in outcome.get("evidence") or []
            if isinstance(item, dict) and item.get("type") == "runtime_fingerprint"
        ]
        if len(matching) != 1 or not isinstance(matching[0], str):
            return []
        values.append(matching[0])
    return values


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    raise SystemExit(main())
