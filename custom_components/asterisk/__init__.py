import asyncio
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_DEVICES,
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_USERNAME,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

from .ami_client import SimpleAMIClient, AMIEvent
from .const import AUTO_RECONNECT, CLIENT, DOMAIN, PLATFORMS, SIP_LOADED, PJSIP_LOADED

_LOGGER = logging.getLogger(__name__)

# Key for storing state refresh callbacks
STATE_REFRESH_CALLBACKS = "state_refresh_callbacks"

# Timeout for device discovery (seconds)
DISCOVERY_TIMEOUT = 10


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update - reload the integration."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Setup up a config entry."""
    _LOGGER.warning("Setting up Asterisk integration for %s", entry.data.get(CONF_HOST))

    # Events to signal discovery completion
    sip_complete = asyncio.Event()
    pjsip_complete = asyncio.Event()
    devices = []

    def create_PJSIP_device(event: AMIEvent):
        _LOGGER.debug("Creating PJSIP device: %s", event)
        device = {
            "extension": event["ObjectName"],
            "tech": "PJSIP",
            "status": event["DeviceState"],
        }
        devices.append(device)

    def create_SIP_device(event: AMIEvent):
        _LOGGER.debug("Creating SIP device: %s", event)
        device = {
            "extension": event["ObjectName"],
            "tech": "SIP",
            "status": event["Status"],
        }
        devices.append(device)

    def on_sip_complete(event: AMIEvent):
        _LOGGER.debug("SIP discovery complete")
        hass.loop.call_soon_threadsafe(sip_complete.set)

    def on_pjsip_complete(event: AMIEvent):
        _LOGGER.debug("PJSIP discovery complete")
        hass.loop.call_soon_threadsafe(pjsip_complete.set)

    async def send_action_service(call) -> None:
        """Send action service."""
        action = call.data.get("action")
        params = call.data.get("parameters", {})
        _LOGGER.debug("Sending action: %s with params %s", action, params)

        try:
            response = hass.data[DOMAIN][entry.entry_id][CLIENT].send_action(action, **params)
            _LOGGER.debug("Action response: %s", response)
        except Exception as e:
            _LOGGER.warning("Failed to send action: %s", e)

    # Create our simple AMI client
    client = SimpleAMIClient(
        host=entry.data[CONF_HOST],
        port=entry.data[CONF_PORT],
        username=entry.data[CONF_USERNAME],
        secret=entry.data[CONF_PASSWORD],
    )

    # Connect to AMI
    try:
        if not client.connect():
            raise ConfigEntryNotReady("Failed to connect to AMI")
    except Exception as e:
        raise ConfigEntryNotReady(e)

    # Register event listeners BEFORE sending actions
    client.add_event_listener(create_SIP_device, white_list=["PeerEntry"])
    client.add_event_listener(on_sip_complete, white_list=["PeerlistComplete"])
    client.add_event_listener(create_PJSIP_device, white_list=["EndpointList"])
    client.add_event_listener(on_pjsip_complete, white_list=["EndpointListComplete"])

    # Small delay to ensure listeners are ready in reader thread
    await asyncio.sleep(0.1)

    # Send discovery actions
    sip_response = client.send_action("SIPpeers")
    if "Error" in sip_response:
        _LOGGER.debug("SIP module not loaded. Skipping SIP devices.")
        sip_complete.set()

    pjsip_response = client.send_action("PJSIPShowEndpoints")
    if "Error" in pjsip_response:
        _LOGGER.debug("PJSIP module not loaded. Skipping PJSIP devices.")
        pjsip_complete.set()

    # Wait for both discoveries to complete with timeout
    try:
        await asyncio.wait_for(
            asyncio.gather(sip_complete.wait(), pjsip_complete.wait()),
            timeout=DISCOVERY_TIMEOUT
        )
        _LOGGER.debug("Device discovery complete. Found %d devices.", len(devices))
    except asyncio.TimeoutError:
        _LOGGER.warning("Device discovery timed out after %ds. Found %d devices.",
                       DISCOVERY_TIMEOUT, len(devices))

    # Note: We intentionally do NOT clean up stale devices here.
    # Discovery only returns currently registered devices, not all configured extensions.
    # Offline phones would be incorrectly removed as "stale" if we cleaned up here.
    # Users should manually delete orphaned devices via the HA UI if needed.

    # Store data
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        CLIENT: client,
        AUTO_RECONNECT: None,  # Our client handles reconnection internally
        CONF_DEVICES: devices,
        SIP_LOADED: True,
        PJSIP_LOADED: True,
        STATE_REFRESH_CALLBACKS: [],  # List of callbacks to refresh sensor states
    }

    def on_reconnect(ami_client, message):
        """Handle AMI reconnection - refresh all device states."""
        _LOGGER.warning("AMI reconnected - refreshing device states")

        # Query device states - DeviceStateList triggers DeviceStateChange events
        # for each device, which sensors are already listening for
        response = ami_client.send_action("DeviceStateList")
        _LOGGER.debug("DeviceStateList response: %s", response)

        # Call any registered refresh callbacks
        callbacks = hass.data[DOMAIN][entry.entry_id].get(STATE_REFRESH_CALLBACKS, [])
        for callback in callbacks:
            try:
                callback()
            except Exception as e:
                _LOGGER.error("Error in state refresh callback: %s", e)

    client.set_on_reconnect(on_reconnect)

    def on_disconnect(ami_client, error):
        """Handle AMI disconnection."""
        _LOGGER.warning("AMI connection lost - will attempt to reconnect")

    client.set_on_disconnect(on_disconnect)

    # Register service
    hass.services.async_register(DOMAIN, "send_action", send_action_service)

    # Now load platforms - this happens synchronously in the async context
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Listen for options updates to reload the integration
    entry.async_on_unload(entry.add_update_listener(async_update_options))

    _LOGGER.warning("Asterisk integration setup complete with %d devices", len(devices))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    client = data[CLIENT]

    client.disconnect()

    unloaded = all(
        await asyncio.gather(
            *[
                hass.config_entries.async_forward_entry_unload(entry, component)
                for component in PLATFORMS
            ]
        )
    )

    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unloaded


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Reload a config entry."""
    await async_unload_entry(hass, entry)
    return await async_setup_entry(hass, entry)
