import csv
import importlib.util
import re
import sys
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from types import ModuleType
from urllib.parse import urlsplit

import pytest


CURRENT_DOCS = [
    Path("docs/architecture.md"),
    Path("docs/architecture.zh.md"),
    Path("docs/demo-script.md"),
    Path("docs/demo-script.zh.md"),
    Path("docs/local-runbook.md"),
    Path("docs/local-runbook.zh.md"),
    Path("docs/warehouse-inventory-table-sync.md"),
    Path("docs/warehouse-inventory-table-sync.zh.md"),
    Path("docs/warehouse-view-template-builder.md"),
    Path("docs/warehouse-view-template-builder.zh.md"),
]

WAREHOUSE_AGENT_DOCS = [
    Path("docs/AGENTS/warehouse-agent/README.md"),
    Path("docs/AGENTS/warehouse-agent/business-boundary.md"),
    Path("docs/AGENTS/warehouse-agent/mock-api.md"),
    Path("docs/AGENTS/warehouse-agent/database-tables.md"),
]

DATA_OPS_CONTRACTS_PATH = Path(
    "services/data-ops/src/data_ops/core/contracts.py"
)
RPA_README_PATH = Path("rpa/yingdao/README.md")
RPA_WEB_PAGE_TEMPLATE_PATH = Path(
    "rpa/yingdao/templates/web-page-to-csv.md"
)
RPA_JD_PRODUCT_IMPLEMENTATION_PATH = Path(
    "rpa/yingdao/implementations/jd-product-export.md"
)
JD_PRODUCT_URLS_FIXTURE = Path("fixtures/rpa/jd_product_urls.csv")
JD_PRODUCT_EXPORT_FIXTURE = Path("fixtures/rpa/jd_product_export.csv")

COMMON_WEB_EXPORT_COLUMNS = (
    "dataset_type",
    "batch_id",
    "input_index",
    "source_url",
    "captured_at",
    "crawl_status",
    "error_code",
)
JD_PRODUCT_SITE_COLUMNS = (
    "jd_sku_id",
    "title",
    "display_price",
    "shop_name",
    "primary_image_url",
    "capture_region",
)


@lru_cache(maxsize=1)
def _load_data_ops_contracts() -> ModuleType:
    """Load the standalone J1 contract module before data-ops packaging exists.

    J4 owns the installable data-ops project and package markers. J1 still needs
    executable contract tests, so this helper loads the module directly from its
    documented source location without changing the process import path.

    Returns:
        The loaded contract module.

    Raises:
        AssertionError: If the module cannot be loaded from the specified path.
    """

    spec = importlib.util.spec_from_file_location(
        "talonmart_data_ops_contracts",
        DATA_OPS_CONTRACTS_PATH,
    )
    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _read_csv_rows(path: Path) -> tuple[tuple[str, ...], list[dict[str, str]]]:
    """Read one UTF-8 CSV fixture while preserving its declared column order.

    Args:
        path: Repository-relative CSV fixture path.

    Returns:
        A tuple containing the ordered header and all data rows.
    """

    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return tuple(reader.fieldnames or ()), list(reader)


def test_current_warehouse_docs_use_batch_location_inventory_model() -> None:
    for path in CURRENT_DOCS:
        text = path.read_text(encoding="utf-8")
        assert "item_vinda_tissue" in text or "batch + location" in text or "批次 + 库位" in text

    split_docs_text = "\n".join(path.read_text(encoding="utf-8") for path in WAREHOUSE_AGENT_DOCS)
    assert "item_vinda_tissue" in split_docs_text or "batch + location" in split_docs_text or "批次 + 库位" in split_docs_text

    for path in CURRENT_DOCS + WAREHOUSE_AGENT_DOCS:
        text = path.read_text(encoding="utf-8")
        assert "sku_bag_1" not in text
        assert "SKU + Warehouse" not in text
        assert '"SKU"' not in text
        assert '"Available"' not in text


def test_root_agents_doc_routes_to_split_warehouse_docs() -> None:
    text = Path("AGENTS.md").read_text(encoding="utf-8")

    assert "docs/AGENTS/warehouse-agent/README.md" in text
    assert "docs/AGENTS/warehouse-agent/mock-api.md" in text
    assert "sku_bag_1" not in text
    assert "SKU + Warehouse" not in text


