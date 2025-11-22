from pydantic import BaseModel, model_validator


class MerchantListingRow(BaseModel):
    asin: str
    seller_sku: str
    item_name: str | None = None
    item_description: str | None = None
    fulfillment_channel: str | None = None
    status: str | None = None
    search_text: str | None = None

    @model_validator(mode="after")
    def set_search_text(self):
        if self.search_text:
            return self

        item_name = (self.item_name or "").strip()
        item_description = (self.item_description or "").strip() or None

        if item_description:
            self.search_text = f"{item_name}\n\n{item_description}"
        else:
            self.search_text = item_name

        return self
