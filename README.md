# Asterisk Integration for Home Assistant

This is a fork of [TECH7Fox/asterisk-hass-integration](https://github.com/TECH7Fox/asterisk-hass-integration) with additional features and bug fixes.

This integration finds and adds all SIP and PJSIP devices to your Home Assistant.

## Features
- Device state tracking (Not in use, In use, Ringing, etc.)
- Connected line information with extra attributes
- DTMF event tracking
- Debug logging toggle in integration options

## Requirements
- A SIP/PBX server (e.g., FreePBX, Asterisk)
- HACS on your Home Assistant
- AMI manager configured with your Home Assistant IP allowed

## Installation
Download using **HACS**
1. Go to HACS
2. Click on the 3 dots in the upper right corner and click on `Custom repositories`
3. Paste `https://github.com/robsonfelix/asterisk-hass-integration` into `Add custom repository URL` and select Integration as the category
4. Click Add and verify the repository appears
5. Find Asterisk integration and click `INSTALL`
6. Restart Home Assistant
7. Go to Settings → Devices & Services → Add Integration → Asterisk
8. Fill in the AMI connection details and click Add

## Debug Logging
To enable debug logging:
1. Go to Settings → Devices & Services → Asterisk
2. Click Configure
3. Enable "Enable debug logging"
4. Check logs in Settings → System → Logs

## Troubleshooting
Most problems are due to PBX server configuration.

* For DTMF signaling to work in FreePBX, change the DTMF signaling mode. For intercom purposes, "SIP-INFO DTMF-Relay" is needed.

If you are still having problems, please [open an issue](https://github.com/robsonfelix/asterisk-hass-integration/issues).

## Credits
Original integration by [TECH7Fox](https://github.com/TECH7Fox)