def test_current_warehouse_docs_describe_live_view_templates() -> None:
    english = Path("docs/warehouse-view-template-builder.md").read_text(encoding="utf-8")
    chinese = Path("docs/warehouse-view-template-builder.zh.md").read_text(encoding="utf-8")

    for text in (english, chinese):
        assert "category_inventory_view" in text
        assert "low_stock_view" in text
        assert "expiring_inventory_view" in text
        assert "location_inventory_view" in text
        assert "batch_risk_view" in text
        assert "replenishment_candidate_view" in text


def test_rpa_data_contract_exposes_stable_extension_boundaries() -> None:
    """Protect the generic CSV contract from site-specific semantic drift."""

    contracts = _load_data_ops_contracts()

    web_contract = contracts.JD_PRODUCT_WEB_EXPORT_CONTRACT
    assert web_contract.dataset_type == "jd_product"
    assert web_contract.input_columns == ("input_index", "product_url")
    assert web_contract.common_output_columns == COMMON_WEB_EXPORT_COLUMNS
    assert web_contract.site_output_columns == JD_PRODUCT_SITE_COLUMNS
    assert web_contract.output_columns == (
        COMMON_WEB_EXPORT_COLUMNS + JD_PRODUCT_SITE_COLUMNS
    )
    assert web_contract.crawl_statuses == ("success", "partial", "failed")
    assert {
        "invalid_input",
        "navigation_failed",
        "page_timeout",
        "field_missing",
        "manual_verification_required",
        "access_restricted",
        "adapter_error",
    }.issubset(web_contract.error_codes)

    extension_contract = contracts.WebPageExportContract(
        dataset_type="example_listing",
        input_columns=("input_index", "page_url"),
        site_output_columns=("external_id",),
        error_codes=contracts.COMMON_ERROR_CODES + ("site_state_unknown",),
    )
    assert extension_contract.output_columns == (
        COMMON_WEB_EXPORT_COLUMNS + ("external_id",)
    )
    with pytest.raises(ValueError, match="must not shadow common columns"):
        contracts.WebPageExportContract(
            dataset_type="invalid_listing",
            input_columns=("input_index", "page_url"),
            site_output_columns=("source_url",),
        )

    dataset_contract = contracts.JD_PRODUCT_DATASET_CONTRACT
    assert dataset_contract.dataset_type == "jd_product"
    assert dataset_contract.required_columns == web_contract.output_columns
    assert dataset_contract.optional_columns == ("display_price_amount",)
    assert dataset_contract.column_types["display_price_amount"] == "decimal"
    assert set(dataset_contract.column_types) == set(dataset_contract.all_columns)
    assert dataset_contract.unique_by == ("batch_id", "input_index")
    assert (
        dataset_contract.normalized_filename_template
        == "{dataset_type}_{batch_id}_normalized.csv"
    )
    assert (
        dataset_contract.failed_filename_template
        == "{dataset_type}_{batch_id}_failed.csv"
    )

    directories = contracts.DEFAULT_RUNTIME_DIRECTORIES
    assert directories.as_mapping() == {
        "inbox": Path("var/rpa/inbox"),
        "normalized": Path("var/rpa/normalized"),
        "archive": Path("var/rpa/archive"),
        "failed": Path("var/rpa/failed"),
    }

    class ExampleProcessor:
        def process(self, frame, contract):
            return contracts.ProcessorResult(
                normalized_rows=frame,
                failed_rows=[],
                summary={"input_rows": len(frame)},
            )

    processor = ExampleProcessor()
    assert isinstance(processor, contracts.ProcessorContract)
    result = processor.process([{"input_index": "1"}], dataset_contract)
    assert result.normalized_rows == [{"input_index": "1"}]
    assert result.failed_rows == []
    assert result.summary == {"input_rows": 1}


