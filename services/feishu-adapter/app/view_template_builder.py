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


def get_template(template_id: str) -> WarehouseViewTemplate:
    for template in load_warehouse_view_templates():
        if template.template_id == template_id:
            return template
    raise ValueError(f"unknown template_id: {template_id}")


def render_filters(slots: dict[str, Any]) -> list[dict[str, Any]]:
    filters: list[dict[str, Any]] = []
    if slots.get("risk_level"):
        filters.append(
            {"field": "Risk Level", "operator": "is", "value": slots["risk_level"]}
        )
    if slots.get("warehouse"):
        filters.append(
            {"field": "Warehouse ID", "operator": "is", "value": slots["warehouse"]}
        )
    if slots.get("category"):
        filters.append(
            {"field": "Category ID", "operator": "is", "value": slots["category"]}
        )
    if slots.get("location_code"):
        filters.append(
            {"field": "Location", "operator": "is", "value": slots["location_code"]}
        )
    if slots.get("expiry_risk"):
        filters.append(
            {"field": "Expiry Risk", "operator": "is", "value": slots["expiry_risk"]}
        )
    if slots.get("available_lt") is not None:
        try:
            available_lt = int(slots["available_lt"])
        except (TypeError, ValueError) as exc:
            raise ValueError("available_lt must be an integer") from exc
        filters.append(
            {
                "field": "Quantity Available",
                "operator": "lt",
                "value": available_lt,
            }
        )
    return filters


def render_warehouse_view_plan(
    template_id: str,
    view_name: str | None,
    slots: dict[str, Any],
) -> dict[str, Any]:
    template = get_template(template_id)
    merged_slots = {**template.defaults, **slots}
    return {
        "table_name": template.table_name,
        "view_name": view_name or template.display_name,
        "view_type": "grid",
        "visible_fields": list(template.visible_fields),
        "filters": render_filters(merged_slots),
        "sorts": list(template.sorts),
        "template_id": template.template_id,
        "slots": merged_slots,
    }


def match_warehouse_view_template(message: str) -> TemplateMatchResult:
    template = _find_template(message, load_warehouse_view_templates())
    if template is None:
        return TemplateMatchResult(
            matched=False,
            error="unknown_view_template",
            suggestions=_template_suggestions(),
        )

    slots = dict(template.defaults)
    for extractor in (
        _extract_warehouse,
        _extract_category,
        _extract_location_code,
        _extract_risk_level,
        _extract_expiry_risk,
        _extract_available_lt,
    ):
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
        if any(_matches_risk_alias(normalized_message, alias) for alias in aliases):
            return "risk_level", risk_level
    return "risk_level", None


def _extract_category(message: str) -> tuple[str, str | None]:
    normalized_message = message.casefold()
    category_aliases = {
        "paper": ("纸品", "纸巾", "抽纸", "paper"),
        "dairy": ("乳制品", "奶制品", "牛奶", "酸奶", "dairy", "milk", "yogurt"),
        "beverage": ("饮料", "矿泉水", "可乐", "beverage", "drink"),
        "daily_chemical": ("日化", "洗衣液", "daily chemical", "detergent"),
        "office_supply": ("办公耗材", "办公用品", "文具", "office supply"),
    }
    for category, aliases in category_aliases.items():
        if any(alias.casefold() in normalized_message for alias in aliases):
            return "category", category
    return "category", None


def _extract_location_code(message: str) -> tuple[str, str | None]:
    match = re.search(r"(?<![A-Z0-9])([A-Z]\d{1,2})(?![A-Z0-9])", message, flags=re.IGNORECASE)
    if match:
        return "location_code", match.group(1).upper()
    return "location_code", None


def _extract_expiry_risk(message: str) -> tuple[str, str | None]:
    normalized_message = message.casefold()
    if any(token in normalized_message for token in ("已过期", "过期", "expired")):
        return "expiry_risk", "expired"
    if any(token in normalized_message for token in ("临期", "快过期", "保质期", "expiring", "expiry")):
        return "expiry_risk", "expiring_soon"
    return "expiry_risk", None


def _matches_risk_alias(normalized_message: str, alias: str) -> bool:
    normalized_alias = alias.casefold()
    if normalized_alias.isascii():
        return re.search(rf"\b{re.escape(normalized_alias)}\b", normalized_message) is not None
    return normalized_alias in normalized_message


def _extract_available_lt(message: str) -> tuple[str, int | None]:
    patterns = (
        r"(?:available|stock|库存|可用库存)\s*(?:<|低于|少于|小于|below|under)\s*(\d+)",
        r"(?:below|under)\s*(\d+)\s*(?:件|个|pcs|units?)?",
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
    category = _category_view_name(slots.get("category"))
    location = str(slots.get("location_code") or "")
    expiry = _expiry_risk_view_name(slots.get("expiry_risk"))

    template_names = {
        "category_inventory_view": f"{prefix}{category}库存",
        "low_stock_view": f"{prefix}缺货预警",
        "expiring_inventory_view": f"{prefix}{category}{expiry}库存",
        "location_inventory_view": f"{prefix}{location}库位库存",
        "batch_risk_view": f"{prefix}{risk_level}批次",
        "replenishment_candidate_view": f"{prefix}补货候选",
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


def _category_view_name(category: Any) -> str:
    category_names = {
        "paper": "纸品",
        "dairy": "乳制品",
        "beverage": "饮料",
        "daily_chemical": "日化",
        "office_supply": "办公耗材",
    }
    return category_names.get(category, "")


def _expiry_risk_view_name(expiry_risk: Any) -> str:
    expiry_names = {
        "expiring_soon": "临期",
        "expired": "过期",
        "normal": "正常保质期",
    }
    return expiry_names.get(expiry_risk, "")


def _template_suggestions() -> list[str]:
    suggestions: list[str] = []
    for template in load_warehouse_view_templates():
        suggestions.extend(template.aliases[:1])
    return suggestions
