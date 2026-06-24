from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CatalogDetailField:
    label: str
    value: str


@dataclass(frozen=True)
class CatalogDetailSection:
    title: str
    description: str
    fields: list[CatalogDetailField]
    bullets: list[str] | None = None


def _format_scalar(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value).strip()
    return text or None


def _format_list(values: list[Any] | None, *, separator: str = ", ") -> str | None:
    if not values:
        return None
    items = [_format_scalar(item) for item in values]
    items = [item for item in items if item]
    if not items:
        return None
    return separator.join(items)


def _format_ingredients(values: list[dict[str, Any]] | None) -> str | None:
    if not values:
        return None
    lines: list[str] = []
    for item in values:
        name = _format_scalar(item.get("name"))
        strength_value = item.get("strength_value")
        strength_unit = _format_scalar(item.get("strength_unit"))
        if name and strength_value is not None and strength_unit:
            lines.append(f"{name}: {strength_value} {strength_unit}")
        elif name and strength_value is not None:
            lines.append(f"{name}: {strength_value}")
        elif name:
            lines.append(name)
    return "\n".join(lines) if lines else None


def _format_dimensions(value: dict[str, Any] | None) -> str | None:
    if not value:
        return None
    parts: list[str] = []
    length_cm = value.get("length_cm")
    width_cm = value.get("width_cm")
    height_cm = value.get("height_cm")
    weight_g = value.get("weight_g")
    if length_cm is not None and width_cm is not None and height_cm is not None:
        parts.append(f"{length_cm} x {width_cm} x {height_cm} cm")
    if weight_g is not None:
        parts.append(f"{weight_g} g")
    return " · ".join(parts) if parts else None


def _append_field(fields: list[CatalogDetailField], label: str, value: Any) -> None:
    text = _format_scalar(value)
    if text:
        fields.append(CatalogDetailField(label=label, value=text))


def _append_multiline_field(fields: list[CatalogDetailField], label: str, value: Any) -> None:
    text = _format_scalar(value)
    if text:
        fields.append(CatalogDetailField(label=label, value=text))


def _maybe_section(
    *,
    title: str,
    description: str,
    builder: Callable[[], tuple[list[CatalogDetailField], list[str] | None]],
) -> CatalogDetailSection | None:
    fields, bullets = builder()
    if not fields and not bullets:
        return None
    return CatalogDetailSection(
        title=title,
        description=description,
        fields=fields,
        bullets=bullets,
    )


def _build_overview_section(product: dict[str, Any]) -> CatalogDetailSection | None:
    def builder() -> tuple[list[CatalogDetailField], list[str] | None]:
        fields: list[CatalogDetailField] = []
        _append_field(fields, "Catalog title", product.get("title"))
        _append_field(fields, "Brand", product.get("brand"))
        _append_field(fields, "Manufacturer", product.get("manufacturer"))
        _append_multiline_field(fields, "Catalog description", product.get("description"))
        bullets = [
            item.strip()
            for item in (product.get("bullets") or [])
            if isinstance(item, str) and item.strip()
        ]
        return fields, bullets or None

    return _maybe_section(
        title="Amazon Catalog Overview",
        description="Merchandising fields from Amazon SP-API catalog attributes.",
        builder=builder,
    )


def _build_composition_section(product: dict[str, Any]) -> CatalogDetailSection | None:
    def builder() -> tuple[list[CatalogDetailField], list[str] | None]:
        fields: list[CatalogDetailField] = []
        _append_field(fields, "Supplement type", product.get("primary_supplement_type"))
        _append_multiline_field(fields, "Ingredients text", product.get("ingredients_text"))
        ingredients = _format_ingredients(product.get("ingredients"))
        if ingredients:
            fields.append(CatalogDetailField(label="Active ingredients", value=ingredients))
        benefits = _format_list(product.get("product_benefits"), separator="\n")
        if benefits:
            fields.append(CatalogDetailField(label="Product benefits", value=benefits))
        _append_field(fields, "Age range", product.get("age_range"))
        return fields, None

    return _maybe_section(
        title="Composition & Health",
        description="Ingredients, supplement type, and health-related catalog attributes.",
        builder=builder,
    )


def _build_dosage_section(product: dict[str, Any]) -> CatalogDetailSection | None:
    def builder() -> tuple[list[CatalogDetailField], list[str] | None]:
        fields: list[CatalogDetailField] = []
        _append_field(fields, "Item form", product.get("item_form"))
        _append_field(fields, "Dosage form", product.get("dosage_form"))
        _append_multiline_field(fields, "Serving recommendation", product.get("serving_recommendation"))
        _append_field(fields, "Concentration", product.get("concentration"))
        return fields, None

    return _maybe_section(
        title="Form & Dosage",
        description="How the product is supplied and recommended for use.",
        builder=builder,
    )


def _build_variant_section(product: dict[str, Any]) -> CatalogDetailSection | None:
    def builder() -> tuple[list[CatalogDetailField], list[str] | None]:
        fields: list[CatalogDetailField] = []
        _append_field(fields, "Flavor", product.get("flavor"))
        _append_field(fields, "Scent", product.get("scent"))
        _append_field(fields, "Size", product.get("size"))
        _append_field(fields, "Number of items", product.get("number_of_items"))
        unit_count = product.get("unit_count")
        unit_count_type = _format_scalar(product.get("unit_count_type"))
        if unit_count is not None:
            label = "Unit count"
            value = f"{unit_count} {unit_count_type}".strip() if unit_count_type else str(unit_count)
            fields.append(CatalogDetailField(label=label, value=value))
        dimensions = _format_dimensions(product.get("dimensions"))
        if dimensions:
            fields.append(CatalogDetailField(label="Package dimensions", value=dimensions))
        list_price = product.get("list_price")
        if list_price is not None:
            currency = _format_scalar(product.get("list_price_currency")) or ""
            fields.append(
                CatalogDetailField(
                    label="List price",
                    value=f"{list_price} {currency}".strip(),
                ),
            )
        return fields, None

    return _maybe_section(
        title="Variant & Packaging",
        description="Size, count, flavor, and package details from catalog attributes.",
        builder=builder,
    )


def _build_classification_section(product: dict[str, Any]) -> CatalogDetailSection | None:
    def builder() -> tuple[list[CatalogDetailField], list[str] | None]:
        fields: list[CatalogDetailField] = []
        _append_field(fields, "UNSPSC code", product.get("unspsc_code"))
        browse_nodes = _format_list(product.get("browse_node_ids"))
        if browse_nodes:
            fields.append(CatalogDetailField(label="Browse nodes", value=browse_nodes))
        return fields, None

    return _maybe_section(
        title="Classification",
        description="Amazon browse and classification metadata.",
        builder=builder,
    )


def build_catalog_product_sections(product: dict[str, Any] | None) -> list[CatalogDetailSection]:
    if not product:
        return []

    builders = (
        _build_overview_section,
        _build_composition_section,
        _build_dosage_section,
        _build_variant_section,
        _build_classification_section,
    )
    return [section for builder in builders if (section := builder(product)) is not None]