def test_rpa_data_contract_fixtures_are_reconcilable_and_desensitized() -> None:
    """Protect the JD sample handoff from row loss and live URL disclosure."""

    input_header, input_rows = _read_csv_rows(JD_PRODUCT_URLS_FIXTURE)
    export_header, export_rows = _read_csv_rows(JD_PRODUCT_EXPORT_FIXTURE)

    assert input_header == ("input_index", "product_url")
    assert export_header == COMMON_WEB_EXPORT_COLUMNS + JD_PRODUCT_SITE_COLUMNS
    assert len(input_rows) == len(export_rows) >= 3
    assert [row["input_index"] for row in input_rows] == [
        row["input_index"] for row in export_rows
    ]

    input_urls = {row["input_index"]: row["product_url"] for row in input_rows}
    for row in input_rows:
        assert int(row["input_index"]) > 0
        assert (urlsplit(row["product_url"]).hostname or "").endswith(".invalid")

    contracts = _load_data_ops_contracts()
    for row in export_rows:
        assert row["dataset_type"] == "jd_product"
        assert row["source_url"] == input_urls[row["input_index"]]
        assert row["crawl_status"] in {"success", "partial", "failed"}
        assert datetime.fromisoformat(row["captured_at"].replace("Z", "+00:00"))
        if row["crawl_status"] == "success":
            assert row["error_code"] == ""
        else:
            assert row["error_code"] in contracts.JD_PRODUCT_WEB_EXPORT_CONTRACT.error_codes
        if row["primary_image_url"]:
            assert (
                urlsplit(row["primary_image_url"]).hostname or ""
            ).endswith(".invalid")


def test_rpa_data_contract_readme_documents_security_and_data_boundaries() -> None:
    """Protect the RPA handoff from credential leakage and database scope creep."""

    text = RPA_README_PATH.read_text(encoding="utf-8")
    required_tokens = (
        "WebPageExportContract",
        "DatasetContract",
        "ProcessorContract",
        "RuntimeDirectoryContract",
        "dataset_type",
        "batch_id",
        "input_index",
        "source_url",
        "captured_at",
        "crawl_status",
        "error_code",
        "jd_product",
        "display_price",
        "capture_region",
        "var/rpa/inbox",
        "var/rpa/normalized",
        "var/rpa/archive",
        "var/rpa/failed",
        "不新增数据库表",
        "不修改 `items`",
        "验证码",
    )
    for token in required_tokens:
        assert token in text


def test_rpa_web_page_template_defines_generic_one_row_per_input_flow() -> None:
    """Protect the reusable Yingdao flow from site coupling and silent row loss."""

    text = RPA_WEB_PAGE_TEMPLATE_PATH.read_text(encoding="utf-8")
    required_tokens = (
        "LoadInputRows",
        "InvokeSiteAdapter",
        "AppendExportRow",
        "ExportRawCsv",
        "StopForManualVerification",
        "dataset_type",
        "source_url_column",
        "site_output_columns",
        "dataset_type,batch_id,input_index,source_url,captured_at,crawl_status,error_code",
        "success",
        "partial",
        "failed",
        "invalid_input",
        "adapter_error",
        "manual_verification_required",
        "Python 模式",
        "PART_SyntaxEditor",
        "adapter_process_result.adapter_result",
        "list(export_rows[0].keys())",
        "每个输入行",
        "恰好一条",
    )
    for token in required_tokens:
        assert token in text

    assert text.index("LoadInputRows") < text.index("InvokeSiteAdapter")
    assert text.index("InvokeSiteAdapter") < text.index("AppendExportRow")
    assert text.index("AppendExportRow") < text.index("ExportRawCsv")

    lowered = text.casefold()
    for site_specific_token in (
        "jd_product",
        "jd_sku_id",
        "display_price",
        "shop_name",
        "primary_image_url",
        "capture_region",
        "item.jd",
        "京东",
    ):
        assert site_specific_token not in lowered


