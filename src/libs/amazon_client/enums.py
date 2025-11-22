from enum import StrEnum

AUTH_URL = "https://api.amazon.com/auth/o2/token"

HTTPX_DEFAULT_TIMEOUT = 20.0
HTTPX_READ_TIMEOUT = 30.0
HTTPX_MAX_CONNECTIONS = 20


class EndpointRegion(StrEnum):
    EU = "EU"
    NA = "NA"


SPAPI_ENDPOINTS: dict[EndpointRegion, str] = {
    EndpointRegion.EU: "https://sellingpartnerapi-eu.amazon.com",
    EndpointRegion.NA: "https://sellingpartnerapi-na.amazon.com",
}


class MarketplaceId(StrEnum):
    # EU marketplaces
    UK = "A1F83G8C2ARO7P"
    DE = "A1PA6795UKMFR9"
    FR = "A13V1IB3VIYZZH"
    IT = "APJ6JRA9NG5V4"
    ES = "A1RKKUPIHCS9HS"
    # US marketplace
    US = "ATVPDKIKX0DER"

    @classmethod
    def eu_marketplaces(cls) -> set["MarketplaceId"]:
        return {marketplace for marketplace in cls if marketplace != cls.US}


class FulfillmentChannel(StrEnum):
    AFN = "AFN"
    MFN = "MFN"


class ReportType(StrEnum):
    GET_MERCHANT_LISTINGS_ALL_DATA = "GET_MERCHANT_LISTINGS_ALL_DATA"


class ReportStatus(StrEnum):
    DONE = "DONE"
    CANCELLED = "CANCELLED"
    FATAL = "FATAL"
    IN_QUEUE = "IN_QUEUE"
    IN_PROGRESS = "IN_PROGRESS"
    DONE_NO_DATA = "DONE_NO_DATA"

    @classmethod
    def failed(cls) -> set["ReportStatus"]:
        return {cls.FATAL, cls.CANCELLED}
