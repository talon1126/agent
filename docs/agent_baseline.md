# Agent Baseline A1

Baseline ID: `a1-local-mock-2026-09-20`

This report freezes the current AImodel routing and public response behavior. It
is a regression reference, not a claim about production model quality or
latency.

## Environment

- Baseline commit: `d42af26788665143524c9e219c6abd74218db3c6`
- Capture date: `2026-09-20`
- Runtime: CPython 3.12.12 on Windows
- Model: injected deterministic runner; no DashScope request was made
- External services: mocked (`mock-api`, RAG MCP, and Tavily)
- Fixture: `fixtures/evals/shopping_agent_baseline.json`
- Route config SHA-256: `ec2633c157233bb3e8849561e5b13c6508009fc6cd919e89f650903e00642c16`

The fixture contains public, synthetic shopping prompts only. Trace summaries
store input fingerprints rather than raw prompts and contain no credentials,
headers, user identifiers, addresses, or external response bodies.

## Route Inventory

The 16 configured routes reduce to six actions and a fixed tool boundary.

| Route | Action | Primary collection | Allowed tools |
| --- | --- | --- | --- |
| `support.presale.buying_recommendation` | `rag` | `shopping_guides` | `rag_tool` |
| `support.presale.parameter_consulting` | `rag` | `shopping_guides` | `rag_tool` |
| `support.presale.comparison` | `rag` | `shopping_guides` | `rag_tool` |
| `support.presale.price_promotion` | `product_api` | none | detail, catalog search |
| `support.usage.operation_guide` | `rag` | `faq` | `rag_tool` |
| `support.usage.troubleshooting` | `rag` | `faq` | `rag_tool` |
| `support.usage.maintenance` | `rag` | `faq` | `rag_tool` |
| `support.aftersales.order_status` | `order_api` | none | `get_order_status` |
| `support.aftersales.shipping_policy` | `rag` | `policies` | `rag_tool` |
| `support.aftersales.return_exchange` | `rag` | `policies` | `rag_tool` |
| `support.aftersales.installation_service_limits` | `rag` | `policies` | `rag_tool` |
| `support.aftersales.account_invoice` | `rag` | `policies` | `rag_tool` |
| `support.aftersales.service_script` | `rag` | `manual` | `rag_tool` |
| `external.public_market.web_research` | `web` | none | `search_web_with_tavily` |
| `chat.chat_all.greeting` | `direct` | none | none |
| `chat.chat_all.out_of_scope` | `refuse` | none | none |

`installation_service_limits` is the current positive multi-collection sample:
the scored candidates select `policies` and `faq`, in that order. Every route
case is executed twice in the regression suite to freeze route, candidate, and
allowed-tool determinism.

## Fast Paths

- Product links are parsed and fetched from `mock-api` before the Agent runner.
  Successful detail results may create recommended links; failed results do not.
- `product_api`, `order_api`, `rag`, and `web` constrain the Agent to the tools
  assigned to that action.
- `direct` and `refuse` expose no tools, but they still use the model runner.
  They are tool-free paths rather than true model-free fast paths.
- Conversation creation and user-message persistence happen before generation.
  Assistant persistence happens after the visible answer is complete.
- A successful stream emits one or more `status` events, then visible `delta`
  events, then exactly one terminal `done`. Tool JSON and internal trace/chunk
  identifiers are filtered from visible output.

## Failure Baseline

| Failure | Current observable behavior |
| --- | --- |
| Tavily key missing | No HTTP request; tool returns `web_search_unavailable` with `missing_tavily_api_key`. |
| RAG unavailable | Tool returns a readable `ok=false` business result; the Agent decides the final wording. |
| `mock-api` non-200 | Tool records `mock_api_status_<code>` and produces no recommended link. |
| Generation exception | Stream terminates with one `error` event and no `done` event. |
| Memory setup exception | Stream emits an `error` event and stops before generation. |
| Assistant persistence exception | Already generated output is retained and `done` is still emitted. |

Known baseline limitations are intentionally not fixed in A1: degradation text
is not a structured response, direct/refuse still incur a model call, and real
service latency is not represented by the local mock timings.

## Performance Baseline

The fixture contains 12 sanitized trace summaries. Timings come from a local,
deterministic mocked run and show orchestration/test-double overhead only.
They must not be compared with production SLOs or real LLM/RAG latency.

| Metric | Min | Median | Mean | Max |
| --- | ---: | ---: | ---: | ---: |
| `model_ms` | 0.008 | 0.010 | 0.010 | 0.014 |
| `tool_ms` | 0.000 | 0.035 | 0.193 | 0.614 |
| `total_ms` | 0.301 | 0.387 | 0.512 | 0.911 |

`model_ms` measures the injected runner, `tool_ms` measures mocked adapter work,
and `total_ms` measures the whole local request path. The 12 outcomes comprise
eight successes without a failure category and one each for
`tavily_unconfigured`, `rag_unavailable`, `mock_api_error`, and
`generation_error`.

## Reproduce

Run the frozen A1 acceptance contract:

```powershell
uv run pytest tests/acceptance/a1/test_a1_baseline_contract.py -q
```

Run the taskbook verification command:

```powershell
uv run --project services/ai-service pytest services/ai-service/tests/test_aimodel_agent.py services/ai-service/tests/test_aimodel_rag_tool.py services/ai-service/tests/test_aimodel_memory.py -q
```

Both commands use deterministic tests and do not require live model, RAG,
Tavily, PostgreSQL, or mock-api services.
