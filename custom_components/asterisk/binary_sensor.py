import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import CONF_DEVICES

from .ami_client import SimpleAMIClient, AMIEvent
from .base import AsteriskDeviceEntity
from .const import AUTO_RECONNECT, CLIENT, DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up the Asterisk sensor platform."""
    devices = hass.data[DOMAIN][entry.entry_id][CONF_DEVICES]

    entities = [AMIConnected(hass, entry)]

    for device in devices:
        entities.append(RegisteredSensor(hass, entry, device))
        entities.append(IncomingCallSensor(hass, entry, device))

    async_add_entities(entities, False)


class RegisteredSensor(AsteriskDeviceEntity, BinarySensorEntity):
    """Binary entity for the registered state."""

    def __init__(self, hass, entry, device):
        """Initialize the sensor."""
        super().__init__(hass, entry, device)
        self._unique_id = f"{self._unique_id_prefix}_registered"
        self._name = f"{device['extension']} Registered"
        self._state = (
            device["status"] != "Unavailable" and device["status"] != "Unknown"
        )
        self._device_filter = f"{device['tech']}/{device['extension']}"
        self._ami_client.add_event_listener(
            self.handle_state_change,
            white_list=["DeviceStateChange"],
        )

    def handle_state_change(self, event: AMIEvent):
        """Handle an device state change event."""
        device = event.get("Device", "")
        if device != self._device_filter:
            return
        state = event.get("State", "")
        self._state = state != "UNAVAILABLE" and state != "UNKNOWN"
        self._schedule_update()  # Non-blocking: queue to HA event loop

    @property
    def is_on(self) -> bool:
        """Return registered state."""
        return self._state

    @property
    def icon(self) -> str:
        """Return the icon of the sensor."""
        return "mdi:phone-check" if self._state else "mdi:phone-off"


class IncomingCallSensor(AsteriskDeviceEntity, BinarySensorEntity):
    """Binary sensor for detecting incoming calls on a device/trunk."""

    def __init__(self, hass, entry, device):
        """Initialize the sensor."""
        super().__init__(hass, entry, device)
        self._unique_id = f"{self._unique_id_prefix}_incoming_call"
        self._name = f"{device['extension']} Incoming Call"
        self._state = False
        self._extra_attributes = {}
        self._channel_pattern = f"{device['tech']}/{device['extension']}-"
        self._active_channel = None

        # Listen to Newchannel events to detect incoming calls
        self._ami_client.add_event_listener(
            self.handle_new_channel,
            white_list=["Newchannel"],
        )

        # Listen to Hangup events to detect call end
        self._ami_client.add_event_listener(
            self.handle_hangup,
            white_list=["Hangup"],
        )

        _LOGGER.debug(
            "IncomingCallSensor initialized for %s: pattern=%s",
            device["extension"],
            self._channel_pattern,
        )

    def handle_new_channel(self, event: AMIEvent):
        """Handle Newchannel event to detect incoming calls."""
        channel = event.get("Channel", "")

        # Only process channels matching our device pattern
        if not channel.startswith(self._channel_pattern):
            return

        caller_id_num = event.get("CallerIDNum", "")
        caller_id_name = event.get("CallerIDName", "")
        exten = event.get("Exten", "")
        context = event.get("Context", "")
        channel_state = event.get("ChannelStateDesc", "")

        _LOGGER.warning(
            "IncomingCallSensor: Newchannel for %s: Channel=%s, CallerIDNum=%s, CallerIDName=%s, Exten=%s, Context=%s, State=%s",
            self._device["extension"],
            channel,
            caller_id_num,
            caller_id_name,
            exten,
            context,
            channel_state,
        )

        # Store the active channel and turn on
        self._active_channel = channel
        self._state = True
        self._extra_attributes = {
            "channel": channel,
            "caller_id": caller_id_num,
            "caller_id_name": caller_id_name,
            "exten": exten,
            "context": context,
            "channel_state": channel_state,
        }
        self._schedule_update()

    def handle_hangup(self, event: AMIEvent):
        """Handle Hangup event to detect call end."""
        channel = event.get("Channel", "")

        # Only process hangup for our active channel
        if not channel.startswith(self._channel_pattern):
            return

        # If this is our active channel, turn off
        if self._active_channel and channel == self._active_channel:
            _LOGGER.warning(
                "IncomingCallSensor: Hangup for %s: Channel=%s",
                self._device["extension"],
                channel,
            )
            self._state = False
            self._active_channel = None
            self._extra_attributes = {
                "channel": channel,
                "caller_id": event.get("CallerIDNum", ""),
                "caller_id_name": event.get("CallerIDName", ""),
                "hangup_cause": event.get("Cause", ""),
                "hangup_cause_txt": event.get("Cause-txt", ""),
            }
            self._schedule_update()

    @property
    def is_on(self) -> bool:
        """Return True if there is an active incoming call."""
        return self._state

    @property
    def extra_state_attributes(self):
        """Return the state attributes."""
        return self._extra_attributes

    @property
    def icon(self) -> str:
        """Return the icon of the sensor."""
        return "mdi:phone-ring" if self._state else "mdi:phone-incoming"

    @property
    def device_class(self) -> str:
        """Return the device class."""
        return BinarySensorDeviceClass.OCCUPANCY


class AMIConnected(BinarySensorEntity):
    """Binary entity for the AMI connection state."""

    def __init__(self, hass, entry):
        """Initialize the sensor."""
        self._hass = hass  # Store hass reference for non-blocking callbacks
        self._entry = entry
        self._unique_id = f"{self._entry.entry_id}_connected"
        self._name = "AMI Connected"
        self._ami_client: SimpleAMIClient = hass.data[DOMAIN][entry.entry_id][CLIENT]
        self._state: bool = self._ami_client.connected

        # Set up disconnect/reconnect callbacks
        self._ami_client.set_on_disconnect(self.on_disconnect)
        self._ami_client.set_on_reconnect(self.on_reconnect)

        # Get Asterisk version
        response = self._ami_client.send_action("CoreSettings")
        self._asterisk_version = "Unknown"
        if response:
            for line in response.split("\r\n"):
                if line.startswith("AsteriskVersion:"):
                    self._asterisk_version = line.split(": ", 1)[1]
                    break

    def _schedule_update(self):
        """Schedule a state update in Home Assistant's event loop (thread-safe)."""
        self._hass.loop.call_soon_threadsafe(self.schedule_update_ha_state)

    def on_disconnect(self, client, response):
        _LOGGER.debug("Disconnected from AMI: %s", response)
        self._state = False
        self._schedule_update()  # Non-blocking: queue to HA event loop

    def on_reconnect(self, client, response):
        _LOGGER.debug("Reconnected to AMI: %s", response)
        self._state = True
        self._schedule_update()  # Non-blocking: queue to HA event loop

    @property
    def device_info(self):
        """Return the device info."""
        return {
            "identifiers": {(DOMAIN, f"{self._entry.entry_id}_server")},
            "name": "Asterisk Server",
            "manufacturer": "Asterisk",
            "model": "PBX",
            "configuration_url": f"http://{self._entry.data['host']}",
            "sw_version": self._asterisk_version,
        }

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        return self._name

    @property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return self._unique_id

    @property
    def is_on(self) -> bool:
        """Return connected state."""
        return self._state

    @property
    def device_class(self) -> str:
        """Return the device class of the sensor."""
        return BinarySensorDeviceClass.CONNECTIVITY
