import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
ROOT = Path(__file__).resolve().parents[3]


def test_policy_rag_eval_questions_return_expected_clause_metadata():
    evals = json.loads((ROOT / "fixtures" / "evals" / "policy_rag_eval.json").read_text(encoding="utf-8"))

    for case in evals:
        response = client.post("/policies/search", json={"query": case["query"], "locale": "zh", "limit": 3})

        assert response.status_code == 200, case["id"]
        body = response.json()
        assert body["ok"] is True, case["id"]
        assert body["matches"], case["id"]
        assert body["matches"][0]["source_file"] == "fixtures/policies/after_sales_policy.zh.md", case["id"]
        assert body["matches"][0]["section"], case["id"]
        assert body["matches"][0]["clause_id"].startswith(case["expected_clause_prefix"]), case["id"]
        assert body["matches"][0]["text"], case["id"]
