from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

# =========================
# БАЗОВЫЕ "КИРПИЧИКИ"
# =========================


class MarketplaceBase(BaseModel):
    marketplace_id: str


class LocalizedStringAttribute(MarketplaceBase):
    # В некоторых полях (как dosage_form в твоём примере) value может отсутствовать,
    # поэтому разрешаем None.
    language_tag: str | None = None
    value: str | None = None


class SimpleStringAttribute(MarketplaceBase):
    value: str


class BoolAttribute(MarketplaceBase):
    value: bool


class IntAttribute(MarketplaceBase):
    value: int


class FloatAttribute(MarketplaceBase):
    value: float


class UnitValue(BaseModel):
    unit: str
    value: float


class Dimension(UnitValue):
    """Например: {'unit': 'centimeters', 'value': 5.6}"""


class PackageDimensions(MarketplaceBase):
    length: Dimension
    width: Dimension
    height: Dimension


class Weight(MarketplaceBase):
    unit: str
    value: float


class MoneyAmount(MarketplaceBase):
    value_with_tax: float
    currency: str


# =========================
# СПЕЦИФИЧЕСКИЕ АТРИБУТЫ
# =========================

class IngredientName(BaseModel):
    language_tag: str | None = None
    value: str


class IngredientStrength(BaseModel):
    unit: str
    value: float


class ActiveIngredientStrengthItem(MarketplaceBase):
    ingredient_strength: IngredientStrength
    sequential_id: int
    ingredient_name: IngredientName


class VariationTheme(MarketplaceBase):
    name: str


class ExternallyAssignedProductIdentifier(MarketplaceBase):
    value: str
    type: str  # 'ean' и т.п.


class LegalComplianceCertification(MarketplaceBase):
    language_tag: str | None = None
    certification_status: str | None = None
    regulatory_organization_name: str | None = None
    value: str | None = None


class UnitCountType(BaseModel):
    language_tag: str | None = None
    value: str


class UnitCount(MarketplaceBase):
    type: UnitCountType | None = None
    value: float


class StreetDateAttribute(MarketplaceBase):
    value: datetime


class ProductSiteLaunchDateAttribute(MarketplaceBase):
    value: datetime


# =========================
# ОСНОВНАЯ МОДЕЛЬ АТРИБУТОВ
# =========================

class ItemAttributes(BaseModel):
    # === Флаги / статусы ===
    skip_offer: list[BoolAttribute] | None = None
    is_expiration_dated_product: list[BoolAttribute] | None = None
    contains_liquid_contents: list[BoolAttribute] | None = None
    contains_food_or_beverage: list[BoolAttribute] | None = None
    is_heat_sensitive: list[BoolAttribute] | None = None
    batteries_required: list[BoolAttribute] | None = None

    # === Активные ингредиенты ===
    active_ingredient_strength: list[ActiveIngredientStrengthItem] | None = None

    # === Текстовые описания / маркетинг ===
    age_range_description: list[LocalizedStringAttribute] | None = None
    bullet_point: list[LocalizedStringAttribute] | None = None
    scent: list[LocalizedStringAttribute] | None = None
    product_description: list[LocalizedStringAttribute] | None = None
    brand: list[LocalizedStringAttribute] | None = None
    temperature_rating: list[LocalizedStringAttribute] | None = None
    item_form: list[LocalizedStringAttribute] | None = None
    flavor: list[LocalizedStringAttribute] | None = None
    directions: list[LocalizedStringAttribute] | None = None
    size: list[LocalizedStringAttribute] | None = None
    primary_supplement_type: list[LocalizedStringAttribute] | None = None
    manufacturer: list[LocalizedStringAttribute] | None = None
    ingredients: list[LocalizedStringAttribute] | None = None
    product_benefit: list[LocalizedStringAttribute] | None = None
    item_name: list[LocalizedStringAttribute] | None = None
    concentration: list[LocalizedStringAttribute] | None = None
    serving_recommendation: list[LocalizedStringAttribute] | None = None
    dosage_form: list[LocalizedStringAttribute] | None = None

    # === Простые строковые коды / статусы ===
    unspsc_code: list[SimpleStringAttribute] | None = None
    rtip_manufacturer_contact_information: list[SimpleStringAttribute] | None = None
    part_number: list[SimpleStringAttribute] | None = None
    hfss_status: list[SimpleStringAttribute] | None = None
    supplier_declared_dg_hz_regulation: list[SimpleStringAttribute] | None = None
    model_number: list[SimpleStringAttribute] | None = None
    recommended_browse_nodes: list[SimpleStringAttribute] | None = None
    supplement_formulation: list[SimpleStringAttribute] | None = None

    # === Вариации / идентификаторы ===
    variation_theme: list[VariationTheme] | None = None
    externally_assigned_product_identifier: list[ExternallyAssignedProductIdentifier] | None = None

    # === Кол-во, размер, вес ===
    number_of_items: list[IntAttribute] | None = None
    item_package_dimensions: list[PackageDimensions] | None = None
    item_package_weight: list[Weight] | None = None
    unit_count: list[UnitCount] | None = None

    # === Цена ===
    list_price: list[MoneyAmount] | None = None

    # === Даты (как datetime) ===
    street_date: list[StreetDateAttribute] | None = None
    product_site_launch_date: list[ProductSiteLaunchDateAttribute] | None = None

    # === Юридическая инфа ===
    legal_compliance_certifications: list[LegalComplianceCertification] | None = None

    model_config = ConfigDict(
        extra="ignore",
    )


