"""Decoding of the BLE service data broadcast by Xiaomi thermometers.

`data` is the service data without the 16-bit UUID, as Bleak reports it.

- ATC1441 and PVVX custom firmware, UUID 0x181A:
  https://github.com/pvvx/ATC_MiThermometer#custom-format-all-data-little-endian
- Xiaomi MiBeacon (stock LYWSDCGQ), UUID 0xFE95:
  https://github.com/pvvx/ATC_MiThermometer/blob/master/InfoMijiaBLE/Mijia%20BLE%20Broadcasting%20Protocol-Mi%20Beacon%20v4.md
"""

from dataclasses import dataclass

UUID_ENVIRONMENTAL = "0000181a-0000-1000-8000-00805f9b34fb"
UUID_XIAOMI = "0000fe95-0000-1000-8000-00805f9b34fb"
UUIDS = (UUID_ENVIRONMENTAL, UUID_XIAOMI)

MODELS = {"PVVX": "LYWSD03MMC", "ATC1441": "LYWSD03MMC", "MiBeacon": "LYWSDCGQ"}

# MiBeacon frame control bits
_ENCRYPTED = 0x0008
_MAC_INCLUDED = 0x0010
_CAPABILITY_INCLUDED = 0x0020
_OBJECT_INCLUDED = 0x0040
_IO_CAPABILITY = 0x20  # in the capability byte: two extra I/O capability bytes follow


@dataclass(frozen=True)
class Reading:
    format: str
    counter: int  # changes with every new measurement; repeated broadcasts carry the same value
    temperature: float | None = None  # °C
    humidity: float | None = None  # %
    battery: int | None = None  # %


def parse(uuid: str, data: bytes) -> Reading | None:
    if uuid == UUID_ENVIRONMENTAL:
        return _parse_custom(data)
    if uuid == UUID_XIAOMI:
        return _parse_mibeacon(data)
    return None


def _parse_custom(data: bytes) -> Reading | None:
    if len(data) == 15:  # PVVX: little endian, 0.01 °C and 0.01 %
        return Reading(
            format="PVVX",
            counter=data[13],
            temperature=int.from_bytes(data[6:8], "little", signed=True) / 100,
            humidity=int.from_bytes(data[8:10], "little") / 100,
            battery=data[12],
        )
    if len(data) == 13:  # ATC1441: big endian, 0.1 °C and 1 %
        return Reading(
            format="ATC1441",
            counter=data[12],
            temperature=int.from_bytes(data[6:8], "big", signed=True) / 10,
            humidity=data[8],
            battery=data[9],
        )
    return None


def _parse_mibeacon(data: bytes) -> Reading | None:
    if len(data) < 5:
        return None
    control = int.from_bytes(data[0:2], "little")
    if control & _ENCRYPTED or not control & _OBJECT_INCLUDED:
        return None
    counter = data[4]
    position = 5
    if control & _MAC_INCLUDED:
        position += 6
    if control & _CAPABILITY_INCLUDED:
        if len(data) <= position:
            return None
        capability = data[position]
        position += 3 if capability & _IO_CAPABILITY else 1
    if len(data) < position + 3:
        return None
    object_id = int.from_bytes(data[position : position + 2], "little")
    size = data[position + 2]
    value = data[position + 3 : position + 3 + size]
    if len(value) != size:
        return None

    def word(offset: int) -> float:
        return int.from_bytes(value[offset : offset + 2], "little", signed=True) / 10

    if object_id == 0x1004 and size == 2:
        return Reading("MiBeacon", counter, temperature=word(0))
    if object_id == 0x1006 and size == 2:
        return Reading("MiBeacon", counter, humidity=word(0))
    if object_id == 0x100A and size == 1:
        return Reading("MiBeacon", counter, battery=value[0])
    if object_id == 0x100D and size == 4:
        return Reading("MiBeacon", counter, temperature=word(0), humidity=word(2))
    return None
