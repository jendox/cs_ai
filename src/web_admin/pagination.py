DEFAULT_PAGE_LIMIT = 10
PAGE_LIMIT_OPTIONS: tuple[int, ...] = (10, 20, 50)


def parse_page_limit(value: int | None) -> int:
    if value in PAGE_LIMIT_OPTIONS:
        return value
    return DEFAULT_PAGE_LIMIT
