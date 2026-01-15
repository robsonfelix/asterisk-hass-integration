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


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update - reload the integration."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Setup up a config entry."""
    _LOGGER.warning("Setting up Asterisk integration for %s", entry.data.get(CONF_HOST))

    def create_PJSIP_device(event: AMIEvent):
        _LOGGER.debug("Creating PJSIP device: %s", event)
        device = {
            "extension": event["ObjectName"],
            "tech": "PJSIP",
            "status": event["DeviceState"],
        }
        hass.data[DOMAIN][entry.entry_id][CONF_DEVICES].append(device)

    def create_SIP_device(event: AMIEvent):
        _LOGGER.debug("Creating SIP device: %s", event)
        device = {
            "extension": event["ObjectName"],
            "tech": "SIP",
            "status": event["Status"],
        }
        hass.data[DOMAIN][entry.entry_id][CONF_DEVICES].append(device)

    def devices_complete(event: AMIEvent):
        sip_loaded = hass.data[DOMAIN][entry.entry_id][SIP_LOADED]
        pjsip_loaded = hass.data[DOMAIN][entry.entry_id][PJSIP_LOADED]
        if event.name == "PeerlistComplete":
            _LOGGER.debug("SIP loaded.")
            sip_loaded = True
            hass.data[DOMAIN][entry.entry_id][SIP_LOADED] = True
        elif event.name == "EndpointListComplete":
            _LOGGER.debug("PJSIP loaded.")
            pjsip_loaded = True
            hass.data[DOMAIN][entry.entry_id][PJSIP_LOADED] = True

        if sip_loaded and pjsip_loaded:
            _LOGGER.debug("Both SIP and PJSIP loaded. Loading platforms.")
            asyncio.run_coroutine_threadsafe(
                hass.config_entries.async_forward_entry_setups(entry, PLATFORMS),
                hass.loop
            )

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

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        CLIENT: client,
        AUTO_RECONNECT: None,  # Our client handles reconnection internally
        CONF_DEVICES: [],
        SIP_LOADED: False,
        PJSIP_LOADED: False,
    }
    hass.services.async_register(DOMAIN, "send_action", send_action_service)

    # Register event listeners for SIP devices
    client.add_event_listener(create_SIP_device, white_list=["PeerEntry"])
    client.add_event_listener(devices_complete, white_list=["PeerlistComplete"])
    response = client.send_action("SIPpeers")
    if "Error" in response:
        _LOGGER.debug("SIP module not loaded. Skipping SIP devices.")
        hass.data[DOMAIN][entry.entry_id][SIP_LOADED] = True

    # Register event listeners for PJSIP devices
    client.add_event_listener(create_PJSIP_device, white_list=["EndpointList"])
    client.add_event_listener(devices_complete, white_list=["EndpointListComplete"])
    response = client.send_action("PJSIPShowEndpoints")
    if "Error" in response:
        _LOGGER.debug("PJSIP module not loaded. Skipping PJSIP devices.")
        hass.data[DOMAIN][entry.entry_id][PJSIP_LOADED] = True

    # Listen for options updates to reload the integration
    entry.async_on_unload(entry.add_update_listener(async_update_options))

    _LOGGER.warning("Asterisk integration setup complete")
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
