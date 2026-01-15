from .ami_client import SimpleAMIClient
from .const import CLIENT, CONF_DEBUG_LOGGING, DOMAIN


class AsteriskDeviceEntity:
    """Base entity for Asterisk devices."""

    def __init__(self, hass, entry, device):
        """Initialize the sensor."""
        self._hass = hass  # Store hass reference for non-blocking callbacks
        self._device = device
        self._entry = entry
        self._unique_id_prefix = f"{entry.entry_id}_{device['extension']}"
        self._ami_client: SimpleAMIClient = hass.data[DOMAIN][entry.entry_id][CLIENT]
        self._name: str
        self._unique_id: str

    def _schedule_update(self):
        """Schedule a state update in Home Assistant's event loop (thread-safe)."""
        self._hass.loop.call_soon_threadsafe(self.schedule_update_ha_state)

    @property
    def _debug_logging(self) -> bool:
        """Return True if debug logging is enabled."""
        try:
            return self._entry.options.get(CONF_DEBUG_LOGGING, False)
        except Exception:
            return False

    @property
    def device_info(self):
        """Return the device info."""
        return {
            "identifiers": {(DOMAIN, self._unique_id_prefix)},
            "name": f"{self._device['tech']}/{self._device['extension']}",
            "manufacturer": "Asterisk",
            "model": self._device["tech"],
            "via_device": (DOMAIN, f"{self._entry.entry_id}_server"),
        }

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        return self._name

    @property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return self._unique_id
