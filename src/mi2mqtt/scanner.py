"""Listening for sensor advertisements with Bleak (BlueZ over D-Bus)."""

import asyncio
import logging
from collections.abc import Callable

from bleak import BleakScanner
from bleak.args.bluez import OrPattern
from bleak.assigned_numbers import AdvertisementDataType
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from bleak.exc import BleakError

from mi2mqtt.config import BleConfig
from mi2mqtt.parsers import UUIDS

log = logging.getLogger("mi2mqtt.scanner")

# BlueZ needs at least one pattern for passive scanning. A pattern on the service data UUID does not
# work: BlueZ (e.g. 5.82) compares service data patterns with the bytes after the UUID. So match the
# common values of the Flags field instead, which every connectable BLE device sends (Home Assistant
# does the same), and filter on the service data UUID in `detected`.
OR_PATTERNS = [
    OrPattern(0, AdvertisementDataType.FLAGS, flags)
    for flags in (b"\x02", b"\x06", b"\x1a")  # LE general discoverable, + BR/EDR not supported, + dual mode
]

PacketCallback = Callable[[str, int, str, bytes], None]  # mac, rssi, uuid, service data


class ScannerError(Exception):
    pass


async def scan(config: BleConfig, on_packet: PacketCallback, idle_timeout: float | None = None) -> None:
    """Scan until cancelled, calling `on_packet` for every sensor advertisement.

    When nothing at all is received for `idle_timeout` seconds, the scanner is restarted.
    """
    received = asyncio.Event()

    def detected(device: BLEDevice, advertisement: AdvertisementData) -> None:
        for uuid, data in advertisement.service_data.items():
            if uuid in UUIDS:
                received.set()
                try:
                    on_packet(device.address.upper(), advertisement.rssi, uuid, data)
                except Exception:
                    log.exception("error handling packet from %s", device.address)

    bluez: dict = {"or_patterns": OR_PATTERNS}
    if config.adapter:
        bluez["adapter"] = config.adapter

    while True:
        scanner = BleakScanner(detected, scanning_mode=config.scanning_mode, bluez=bluez)
        try:
            await scanner.start()
        except BleakError as error:
            hint = (
                "; or set ble.scanning_mode: active (see README)" if config.scanning_mode == "passive" else ""
            )
            raise ScannerError(f"cannot start {config.scanning_mode} scanning: {error}{hint}") from error
        log.info("scanning (%s%s)", config.scanning_mode, f", {config.adapter}" if config.adapter else "")
        try:
            while True:
                received.clear()
                await asyncio.wait_for(received.wait(), idle_timeout)
        except TimeoutError:
            log.warning("no advertisements for %.0fs, restarting the scanner", idle_timeout)
        finally:
            try:
                await scanner.stop()
            except Exception as error:  # best effort, e.g. bluetoothd restarted
                log.debug("stopping scanner: %s", error)
