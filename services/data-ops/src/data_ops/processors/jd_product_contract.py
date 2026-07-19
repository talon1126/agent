"""Define JD-specific file columns and processor output contracts.

The module extends generic web-export and dataset contract types with the first
concrete site schema. Keeping these names beside the JD processor prevents
website fields from leaking into the reusable data-ops core.
"""

from __future__ import annotations

from data_ops.core.contracts import (
    COMMON_WEB_EXPORT_COLUMN_TYPES,
    DatasetContract,
    WebPageExportContract,
)

JD_PRODUCT_INPUT_COLUMNS = ("input_index", "product_url")
JD_PRODUCT_SITE_COLUMNS = (
    "jd_sku_id",
    "title",
    "display_price",
    "shop_name",
    "primary_image_url",
    "capture_region",
)
JD_PRODUCT_NORMALIZED_COLUMNS = ("display_price_amount",)

JD_PRODUCT_WEB_EXPORT_CONTRACT = WebPageExportContract(
    dataset_type="jd_product",
    input_columns=JD_PRODUCT_INPUT_COLUMNS,
    site_output_columns=JD_PRODUCT_SITE_COLUMNS,
)

JD_PRODUCT_DATASET_CONTRACT = DatasetContract(
    dataset_type="jd_product",
    required_columns=JD_PRODUCT_WEB_EXPORT_CONTRACT.output_columns,
    optional_columns=JD_PRODUCT_NORMALIZED_COLUMNS,
    column_types={
        **COMMON_WEB_EXPORT_COLUMN_TYPES,
        "jd_sku_id": "string",
        "title": "string",
        "display_price": "string",
        "shop_name": "string",
        "primary_image_url": "url",
        "capture_region": "string",
        "display_price_amount": "decimal",
    },
    unique_by=("batch_id", "input_index"),
    normalized_filename_template="{dataset_type}_{batch_id}_normalized.csv",
    failed_filename_template="{dataset_type}_{batch_id}_failed.csv",
)

__all__ = [
    "JD_PRODUCT_DATASET_CONTRACT",
    "JD_PRODUCT_INPUT_COLUMNS",
    "JD_PRODUCT_NORMALIZED_COLUMNS",
    "JD_PRODUCT_SITE_COLUMNS",
    "JD_PRODUCT_WEB_EXPORT_CONTRACT",
]
