"""pyserial URL handler: serial.serial_for_url("ble://<address or name>[?notify=<uuid>&write=<uuid>]").

Registered by elm/__init__.py. python-OBD opens its port with serial_for_url, so once this is
registered it talks to a BLE adapter exactly as it does to a USB one. Baud rate and framing
settings are accepted and ignored: there is no UART on this side of the radio.
"""

import urllib.parse

from serial.serialutil import PortNotOpenError, SerialBase, SerialException

from . import ble


class Serial(SerialBase):
    def open(self):
        if self._port is None:
            raise SerialException("Port must be configured before it can be used.")
        if self.is_open:
            raise SerialException("Port is already open.")
        target, opts = parse(self._port)
        if not target:
            raise SerialException("ble:// needs an address or name; elm.connect('ble') scans for one")
        link = ble.BleLink(target, notify=opts.get("notify"), write=opts.get("write"))
        try:
            link.open()
        except (ble.LinkError, TimeoutError, OSError) as e:
            ble.last_error = str(e)
            raise SerialException(str(e)) from e
        ble.last_link, ble.last_error = link, None
        self._link = link
        self.is_open = True

    def close(self):
        if self.is_open:
            self._link.close()
            self.is_open = False

    def _reconfigure_port(self, force_update=False):
        pass

    @property
    def in_waiting(self):
        if not self.is_open:
            raise PortNotOpenError()
        return self._link.in_waiting

    def read(self, size=1):
        if not self.is_open:
            raise PortNotOpenError()
        try:
            return self._link.read(size, self._timeout)
        except ble.LinkError as e:
            raise SerialException(str(e)) from e

    def write(self, data):
        if not self.is_open:
            raise PortNotOpenError()
        try:
            return self._link.write(data)
        except (ble.LinkError, TimeoutError) as e:
            raise SerialException(str(e)) from e

    def reset_input_buffer(self):
        if self.is_open:
            self._link.reset_input()

    def reset_output_buffer(self):
        pass

    def flush(self):
        pass


def parse(url):
    """'ble://AA:BB:CC:DD:EE:FF?notify=..&write=..' -> ('AA:BB:CC:DD:EE:FF', {'notify': .., 'write': ..})"""
    rest = url.split("://", 1)[1] if "://" in url else url
    target, _, query = rest.partition("?")
    opts = {k: v[0] for k, v in urllib.parse.parse_qs(query).items()}
    return urllib.parse.unquote(target), opts
