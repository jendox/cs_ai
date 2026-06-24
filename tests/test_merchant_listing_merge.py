from src.db.repositories.merchant_listing import _merge_optional_field, _merge_text_field


def test_merge_text_field_keeps_existing_name_when_incoming_empty() -> None:
    assert _merge_text_field("", "Existing title") == "Existing title"
    assert _merge_text_field(None, "Existing title") == "Existing title"
    assert _merge_text_field("New title", "Existing title") == "New title"


def test_merge_optional_field_keeps_existing_description_when_incoming_empty() -> None:
    assert _merge_optional_field(None, "Existing description") == "Existing description"
    assert _merge_optional_field("", "Existing description") == "Existing description"
    assert _merge_optional_field("New description", "Existing description") == "New description"
