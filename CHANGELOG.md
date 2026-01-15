# Changelog

All notable changes to this project will be documented in this file.

## [1.1.1] - 2026-01-15

### Fixed
- Fixed startup race condition where sensors showed "unavailable" after HA restart
- Fixed state updates not reflecting in Home Assistant (thread-safe scheduling)

## [1.1.0] - 2026-01-15

### Changed
- Replaced `asterisk-ami` library with custom socket-based AMI client
- Changed `iot_class` from `local_polling` to `local_push` (events are pushed from Asterisk)
- Improved event filtering - now handled in event handlers instead of at registration

### Fixed
- Fixed critical issue where DeviceStateChange events were not being delivered to Home Assistant
- Phone state sensors (Ringing, In use, Not in use) now update correctly
- AMI connection stability improved - no more frequent disconnects

### Removed
- Removed dependency on `asterisk-ami` library (now uses built-in socket-based client)

## [1.0.9] - Previous versions

See git history for changes prior to 1.1.0.
