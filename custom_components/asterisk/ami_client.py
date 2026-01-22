"""Simple AMI client using raw sockets - replaces asterisk-ami library."""
import logging
import socket
import threading
import time
from typing import Callable, Dict, List, Optional

_LOGGER = logging.getLogger(__name__)


class AMIEvent:
    """Represents an AMI event."""

    def __init__(self, name: str, data: Dict[str, str]):
        self.name = name
        self._data = data

    def __getitem__(self, key: str) -> str:
        return self._data.get(key, "")

    def get(self, key: str, default: str = "") -> str:
        return self._data.get(key, default)

    def __repr__(self):
        return f"AMIEvent({self.name}, {self._data})"


class SimpleAMIClient:
    """Simple AMI client with event handling."""

    def __init__(self, host: str, port: int, username: str, secret: str):
        self.host = host
        self.port = port
        self.username = username
        self.secret = secret
        self._sock: Optional[socket.socket] = None
        self._connected = False
        self._running = False
        self._reader_thread: Optional[threading.Thread] = None
        self._ping_thread: Optional[threading.Thread] = None
        self._event_listeners: List[tuple] = []  # (callback, whitelist, filters)
        self._on_disconnect: Optional[Callable] = None
        self._on_reconnect: Optional[Callable] = None
        self._reconnect_delay = 5
        self._ping_interval = 30  # Send ping every 30 seconds
        self._lock = threading.Lock()
        self._send_lock = threading.Lock()  # Separate lock for send operations

    def connect(self) -> bool:
        """Connect and login to AMI."""
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sock.settimeout(10)
            self._sock.connect((self.host, self.port))

            # Read banner
            banner = self._sock.recv(1024).decode().strip()
            _LOGGER.debug("AMI Banner: %s", banner)

            # Login
            login_cmd = f"Action: Login\r\nUsername: {self.username}\r\nSecret: {self.secret}\r\n\r\n"
            self._sock.send(login_cmd.encode())

            # Read login response
            response = self._recv_response()
            if "Success" in response:
                self._connected = True
                self._running = True
                self._start_reader()
                self._start_ping()
                _LOGGER.info("Connected to AMI at %s:%s", self.host, self.port)
                return True
            else:
                _LOGGER.error("AMI login failed: %s", response)
                return False
        except Exception as e:
            _LOGGER.error("Failed to connect to AMI: %s", e)
            return False

    def _recv_response(self) -> str:
        """Receive a complete response (until double newline)."""
        data = ""
        while "\r\n\r\n" not in data:
            chunk = self._sock.recv(4096).decode()
            if not chunk:
                break
            data += chunk
        return data

    def _start_reader(self):
        """Start the event reader thread."""
        self._reader_thread = threading.Thread(target=self._read_events, daemon=True)
        self._reader_thread.start()

    def _start_ping(self):
        """Start the ping thread for keep-alive."""
        self._ping_thread = threading.Thread(target=self._ping_loop, daemon=True)
        self._ping_thread.start()

    def _ping_loop(self):
        """Send periodic pings to keep connection alive and detect failures."""
        while self._running and self._connected:
            time.sleep(self._ping_interval)
            if not self._running or not self._connected:
                break
            try:
                # Send a Ping action - this is a lightweight AMI command
                response = self.ping()
                if not response:
                    _LOGGER.warning("AMI ping failed - no response")
                    self._handle_disconnect()
                    break
            except Exception as e:
                _LOGGER.warning("AMI ping error: %s", e)
                self._handle_disconnect()
                break

    def ping(self) -> bool:
        """Send a ping to AMI and return True if successful."""
        if not self._connected or not self._sock:
            return False
        try:
            with self._send_lock:
                cmd = "Action: Ping\r\n\r\n"
                self._sock.send(cmd.encode())
            # Response will be received by reader thread, just check we could send
            return True
        except Exception as e:
            _LOGGER.debug("Ping send failed: %s", e)
            return False

    def _read_events(self):
        """Read events from AMI socket."""
        buffer = ""
        self._sock.settimeout(1)  # Short timeout for checking _running flag

        while self._running:
            try:
                chunk = self._sock.recv(4096).decode()
                if not chunk:
                    _LOGGER.warning("AMI connection closed")
                    self._handle_disconnect()
                    break
                buffer += chunk

                # Process complete messages
                while "\r\n\r\n" in buffer:
                    msg, buffer = buffer.split("\r\n\r\n", 1)
                    self._process_message(msg)

            except socket.timeout:
                continue
            except Exception as e:
                if self._running:
                    _LOGGER.warning("AMI read error: %s", e)
                    self._handle_disconnect()
                break

    def _process_message(self, msg: str):
        """Process a single AMI message."""
        lines = msg.strip().split("\r\n")
        if not lines:
            return

        data = {}
        for line in lines:
            if ": " in line:
                key, value = line.split(": ", 1)
                data[key] = value

        if "Event" in data:
            event = AMIEvent(data["Event"], data)
            self._dispatch_event(event)

    def _dispatch_event(self, event: AMIEvent):
        """Dispatch event to registered listeners."""
        for callback, whitelist, filters in self._event_listeners:
            # Check whitelist
            if whitelist and event.name not in whitelist:
                continue

            # Check filters
            match = True
            for key, value in filters.items():
                if event.get(key) != value:
                    match = False
                    break

            if match:
                try:
                    callback(event)
                except Exception as e:
                    _LOGGER.error("Event handler error: %s", e)

    def _handle_disconnect(self):
        """Handle disconnection."""
        self._connected = False
        if self._on_disconnect:
            try:
                self._on_disconnect(self, None)
            except:
                pass

        # Auto-reconnect
        if self._running:
            threading.Thread(target=self._reconnect_loop, daemon=True).start()

    def _reconnect_loop(self):
        """Try to reconnect."""
        while self._running and not self._connected:
            time.sleep(self._reconnect_delay)
            _LOGGER.info("Attempting to reconnect to AMI...")
            if self.connect():
                if self._on_reconnect:
                    try:
                        self._on_reconnect(self, "Reconnected")
                    except:
                        pass
                break

    def add_event_listener(self, callback: Callable, white_list: List[str] = None, **filters):
        """Add an event listener."""
        with self._lock:
            self._event_listeners.append((callback, white_list or [], filters))

    def set_on_disconnect(self, callback: Callable):
        """Set disconnect callback."""
        self._on_disconnect = callback

    def set_on_reconnect(self, callback: Callable):
        """Set reconnect callback."""
        self._on_reconnect = callback

    def send_action(self, action: str, **params) -> str:
        """Send an action to AMI."""
        if not self._connected:
            return ""

        cmd = f"Action: {action}\r\n"
        for key, value in params.items():
            cmd += f"{key}: {value}\r\n"
        cmd += "\r\n"

        try:
            with self._send_lock:
                self._sock.send(cmd.encode())
                return self._recv_response()
        except Exception as e:
            _LOGGER.error("Failed to send action: %s", e)
            return ""

    def disconnect(self):
        """Disconnect from AMI."""
        self._running = False
        self._connected = False
        if self._sock:
            try:
                self._sock.close()
            except:
                pass
        _LOGGER.info("Disconnected from AMI")

    @property
    def connected(self) -> bool:
        return self._connected
