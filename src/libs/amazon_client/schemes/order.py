from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_pascal

from src.libs.amazon_client.enums import FulfillmentChannel

OptionalStr: type = Annotated[str | None, Field(default=None)]
"""Type alias for optional string fields with default None value."""
OptionalInt: type = Annotated[int | None, Field(default=None)]
"""Type alias for optional integer fields with default None value."""
OptionalBool: type = Annotated[bool | None, Field(default=None)]
"""Type alias for optional boolean fields with default None value."""
OptionalDatetime: type = Annotated[datetime | None, Field(default=None)]
"""Type alias for optional datetime fields with default None value."""


class BaseAmazonModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_pascal,
        validate_by_alias=True,
        validate_by_name=True,
        json_encoders={Decimal: str},
    )


class BuyerInfo(BaseAmazonModel):
    buyer_email: OptionalStr


class Money(BaseAmazonModel):
    amount: Decimal
    currency_code: str


class ShippingAddress(BaseAmazonModel):
    city: OptionalStr
    postal_code: OptionalStr
    country_code: OptionalStr


class ProductInfo(BaseAmazonModel):
    number_of_items: OptionalInt


class OrderSummary(BaseAmazonModel):
    amazon_order_id: str
    seller_order_id: OptionalStr
    buyer_info: BuyerInfo | None = None
    purchase_date: datetime
    last_update_date: datetime
    order_status: str
    fulfillment_channel: FulfillmentChannel | None = None
    is_prime: OptionalBool
    is_business_order: OptionalBool
    is_replacement_order: OptionalBool
    order_total: Money
    number_of_items_shipped: int
    number_of_items_unshipped: int
    ship_service_level: OptionalStr
    shipment_service_level_category: OptionalStr
    earliest_ship_date: OptionalDatetime
    latest_ship_date: OptionalDatetime
    sales_channel: OptionalStr
    marketplace_id: OptionalStr
    payment_method: OptionalStr
    payment_method_details: list[str] | None = None
    shipping_address: ShippingAddress | None = None


class OrderItem(BaseAmazonModel):
    order_item_id: str
    asin: str = Field(alias="ASIN")
    seller_sku: str = Field(default=None, alias="SellerSKU")
    title: OptionalStr
    quantity_ordered: int
    quantity_shipped: int
    is_gift: OptionalBool
    item_price: Money
    item_tax: Money | None = None
    promotion_discount: Money | None = None
    promotion_discount_tax: Money | None = None
    product_info: ProductInfo | None = None


class OrderItemsSummary(BaseAmazonModel):
    order_items: list[OrderItem]


class FullOrder(BaseAmazonModel):
    order: OrderSummary
    items: OrderItemsSummary
