from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from http import HTTPStatus
from typing import NoReturn

from httpx import Response

from .exceptions import AmazonAuthError, AmazonSPAPIError, AmazonThrottlingError
from .schemes.product_attributes import (
    BoolAttribute,
    CatalogItemAttributes,
    ListingMetadata,
    LocalizedStringAttribute,
    NormalizedCompliance,
    NormalizedDimensions,
    NormalizedIngredient,
    NormalizedProduct,
    ProductSiteLaunchDateAttribute,
    SimpleStringAttribute,
    StreetDateAttribute,
)

THROTTLING_CODES = {"QuotaExceeded", "Throttling", "RequestThrottled"}


# ========== Async Client Errors ==========

def get_error_code_and_message(response: Response) -> tuple[str | None, str]:
    try:
        data = response.json()
    except ValueError:
        data = None

    code: str | None = None
    message: str | None = None

    if isinstance(data, dict):
        errors = data.get("errors")
        if isinstance(errors, list) and errors:
            first = errors[0] or {}
            if isinstance(first, dict):
                code = first.get("code") or code
                message = first.get("message") or message
    if not message:
        message = response.text[:500]

    return code, message


def process_errors(response: Response) -> NoReturn:
    text_body = response.text
    code, message = get_error_code_and_message(response)

    status = response.status_code
    if status in {HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN}:
        raise AmazonAuthError(
            message=f"Amazon auth error ({status}): {message}",
            status_code=status,
            body=text_body,
        )

    retry_after_header = response.headers.get("Retry-After")
    retry_after: float | None = None
    if retry_after_header:
        try:
            retry_after = float(retry_after_header)
        except ValueError:
            retry_after = None

    if status == HTTPStatus.TOO_MANY_REQUESTS or (code and code in THROTTLING_CODES):
        raise AmazonThrottlingError(
            message=f"Amazon throttling error ({status}): {message}",
            status_code=status,
            code=code,
            retry_after=retry_after,
            body=text_body,
        )
    raise AmazonSPAPIError(
        message=f"Amazon SP-API error ({status}): {message}",
        status_code=status,
        code=code,
        body=text_body,
    )


# ========== Catalog Items Attributes ==========

def _pick_locale_str(
    items: list[LocalizedStringAttribute] | None,
    default_lang: str | None = "en_GB",
) -> str | None:
    """
    Берём строку с нужным language_tag (если указано),
    иначе просто первую. Если список пустой / None — возвращаем None.
    """
    if not items:
        return None
    if default_lang:
        for it in items:
            if it.language_tag == default_lang and it.value is not None:
                return it.value
    # fallback – первое непустое значение
    for it in items:
        if it.value is not None:
            return it.value
    return None


def _first_bool(arr: list[BoolAttribute] | None) -> bool | None:
    return arr[0].value if arr else None


def _first_str(arr: list[SimpleStringAttribute] | None) -> str | None:
    return arr[0].value if arr else None


def _parse_date_attr(
    items: list[StreetDateAttribute] | list[ProductSiteLaunchDateAttribute] | None,
) -> datetime | None:
    """
    В первом слое value уже datetime, поэтому просто берём первый элемент.
    """
    if not items:
        return None
    return items[0].value


