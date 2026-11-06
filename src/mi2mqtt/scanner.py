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

# Passive scanning needs patterns: BlueZ only passes on advertisements that match one of them. A
# pattern on the service data UUID does not work, because BlueZ (e.g. 5.82) compares service data
# patterns with the bytes *after* the UUID, of any service data UUID. `detected` checks the UUID.
OR_PATTERNS = [
    # Flags, as sent by most devices (Home Assistant matches the same values). This catches MiBeacon
    # sensors: LE general discoverable, + BR/EDR not supported, + simultaneous LE and BR/EDR.
    *(OrPattern(0, AdvertisementDataType.FLAGS, flags) for flags in (b"\x02", b"\x06", b"\x1a")),
    # ATC1441 and PVVX firmware send no Flags, only service data 0x181A starting with the sensor's MAC,
    # so match the Telink OUI A4:C1:38 of the LYWSD03MMC: big endian (ATC1441), little endian (PVVX).
    OrPattern(0, AdvertisementDataType.SERVICE_DATA_UUID16, b"\xa4\xc1\x38"),
    OrPattern(3, AdvertisementDataType.SERVICE_DATA_UUID16, b"\x38\xc1\xa4"),
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