def test_rpa_web_page_template_documents_safe_export_and_acceptance_matrix() -> None:
    """Protect J2 export naming, secret handling, and four acceptance outcomes."""

    template_text = RPA_WEB_PAGE_TEMPLATE_PATH.read_text(encoding="utf-8")
    readme_text = RPA_README_PATH.read_text(encoding="utf-8")

    template_tokens = (
        "UTF-8",
        "{dataset_type}_{batch_id}_{captured_at}.csv",
        "凭据",
        "Cookie",
        "Token",
        "验证码",
        "不得写入 CSV",
        "成功场景",
        "字段缺失场景",
        "适配失败场景",
        "人工验证场景",
        "保留当前页面",
    )
    for token in template_tokens:
        assert token in template_text

    readme_tokens = (
        "templates/web-page-to-csv.md",
        "TalonMart - Web Page to CSV",
        "LoadInputRows",
        "InvokeSiteAdapter",
        "AppendExportRow",
        "ExportRawCsv",
        "StopForManualVerification",
    )
    for token in readme_tokens:
        assert token in readme_text


def test_jd_product_adapter_documents_flow_contract_and_safe_failures() -> None:
    """Protect the first site adapter from fake values and access-control bypasses."""

    text = RPA_JD_PRODUCT_IMPLEMENTATION_PATH.read_text(encoding="utf-8")
    required_tokens = (
        "ParseJdSkuId",
        "GetOpenedJdPage",
        "ExtractJdProduct",
        "BuildJdProductRow",
        "ClassifyJdCaptureResult",
        "JdProductTitle",
        "JdDisplayPrice",
        "JdShopName",
        "JdPrimaryImage",
        "jd_sku_id",
        "title",
        "display_price",
        "shop_name",
        "primary_image_url",
        "capture_region",
        "success",
        "partial",
        "failed",
        "invalid_input",
        "navigation_failed",
        "page_timeout",
        "field_missing",
        "manual_verification_required",
        "access_restricted",
        "验证码",
        "不尝试绕过",
        "错误截图",
        "var/rpa/failed",
        "每个输入行",
        "恰好一条",
    )
    for token in required_tokens:
        assert token in text

    assert "JdCaptureRegion" not in text
    assert "JdUnavailableState" not in text
    assert text.index("ParseJdSkuId") < text.index("GetOpenedJdPage")
    assert text.index("GetOpenedJdPage") < text.index("ExtractJdProduct")
    assert text.index("ExtractJdProduct") < text.index("BuildJdProductRow")
    assert text.index("BuildJdProductRow") < text.index("ClassifyJdCaptureResult")


def test_jd_product_adapter_fixtures_cover_four_desensitized_outcomes() -> None:
    """Protect the J3 handoff matrix and one-output-row-per-input invariant."""

    input_header, input_rows = _read_csv_rows(JD_PRODUCT_URLS_FIXTURE)
    export_header, export_rows = _read_csv_rows(JD_PRODUCT_EXPORT_FIXTURE)

    assert input_header == ("input_index", "product_url")
    assert export_header == COMMON_WEB_EXPORT_COLUMNS + JD_PRODUCT_SITE_COLUMNS
    assert len(input_rows) == len(export_rows) == 4
    assert [row["input_index"] for row in input_rows] == ["1", "2", "3", "4"]
    assert [row["input_index"] for row in export_rows] == ["1", "2", "3", "4"]

    rows_by_index = {row["input_index"]: row for row in export_rows}
    assert (rows_by_index["1"]["crawl_status"], rows_by_index["1"]["error_code"]) == (
        "success",
        "",
    )
    assert (rows_by_index["2"]["crawl_status"], rows_by_index["2"]["error_code"]) == (
        "partial",
        "field_missing",
    )
    assert (rows_by_index["3"]["crawl_status"], rows_by_index["3"]["error_code"]) == (
        "failed",
        "navigation_failed",
    )
    assert (rows_by_index["4"]["crawl_status"], rows_by_index["4"]["error_code"]) == (
        "failed",
        "invalid_input",
    )

    for index in ("1", "2", "3"):
        match = re.fullmatch(
            r"https://item\.jd\.invalid/(?P<sku>\d+)\.html",
            input_rows[int(index) - 1]["product_url"],
        )
        assert match is not None
        assert rows_by_index[index]["jd_sku_id"] == match.group("sku")

    assert input_rows[3]["product_url"].endswith("/not-a-sku")
    for index in ("3", "4"):
        assert all(
            rows_by_index[index][column] == ""
            for column in (
                "title",
                "display_price",
                "shop_name",
                "primary_image_url",
                "capture_region",
            )
        )

    assert all(row["capture_region"] == "" for row in export_rows)
