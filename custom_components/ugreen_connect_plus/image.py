"""Image platform: the product photo of the charger itself.

Home Assistant has no picture of its own for a device -- a device carries a
name, a model and a serial and nothing that renders -- so the photo UGREEN
publishes for the model becomes an entity on the charger instead, where the
device page can show it.

It is the only picture here that is not a wallpaper. The screen's own images
are signed links that expire within minutes and go through ``image_proxy``;
this one is a plain static URL on the same CDN, needs no credentials and does
not go stale, so the image component can fetch it directly.
"""

from __future__ import annotations

from homeassistant.components.image import ImageEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import UgreenConfigEntry
from .coordinator import UgreenCoordinator, device_key
from .entity import UgreenDeviceEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: UgreenConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _add_new_devices() -> None:
        new = []
        for device in coordinator.data.get("devices", []):
            key = device_key(device)
            if key is None or key in known:
                continue
            # The product metadata is fetched once per charger and can fail on
            # its own without taking the readings down. Until it arrives there
            # is no picture to show, so the entity waits rather than appearing
            # empty.
            product = coordinator.data.get("detail", {}).get(key) or {}
            if not product.get("image"):
                continue
            known.add(key)
            new.append(UgreenProductImage(hass, coordinator, key))
        if new:
            async_add_entities(new)

    _add_new_devices()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_devices))


class UgreenProductImage(UgreenDeviceEntity, ImageEntity):
    """The manufacturer's photo of this model."""

    _attr_translation_key = "product"

    def __init__(self, hass: HomeAssistant, coordinator: UgreenCoordinator, key: str) -> None:
        super().__init__(coordinator, key)
        ImageEntity.__init__(self, hass, verify_ssl=True)
        self._attr_unique_id = f"{key}_product_image"
        self._attr_image_url = self._product.get("image")
        # A picture with no timestamp reads as "unknown" and is never fetched.
        # Nothing here dates the photo, so the moment it first became known is
        # the honest answer.
        self._attr_image_last_updated = dt_util.utcnow()

    @callback
    def _handle_coordinator_update(self) -> None:
        # A model's photo is effectively fixed, but the URL is the cloud's to
        # change -- a new one has to invalidate what was cached under the old.
        if (url := self._product.get("image")) != self._attr_image_url:
            self._attr_image_url = url
            self._attr_image_last_updated = dt_util.utcnow()
            self._cached_image = None
        super()._handle_coordinator_update()
