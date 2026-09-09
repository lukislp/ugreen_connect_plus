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
from .image_proxy import WallpaperUnavailable, async_wallpaper_bytes


async def async_setup_entry(
    hass: HomeAssistant,
    entry: UgreenConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    known_product: set[str] = set()
    known_wallpaper: set[str] = set()

    @callback
    def _add_new_devices() -> None:
        new: list[ImageEntity] = []
        for device in coordinator.data.get("devices", []):
            key = device_key(device)
            if key is None:
                continue
            if key not in known_wallpaper:
                known_wallpaper.add(key)
                new.append(UgreenWallpaperImage(hass, coordinator, key))
            # The product metadata is fetched once per charger and can fail on
            # its own without taking the readings down. Until it arrives there
            # is no picture to show, so the entity waits rather than appearing
            # empty -- which is why this one is tracked separately.
            product = coordinator.data.get("detail", {}).get(key) or {}
            if key not in known_product and product.get("image"):
                known_product.add(key)
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


class UgreenWallpaperImage(UgreenDeviceEntity, ImageEntity):
    """The picture the charger's screen is showing.

    Not fetched the way the product photo is. The links UGREEN issues for a
    wallpaper are signed and expire within minutes, so handing one to Home
    Assistant to fetch later would produce a broken picture more often than
    not. The bytes are read through the same path the dashboard card uses,
    which resolves the link at the moment it is needed and refreshes it once if
    it has gone stale.
    """

    _attr_translation_key = "wallpaper"

    def __init__(
        self, hass: HomeAssistant, coordinator: UgreenCoordinator, key: str
    ) -> None:
        super().__init__(coordinator, key)
        ImageEntity.__init__(self, hass)
        self._attr_unique_id = f"{key}_wallpaper_image"
        self._shown: str | None = self._current
        self._attr_image_last_updated = dt_util.utcnow()

    @property
    def _current(self) -> str | None:
        """The six-character id of the picture on the screen, if there is one."""
        return (self._reading or {}).get("wallpaper")

    @property
    def available(self) -> bool:
        # "None" is a real setting on this charger -- the screen simply shows
        # the clock -- and there is then no picture to publish.
        return super().available and bool(self._current)

    @callback
    def _handle_coordinator_update(self) -> None:
        if (current := self._current) != self._shown:
            self._shown = current
            self._attr_image_last_updated = dt_util.utcnow()
            self._cached_image = None
        super()._handle_coordinator_update()

    async def async_image(self) -> bytes | None:
        if not (image_id := self._current):
            return None
        try:
            found = await async_wallpaper_bytes(self.hass, self.coordinator, image_id)
        except WallpaperUnavailable:
            return None
        if found is None:
            return None
        self._attr_content_type = found[1]
        return found[0]
