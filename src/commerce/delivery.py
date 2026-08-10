"""Selectable delivery options used at the end of checkout."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DeliveryOption:
    id: str
    label: str
    description: str
    cost_minor: int
    eta_min_days: int
    eta_max_days: int
    free_shipping_eligible: bool = True


@dataclass(frozen=True, slots=True)
class DeliveryOptionsCatalog:
    """Configured delivery methods with optional free-shipping threshold."""

    options: tuple[DeliveryOption, ...]
    free_threshold_minor: int

    def __post_init__(self) -> None:
        if not self.options:
            raise ValueError("at least one delivery option is required")
        ids = [item.id for item in self.options]
        if len(ids) != len(set(ids)):
            raise ValueError("delivery option ids must be unique")

    def get(self, option_id: str) -> DeliveryOption:
        match = next((item for item in self.options if item.id == option_id), None)
        if match is None:
            raise KeyError(option_id)
        return match

    def priced_cost(self, option_id: str, subtotal_after_discount_minor: int) -> int:
        option = self.get(option_id)
        if (
            option.free_shipping_eligible
            and self.free_threshold_minor > 0
            and subtotal_after_discount_minor >= self.free_threshold_minor
        ):
            return 0
        return option.cost_minor

    def quoted(
        self, subtotal_after_discount_minor: int
    ) -> tuple[tuple[DeliveryOption, int], ...]:
        return tuple(
            (option, self.priced_cost(option.id, subtotal_after_discount_minor))
            for option in self.options
        )


def default_delivery_catalog(
    *,
    standard_minor: int,
    express_minor: int,
    pickup_minor: int,
    free_threshold_minor: int,
) -> DeliveryOptionsCatalog:
    return DeliveryOptionsCatalog(
        options=(
            DeliveryOption(
                id="standard",
                label="Standard delivery",
                description="Economy shipping to your address.",
                cost_minor=standard_minor,
                eta_min_days=3,
                eta_max_days=7,
            ),
            DeliveryOption(
                id="express",
                label="Express delivery",
                description="Faster shipping to your address.",
                cost_minor=express_minor,
                eta_min_days=1,
                eta_max_days=2,
            ),
            DeliveryOption(
                id="pickup",
                label="Store pickup",
                description="Collect from the local pickup point.",
                cost_minor=pickup_minor,
                eta_min_days=0,
                eta_max_days=1,
                free_shipping_eligible=False,
            ),
        ),
        free_threshold_minor=free_threshold_minor,
    )