# =========================
# ВЕРХНИЙ УРОВЕНЬ ОТ /catalog/.../items/{asin}
# =========================

class CatalogItemAttributes(BaseModel):
    asin: str
    attributes: ItemAttributes

    model_config = ConfigDict(
        extra="ignore",
    )


# =========================
# ВСПОМОГАТЕЛЬНЫЕ МОДЕЛИ
# =========================

class NormalizedIngredient(BaseModel):
    name: str
    strength_value: float | None = None
    strength_unit: str | None = None


class NormalizedDimensions(BaseModel):
    length_cm: float | None = None
    width_cm: float | None = None
    height_cm: float | None = None
    weight_kg: float | None = None


class NormalizedCompliance(BaseModel):
    is_expiration_dated_product: bool | None = None
    is_heat_sensitive: bool | None = None
    contains_food_or_beverage: bool | None = None
    contains_liquid_contents: bool | None = None
    hfss_status: str | None = None
    supplier_declared_dg_hz_regulation: str | None = None
    legal_certifications: list[str] | None = None


class ListingMetadata(BaseModel):
    """Информация о конкретном листинге (SKU) – удобно связать с TSV/БД."""

    asin: str
    marketplace_id: str
    seller_sku: str | None = None
    listing_id: str | None = None
    price: Decimal | None = None
    currency: str | None = None
    quantity: int | None = None
    fulfillment_channel: str | None = None  # AMAZON_EU / MFN и т.п.
    status: str | None = None  # Active / Inactive / ...
    image_url: str | None = None


class NormalizedProduct(BaseModel):
    # === Identity ===
    asin: str
    marketplace_id: str
    primary_sku: str | None = None
    all_skus: list[str] = []

    # === Main merchandising ===
    title: str | None = None
    brand: str | None = None
    manufacturer: str | None = None
    bullets: list[str] = []
    description: str | None = None

    # === Classification ===
    unspsc_code: str | None = None
    browse_node_ids: list[str] = []

    # === Composition / health ===
    primary_supplement_type: str | None = None
    ingredients_text: str | None = None
    ingredients: list[NormalizedIngredient] = []
    product_benefits: list[str] = []
    age_range: str | None = None

    # === Form / dosage ===
    item_form: str | None = None
    dosage_form: str | None = None
    serving_recommendation: str | None = None
    concentration: str | None = None

    # === Flavour / size / count ===
    flavor: str | None = None
    scent: str | None = None
    size: str | None = None
    number_of_items: int | None = None
    unit_count: float | None = None
    unit_count_type: str | None = None

    # === Packaging / price ===
    dimensions: NormalizedDimensions = NormalizedDimensions()
    list_price: Decimal | None = None
    list_price_currency: str | None = None

    # === Dates ===
    street_date: datetime | None = None
    product_site_launch_date: datetime | None = None

    # === Compliance ===
    compliance: NormalizedCompliance = NormalizedCompliance()

    # === Raw sources (для дебага / логов, не обязательно отдавать модели) ===
    raw_attributes: ItemAttributes
    raw_listings: list[ListingMetadata] = []

    def dump_json_for_model(self) -> str:
        return self.model_dump_json(
            exclude={
                "compliance",
                "raw_attributes",
                "raw_listings",
            },
        )