def build_normalized_product(
    catalog_item: CatalogItemAttributes,
    marketplace_id: str,
    listings: list[ListingMetadata] | None = None,
) -> NormalizedProduct:
    """`
    Превращаем сырой CatalogItemAttributesResponse (слой 1)
    + необязательный список листингов в удобный для LLM NormalizedProduct.
    """
    attrs = catalog_item.attributes
    listings = listings or []

    # --- identity / sku mapping ---
    asin = catalog_item.asin
    all_skus_set = {l.seller_sku for l in listings if l.seller_sku}
    all_skus = sorted(all_skus_set)
    primary_sku = all_skus[0] if all_skus else None

    # --- main merchandising ---
    title = _pick_locale_str(attrs.item_name)
    brand = _pick_locale_str(attrs.brand)
    manufacturer = _pick_locale_str(attrs.manufacturer)
    bullets = [b.value for b in (attrs.bullet_point or []) if b.value]
    description = _pick_locale_str(attrs.product_description)

    # --- classification ---
    unspsc_code = _first_str(attrs.unspsc_code)
    browse_node_ids = [
        n.value for n in (attrs.recommended_browse_nodes or []) if n.value
    ]

    # --- composition / health ---
    primary_supplement_type = _pick_locale_str(attrs.primary_supplement_type)
    ingredients_text = _pick_locale_str(attrs.ingredients)
    product_benefits = [
        b.value for b in (attrs.product_benefit or []) if b.value
    ]
    age_range = _pick_locale_str(attrs.age_range_description)

    ingredients: list[NormalizedIngredient] = []
    for ing in attrs.active_ingredient_strength or []:
        # ActiveIngredientStrengthItem из слоя 1
        ingredient_name = ing.ingredient_name.value
        strength_value = ing.ingredient_strength.value
        strength_unit = ing.ingredient_strength.unit
        ingredients.append(
            NormalizedIngredient(
                name=ingredient_name,
                strength_value=strength_value,
                strength_unit=strength_unit,
            ),
        )

    # --- form / dosage ---
    item_form = _pick_locale_str(attrs.item_form)
    dosage_form = _pick_locale_str(attrs.dosage_form)
    serving_recommendation = _pick_locale_str(attrs.serving_recommendation)
    concentration = _pick_locale_str(attrs.concentration)

    # --- flavour / size / count ---
    flavor = _pick_locale_str(attrs.flavor)
    scent = _pick_locale_str(attrs.scent)
    size = _pick_locale_str(attrs.size)

    number_of_items = (
        int(attrs.number_of_items[0].value)
        if attrs.number_of_items and attrs.number_of_items[0].value is not None
        else None
    )

    unit_count = None
    unit_count_type = None
    if attrs.unit_count:
        uc = attrs.unit_count[0]
        unit_count = uc.value
        if uc.type is not None:
            unit_count_type = uc.type.value

    # --- packaging / price ---
    dims = NormalizedDimensions()
    if attrs.item_package_dimensions:
        d = attrs.item_package_dimensions[0]
        if d.length.unit == "centimeters":
            dims.length_cm = d.length.value
        if d.width.unit == "centimeters":
            dims.width_cm = d.width.value
        if d.height.unit == "centimeters":
            dims.height_cm = d.height.value

    if attrs.item_package_weight:
        w = attrs.item_package_weight[0]
        if w.unit == "kilograms":
            dims.weight_kg = w.value

    list_price = None
    list_price_currency = None
    if attrs.list_price:
        lp = attrs.list_price[0]
        list_price = Decimal(str(lp.value_with_tax))
        list_price_currency = lp.currency

    # --- dates ---
    street_date = _parse_date_attr(attrs.street_date)
    product_site_launch_date = _parse_date_attr(attrs.product_site_launch_date)

    # --- compliance ---
    compliance = NormalizedCompliance(
        is_expiration_dated_product=_first_bool(attrs.is_expiration_dated_product),
        is_heat_sensitive=_first_bool(attrs.is_heat_sensitive),
        contains_food_or_beverage=_first_bool(attrs.contains_food_or_beverage),
        contains_liquid_contents=_first_bool(attrs.contains_liquid_contents),
        hfss_status=_first_str(attrs.hfss_status),
        supplier_declared_dg_hz_regulation=_first_str(
            attrs.supplier_declared_dg_hz_regulation,
        ),
        legal_certifications=[
                                 c.value
                                 for c in (attrs.legal_compliance_certifications or [])
                                 if c.value
                             ] or None,
    )

    return NormalizedProduct(
        asin=asin,
        marketplace_id=marketplace_id,
        primary_sku=primary_sku,
        all_skus=all_skus,
        title=title,
        brand=brand,
        manufacturer=manufacturer,
        bullets=bullets,
        description=description,
        unspsc_code=unspsc_code,
        browse_node_ids=browse_node_ids,
        primary_supplement_type=primary_supplement_type,
        ingredients_text=ingredients_text,
        ingredients=ingredients,
        product_benefits=product_benefits,
        age_range=age_range,
        item_form=item_form,
        dosage_form=dosage_form,
        serving_recommendation=serving_recommendation,
        concentration=concentration,
        flavor=flavor,
        scent=scent,
        size=size,
        number_of_items=number_of_items,
        unit_count=unit_count,
        unit_count_type=unit_count_type,
        dimensions=dims,
        list_price=list_price,
        list_price_currency=list_price_currency,
        street_date=street_date,
        product_site_launch_date=product_site_launch_date,
        compliance=compliance,
        raw_attributes=attrs,
        raw_listings=listings,
    )
