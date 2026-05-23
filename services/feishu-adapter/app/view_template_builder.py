import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


TEMPLATE_CATALOG_PATH = Path(__file__).parent / "view_templates" / "warehouse_inventory.json"


@dataclass(frozen=True)
class WarehouseViewTemplate:
    template_id: str
    display_name: str
    aliases: list[str]
    table_name: str
    visible_fields: list[str]
    slots: dict[str, Any]
    defaults: dict[str, Any]
    sorts: list[dict[str, str]]


@dataclass(frozen=True)
class TemplateMatchResult:
    matched: bool
    template_id: str | None = None
    view_name: str | None = None
    slots: dict[str, Any] | None = None
    template: WarehouseViewTemplate | None = None
    error: str | None = None
    suggestions: list[str] | None = None


def load_warehouse_view_templates() -> list[WarehouseViewTemplate]:
    raw_templates = json.loads(TEMPLATE_CATALOG_PATH.read_text(encoding="utf-8"))
    return [WarehouseViewTemplate(**raw_template) for raw_template in raw_templates]


def match_warehouse_view_template(message: str) -> TemplateMatchResult:
    template = _find_template(message, load_warehouse_view_templates())
    if template is None:
        return TemplateMatchResult(
            matched=False,
            error="unknown_view_template",
            suggestions=_template_suggestions(),
        )

    slots = dict(template.defaults)
    for extractor in (_extract_warehouse, _extract_risk_level, _extract_available_lt):
        key, value = extractor(message)
        if value is not None and key in template.slots:
            slots[key] = value

    return TemplateMatchResult(
        matched=True,
        template_id=template.template_id,
        view_name=_build_view_name(template, slots),
        slots=slots,
        template=template,
    )


def _find_template(
    message: str, templates: list[WarehouseViewTemplate]
) -> WarehouseViewTemplate | None:
    normalized_message = message.casefold()
    for template in templates:
        if any(alias.casefold() in normalized_message for alias in template.aliases):
            return template
    return None


def _extract_warehouse(message: str) -> tuple[str, str | None]:
    normalized_message = message.casefold()
    warehouse_aliases = {
        "wh_hk_1": ("香港仓", "香港仓库", "香港", "hong kong warehouse", "hong kong", "hk warehouse"),
        "wh_sz_1": ("深圳仓", "深圳仓库", "深圳", "shenzhen warehouse", "shenzhen", "sz warehouse"),
        "wh_sg_1": ("新加坡仓", "新加坡仓库", "新加坡", "singapore warehouse", "singapore", "sg warehouse"),
    }
    for warehouse_id, aliases in warehouse_aliases.items():
        if any(alias.casefold() in normalized_message for alias in aliases):
            return "warehouse", warehouse_id
    return "warehouse", None


def _extract_risk_level(message: str) -> tuple[str, str | None]:
    normalized_message = message.casefold()
    risk_aliases = {
        "high": ("高风险", "高危", "high risk", "high"),
        "medium": ("中风险", "中等风险", "medium risk", "medium"),
        "low": ("低风险", "low risk", "low"),
    }
    for risk_level, aliases in risk_aliases.items():
        if any(alias.casefold() in normalized_message for alias in aliases):
            return "risk_level", risk_level
    return "risk_level", None


def _extract_available_lt(message: str) -> tuple[str, int | None]:
    patterns = (
        r"(?:available|stock|库存|可用库存)\s*(?:<|低于|少于|小于|below|under)\s*(\d+)",
        r"(\d+)\s*(?:件|个|pcs|units?)\s*(?:以下|以内|below|under)",
    )
    for pattern in patterns:
        match = re.search(pattern, message, flags=re.IGNORECASE)
        if match:
            return "available_lt", int(match.group(1))
    return "available_lt", None


def _build_view_name(template: WarehouseViewTemplate, slots: dict[str, Any]) -> str:
    prefix = _warehouse_view_name(slots.get("warehouse"))
    risk_level = _risk_level_view_name(slots.get("risk_level"))

    template_names = {
        "inventory_risk_view": f"{prefix}{risk_level}库存",
        "low_stock_view": f"{prefix}缺货预警",
        "warehouse_exception_view": f"{prefix}仓储异常",
        "replenishment_candidate_view": f"{prefix}补货候选",
        "fulfillment_block_view": f"{prefix}{risk_level}履约阻塞",
    }
    return template_names.get(template.template_id, template.display_name.removesuffix("视图"))


def _warehouse_view_name(warehouse: Any) -> str:
    warehouse_names = {
        "wh_hk_1": "香港仓",
        "wh_sz_1": "深圳仓",
        "wh_sg_1": "新加坡仓",
    }
    return warehouse_names.get(warehouse, "")


def _risk_level_view_name(risk_level: Any) -> str:
    risk_level_names = {
        "high": "高风险",
        "medium": "中风险",
        "low": "低风险",
    }
    return risk_level_names.get(risk_level, "")


def _template_suggestions() -> list[str]:
    suggestions: list[str] = []
    for template in load_warehouse_view_templates():
        suggestions.extend(template.aliases[:1])
    return suggestions
