#!/usr/bin/env python3
"""Run live-compatible M1 cases as a separate Kayn exploratory test set."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from kayn_sdk import KaynClient

from run_kayn_abcd_evaluation import (
    CONTRACT_GUARD_KEY,
    JUDGE_KEY,
    collect_run,
    create_tests,
    evaluated_files,
    ensure_metrics,
    execute_run,
    implementation_fingerprint,
    target_runtime_fingerprint,
    write_json,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures/evals/m1_shopping_replay_v2.json"


def _adapt_case(case: dict[str, Any]) -> dict[str, Any]:
    turn = case["turns"][0]
    response_type = turn["response_type"]
    context = turn["page_context"]
    page_context = {
        key: value
        for key, value in context.items()
        if key in {"page_type", "search_query", "current_item_id"}
    }
    page_context["candidate_item_ids"] = [
        item["item_id"] for item in context.get("candidate_refs", [])
    ]
    assertions = [
        {
            "assertion_id": "response_contract",
            "target": "metadata.response_type",
            "operator": "eq",
            "expected": response_type,
        }
    ]
    if response_type in {"recommendation", "product_list", "comparison"}:
        collection = "candidates" if response_type == "recommendation" else "products"
        assertions.append(
            {
                "assertion_id": "product_ids",
                "target": f"metadata.payload.{collection}[*].item_id",
                "operator": "set_eq",
                "expected": turn["item_ids"],
            }
        )
    return {
        "scenario_id": f"m1_{case['id']}",
        "category": case["category"],
        "turns": [{"role": "user", "content": turn["message"]}],
        "page_context": page_context,
        "expected_goal": {
            "item_ids": turn["item_ids"],
            "answer_contains": turn.get("answer_contains", []),
            "reason_code": turn.get("reason_code"),
        },
        "required_tools": turn.get("required_tools", []),
        "forbidden_tools": turn.get("forbidden_tools", []),
        "expected_response_type": response_type,
        "hard_assertions": assertions,
        "tags": ["m1", case["category"]],
        "failure_mode": "none",
    }


def _selected_cases() -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    fixture = json.loads(FIXTURE.read_text("utf-8"))
    selected: list[dict[str, Any]] = []
    excluded: list[dict[str, str]] = []
    for case in fixture["cases"]:
        if len(case["turns"]) != 1:
            reason = "simulator cannot pin per-turn page context"
        elif any(key in case for key in ("reviews", "snapshot_currency", "failure_paths")):
            reason = "case requires deterministic data or fault injection"
        else:
            selected.append(_adapt_case(case))
            continue
        excluded.append({"id": case["id"], "reason": reason})
    return selected, excluded


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:3100")
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--target-id", required=True)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, default=ROOT / "artifacts/kayn-evaluations")
    parser.add_argument("--wait-seconds", type=int, default=3600)
    args = parser.parse_args()
    token = os.getenv("KAYN_PLATFORM_API_TOKEN", "").strip()
    if not token:
        parser.error("KAYN_PLATFORM_API_TOKEN is required")
    source = json.loads(args.source_manifest.read_text("utf-8"))
    args.generation_profile_id = source["generation_profile_id"]
    args.judge_profile_id = source["judge_profile_id"]
    selected, excluded = _selected_cases()
    if not selected:
        raise RuntimeError("M1 has no live-compatible Kayn cases")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output_dir = args.output_root / f"{stamp}-m1-exploration"
    output_dir.mkdir(parents=True, exist_ok=False)
    manifest = {
        "schema_version": 1,
        "purpose": "M1 exploratory Kayn evaluation; not release-gate evidence",
        "fixture_sha256": hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
        "selected_case_ids": [item["scenario_id"] for item in selected],
        "excluded_cases": excluded,
        "project_id": args.project_id,
        "target_id": args.target_id,
        "implementation_fingerprint": implementation_fingerprint(
            ROOT, evaluated_files(ROOT)
        ),
        "target_runtime_fingerprint": None,
        "run_id": None,
    }
    write_json(output_dir / "manifest.json", manifest)
    client = KaynClient(api_token=token, base_url=args.base_url)
    try:
        scope = f"/api/v1/workspaces/{args.workspace_id}/projects/{args.project_id}"
        metrics = ensure_metrics(client, scope)
        batch = create_tests(client, scope, "SINGLE_TURN", selected, stamp)
        suite = client.api.request(
            "POST",
            f"{scope}/test-sets",
            json={
                "name": f"TalonMart M1 探索单轮 {stamp}",
                "description": "M1 fixed replay live-compatible subset; exploratory only",
                "testType": "SINGLE_TURN",
                "memberIds": batch["testIds"],
                "metricIds": [metrics[JUDGE_KEY], metrics[CONTRACT_GUARD_KEY]],
            },
        )
        run = execute_run(
            client,
            args,
            scope,
            suite_id=str(suite["id"]),
            metric_ids=[metrics[JUDGE_KEY], metrics[CONTRACT_GUARD_KEY]],
            model_profile_ids=[args.judge_profile_id],
            max_concurrency=1,
        )
        manifest["run_id"] = run["runId"]
        manifest["test_set_id"] = suite["id"]
        manifest["test_id_to_case"] = dict(
            zip(batch["testIds"], manifest["selected_case_ids"], strict=True)
        )
        result = collect_run(client, scope, str(run["runId"]))
        manifest["target_runtime_fingerprint"] = target_runtime_fingerprint(result, {})
        write_json(output_dir / "results.json", result)
        write_json(output_dir / "manifest.json", manifest)
        summary = result["summary"]
        if (
            summary["counts"]["total"] != len(selected)
            or len(result["items"]) != len(selected)
            or manifest["target_runtime_fingerprint"]
            != manifest["implementation_fingerprint"]
        ):
            raise RuntimeError("Kayn run is incomplete or used a different Agent build")
        for detail in result["details"]:
            guards = [
                metric
                for metric in detail["evidence"]["metricResults"]
                if metric["metricKey"] == CONTRACT_GUARD_KEY
            ]
            if len(guards) != 1 or guards[0]["passed"] is not True:
                raise RuntimeError("Kayn contract guard failed or did not score a case")
        print(
            json.dumps(
                {
                    "run_id": run["runId"],
                    "total": summary["counts"]["total"],
                    "pass_rate": summary["passRate"],
                    "output_dir": str(output_dir),
                },
                ensure_ascii=False,
            )
        )
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
