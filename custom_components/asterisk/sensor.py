import logging

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import CONF_DEVICES
from homeassistant.util.dt import now

from .ami_client import AMIEvent
from .base import AsteriskDeviceEntity
from .const import CONF_DEBUG_LOGGING, DOMAIN, STATE_ICONS, STATES

_LOGGER = logging.getLogger(__name__)


def map_state(raw_state: str) -> str:
    """Map a raw state string to a friendly state name.

    Handles both uppercase codes (NOT_INUSE) and friendly names (Not in use).
    """
    if raw_state in STATES:
        return STATES[raw_state]
    if raw_state.upper() in STATES:
        return STATES[raw_state.upper()]
    if raw_state in STATES.values():
        return raw_state
    return STATES["UNKNOWN"]


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up the Asterisk sensor platform."""
    devices = hass.data[DOMAIN][entry.entry_id][CONF_DEVICES]

    entities = []

    for device in devices:
        entities.append(DeviceStateSensor(hass, entry, device))
        entities.append(ConnectedLineSensor(hass, entry, device))
        entities.append(DTMFSentSensor(hass, entry, device))
        entities.append(DTMFReceivedSensor(hass, entry, device))

    async_add_entities(entities, False)


class DeviceStateSensor(AsteriskDeviceEntity, SensorEntity):
    """Sensor entity for the device state."""

    def __init__(self, hass, entry, device):
        """Initialize the sensor."""
        super().__init__(hass, entry, device)
        self._unique_id = f"{self._unique_id_prefix}_state"
        self._name = f"{device['extension']} State"
        # Map initial state through STATES for consistency
        # PJSIP returns friendly names like "Not in use", SIP returns codes like "NOT_INUSE"
        initial_status = device["status"]
        self._state = map_state(initial_status)
        self._device_filter = f"{device['tech']}/{device['extension']}"
        _LOGGER.warning(
            "DeviceStateSensor initialized for %s: filter=%s, initial=%s",
            device["extension"],
            self._device_filter,
            self._state,
        )
        # Listen to ALL DeviceStateChange events and filter in handler
        self._ami_client.add_event_listener(
            self.handle_event,
            white_list=["DeviceStateChange"],
        )
        # Also listen to DeviceStateListItem for state refresh after reconnection
        self._ami_client.add_event_listener(
            self.handle_state_list_item,
            white_list=["DeviceStateListItem"],
        )

    def handle_event(self, event: AMIEvent):
        """Handle an endpoint update event."""
        device = event.get("Device", "")
        state = event.get("State", "")

        # Only process events for our device
        if device != self._device_filter:
            return

        _LOGGER.warning("ASTERISK: %s -> %s", device, state)
        self._state = STATES.get(state, STATES["UNKNOWN"])
        self._schedule_update()  # Thread-safe: queue to HA event loop

    def handle_state_list_item(self, event: AMIEvent):
        """Handle DeviceStateListItem event for state refresh after reconnection."""
        device = event.get("Device", "")
        state = event.get("State", "")

        # Only process events for our device
        if device != self._device_filter:
            return

        _LOGGER.debug("DeviceStateListItem: %s -> %s", device, state)
        self._state = STATES.get(state, STATES["UNKNOWN"])
        self._schedule_update()  # Thread-safe: queue to HA event loop

    def handle_newstate(self, event: AMIEvent):
        """Handle a Newstate event to detect ringing at channel level."""
        channel_state_desc = event.get("ChannelStateDesc", "")
        exten = event.get("Exten", "")
        caller_id_num = event.get("CallerIDNum", "")
        connected_line_num = event.get("ConnectedLineNum", "")

        _LOGGER.info(
            "Newstate for %s: ChannelStateDesc=%s, CallerIDNum=%s, ConnectedLineNum=%s, Exten=%s",
            self._device["extension"],
            channel_state_desc,
            caller_id_num,
            connected_line_num,
            exten,
        )
        if self._debug_logging:
            _LOGGER.warning(
                "Newstate event for %s: ChannelStateDesc=%s, Channel=%s, CallerIDNum=%s, ConnectedLineNum=%s, Exten=%s",
                self._device["extension"],
                channel_state_desc,
                event.get("Channel"),
                caller_id_num,
                connected_line_num,
                exten,
            )
        # Check if this is a ringing state
        if channel_state_desc in ("Ringing", "Ring"):
            # Set to Ringing if this extension is being called (via Exten, ConnectedLineNum, or caller)
            ext = self._device["extension"]
            if exten == ext or connected_line_num == ext or caller_id_num == ext:
                self._state = STATES["RINGING"]
                self._schedule_update()  # Non-blocking: queue to HA event loop

    def handle_dial(self, event: AMIEvent):
        """Handle DialBegin/DialState events to detect ringing."""
        dial_status = event.get("DialStatus", "")
        dest_caller_id = event.get("DestCallerIDNum", "")

        _LOGGER.info(
            "Dial event for %s: DialStatus=%s, DestCallerIDNum=%s",
            self._device["extension"],
            dial_status,
            dest_caller_id,
        )
        if self._debug_logging:
            _LOGGER.warning(
                "Dial event for %s: Event=%s, DialStatus=%s, DestCallerIDNum=%s, DestChannel=%s",
                self._device["extension"],
                event.name,
                dial_status,
                dest_caller_id,
                event.get("DestChannel"),
            )
        # DialBegin means the device is starting to ring
        if event.name == "DialBegin" or dial_status == "RINGING":
            if dest_caller_id == self._device["extension"]:
                self._state = STATES["RINGING"]
                self._schedule_update()  # Non-blocking: queue to HA event loop

    @property
    def native_value(self) -> str:
        """Return the sensor value."""
        return self._state

    @property
    def icon(self) -> str:
        """Return the icon of the sensor."""
        return STATE_ICONS.get(self._state, STATE_ICONS["Unknown"])


class ConnectedLineSensor(AsteriskDeviceEntity, SensorEntity):
    """Sensor entity for the connected line number."""

    def __init__(self, hass, entry, device):
        """Initialize the sensor."""
        super().__init__(hass, entry, device)
        self._unique_id = f"{self._unique_id_prefix}_connected_line"
        self._name = f"{device['extension']} Connected Line"
        self._state = "None"
        self._extra_attributes = {}
        self._ami_client.add_event_listener(
            self.handle_new_connected_line,
            white_list=["NewConnectedLine"],
            CallerIDNum=device["extension"],
        )
        self._ami_client.add_event_listener(
            self.handle_new_connected_line,
            white_list=["NewConnectedLine"],
            ConnectedLineNum=device["extension"],
        )
        self._ami_client.add_event_listener(
            self.handle_hangup,
            white_list=["Hangup"],
            CallerIDNum=device["extension"],
        )
        self._ami_client.add_event_listener(
            self.handle_hangup,
            white_list=["Hangup"],
            ConnectedLineNum=device["extension"],
        )
        self._ami_client.add_event_listener(
            self.handle_new_channel,
            white_list=["Newchannel"],
            CallerIDNum=device["extension"],
        )
        self._ami_client.add_event_listener(
            self.handle_new_channel,
            white_list=["Newchannel"],
            ConnectedLineNum=device["extension"],
        )

    def handle_new_connected_line(self, event: AMIEvent):
        """Handle an NewConnectedLine event."""
        if self._debug_logging:
            _LOGGER.warning(
                "NewConnectedLine event for %s: CallerIDNum=%s, ConnectedLineNum=%s, ChannelStateDesc=%s, Channel=%s",
                self._device["extension"],
                event.get("CallerIDNum"),
                event.get("ConnectedLineNum"),
                event.get("ChannelStateDesc"),
                event.get("Channel"),
            )
        if event["ConnectedLineNum"] != self._device["extension"]:
            self._state = event["ConnectedLineNum"]
        else:
            self._state = event["CallerIDNum"]
        self._extra_attributes = {
            "Channel": event["Channel"],
            "ChannelState": event["ChannelState"],
            "ChannelStateDesc": event["ChannelStateDesc"],
            "CallerIDNum": event["CallerIDNum"],
            "CallerIDName": event["CallerIDName"],
            "ConnectedLineNum": event["ConnectedLineNum"],
            "ConnectedLineName": event["ConnectedLineName"],
            "Exten": event["Exten"],
            "Context": event["Context"],
        }
        self._schedule_update()  # Non-blocking: queue to HA event loop

    def handle_hangup(self, event: AMIEvent):
        """Handle an Hangup event."""
        if self._debug_logging:
            _LOGGER.warning(
                "Hangup event for %s: CallerIDNum=%s, ConnectedLineNum=%s, ChannelStateDesc=%s, Cause=%s, Channel=%s",
                self._device["extension"],
                event.get("CallerIDNum"),
                event.get("ConnectedLineNum"),
                event.get("ChannelStateDesc"),
                event.get("Cause"),
                event.get("Channel"),
            )
        if event["Cause"] != "26":
            self._state = "None"
            self._extra_attributes = {
                "Channel": event["Channel"],
                "ChannelState": event["ChannelState"],
                "ChannelStateDesc": event["ChannelStateDesc"],
                "CallerIDNum": event["CallerIDNum"],
                "CallerIDName": event["CallerIDName"],
                "ConnectedLineNum": event["ConnectedLineNum"],
                "ConnectedLineName": event["ConnectedLineName"],
                "Exten": event["Exten"],
                "Context": event["Context"],
                "Cause": event["Cause"],
                "Cause-txt": event["Cause-txt"],
            }
            self._schedule_update()  # Non-blocking: queue to HA event loop

    def handle_new_channel(self, event: AMIEvent):
        """Handle an NewChannel event."""
        if self._debug_logging:
            _LOGGER.warning(
                "Newchannel event for %s: CallerIDNum=%s, ConnectedLineNum=%s, ChannelStateDesc=%s, Channel=%s, Exten=%s",
                self._device["extension"],
                event.get("CallerIDNum"),
                event.get("ConnectedLineNum"),
                event.get("ChannelStateDesc"),
                event.get("Channel"),
                event.get("Exten"),
            )
        self._state = "None"
        self._extra_attributes = {
            "Channel": event["Channel"],
            "ChannelState": event["ChannelState"],
            "ChannelStateDesc": event["ChannelStateDesc"],
            "CallerIDNum": event["CallerIDNum"],
            "CallerIDName": event["CallerIDName"],
            "ConnectedLineNum": event["ConnectedLineNum"],
            "ConnectedLineName": event["ConnectedLineName"],
            "Exten": event["Exten"],
            "Context": event["Context"],
        }
        self._schedule_update()  # Non-blocking: queue to HA event loop

    @property
    def native_value(self) -> str:
        """Return registered state."""
        return self._state

    @property
    def extra_state_attributes(self):
        """Return the state attributes."""
        return self._extra_attributes

    @property
    def icon(self) -> str:
        """Return the icon of the sensor."""
        return (
            "mdi:phone-remove"
            if self._state == "None"
            else "mdi:phone-incoming-outgoing"
        )


class DTMFSentSensor(AsteriskDeviceEntity, SensorEntity):
    """Sensor entity with the latest DTMF sent."""

    def __init__(self, hass, entry, device):
        """Initialize the sensor."""
        super().__init__(hass, entry, device)
        self._unique_id = f"{self._unique_id_prefix}_dtmf_sent"
        self._name = f"{device['extension']} DTMF Sent"
        self._state = None
        self._extra_attributes = {}
        self._ami_client.add_event_listener(
            self.handle_dtmf,
            white_list=["DTMFBegin"],
            ConnectedLineNum=device["extension"],
            Direction="Sent",
        )

    def handle_dtmf(self, event: AMIEvent):
        """Handle an DTMF event."""
        self._state = now()
        self._extra_attributes = {
            "Channel": event["Channel"],
            "Digit": event["Digit"],
            "CallerIDNum": event["CallerIDNum"],
            "CallerIDName": event["CallerIDName"],
            "ConnectedLineNum": event["ConnectedLineNum"],
            "ConnectedLineName": event["ConnectedLineName"],
            "Context": event["Context"],
        }
        self._schedule_update()  # Non-blocking: queue to HA event loop

    @property
    def native_value(self) -> str:
        """Return registered state."""
        return self._state

    @property
    def device_class(self) -> SensorDeviceClass:
        return SensorDeviceClass.TIMESTAMP

    @property
    def extra_state_attributes(self):
        """Return the state attributes."""
        return self._extra_attributes


class DTMFReceivedSensor(AsteriskDeviceEntity, SensorEntity):
    """Sensor entity with the latest DTMF received."""

    def __init__(self, hass, entry, device):
        """Initialize the sensor."""
        super().__init__(hass, entry, device)
        self._unique_id = f"{self._unique_id_prefix}_dtmf_received"
        self._name = f"{device['extension']} DTMF Received"
        self._state = None
        self._extra_attributes = {}
        self._ami_client.add_event_listener(
            self.handle_dtmf,
            white_list=["DTMFBegin"],
            ConnectedLineNum=device["extension"],
            Direction="Received",
        )

    def handle_dtmf(self, event: AMIEvent):
        """Handle an DTMF event."""
        self._state = now()
        self._extra_attributes = {
            "Channel": event["Channel"],
            "Digit": event["Digit"],
            "ConnectedLineNum": event["ConnectedLineNum"],
            "ConnectedLineName": event["ConnectedLineName"],
            "Context": event["Context"],
        }
        self._schedule_update()  # Non-blocking: queue to HA event loop

    @property
    def native_value(self) -> str:
        """Return registered state."""
        return self._state

    @property
    def device_class(self) -> SensorDeviceClass:
        return SensorDeviceClass.TIMESTAMP

    @property
    def extra_state_attributes(self):
        """Return the state attributes."""
        return self._extra_attributes
