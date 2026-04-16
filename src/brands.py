"""Stable brand identifiers, decoupled from Zendesk numeric IDs.

Brand is an application-level concept.  The mapping between Brand and
the actual Zendesk ``brand_id`` is provided by :class:`BrandSettings`
(defined in :mod:`src.config`).
"""

from enum import StrEnum


class Brand(StrEnum):
    SUPERSELF = "superself"
    SMARTPARTS = "smartparts"
    CLEOCORA = "cleocora"

    @property
    def short(self) -> str:
        return _SHORT_LABELS.get(self, "??")

    @property
    def label(self) -> str:
        return self.name.title()


_SHORT_LABELS: dict[Brand, str] = {
    Brand.SUPERSELF: "SS",
    Brand.SMARTPARTS: "SP",
    Brand.CLEOCORA: "CC",
}
