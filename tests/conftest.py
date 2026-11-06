import pytest

from mi2mqtt.app import App
from mi2mqtt.config import parse_config
from mi2mqtt.parsers import UUID_ENVIRONMENTAL, UUID_XIAOMI

CONFIG = """
mqtt:
  prefix: mi
availability:
  timeout: 900
devices:
  woonkamer:
    mac: a4:c1:38:11:22:33
    name: Woonkamer
    area: Woonkamer
    model: LYWSD03MMC
  slaapkamer:
    mac: 58:2D:34:44:55:66
"""

WOONKAMER = "A4:C1:38:11:22:33"
SLAAPKAMER = "58:2D:34:44:55:66"


def pvvx(temperature: int, humidity: int, battery: int, counter: int) -> bytes:
    """PVVX custom format: temperature and humidity in 0.01 units."""
    return (
        bytes.fromhex("33221138c1a4")
        + temperature.to_bytes(2, "little", signed=True)
        + humidity.to_bytes(2, "little")
        + (2950).to_bytes(2, "little")
        + bytes([battery, counter, 0x05])
    )


def atc1441(temperature: int, humidity: int, battery: int, counter: int) -> bytes:
    """ATC1441 format: temperature in 0.1 °C, big endian."""
    return (
        bytes.fromhex("a4c138112233")
        + temperature.to_bytes(2, "big", signed=True)
        + bytes([humidity, battery])
        + (2950).to_bytes(2, "big")
        + bytes([counter])
    )


def mibeacon(object_id: int, value: bytes, counter: int, control: int = 0x2050) -> bytes:
    """MiBeacon v2/v3 frame with MAC and object, as sent by a LYWSDCGQ."""
    return (
        control.to_bytes(2, "little")
        + (0x01AA).to_bytes(2, "little")
        + bytes([counter])
        + bytes.fromhex("665544342d58")
        + object_id.to_bytes(2, "little")
        + bytes([len(value)])
        + value
    )


@pytest.fixture
def clock():
    class Clock:
        now = 1000.0

        def __call__(self) -> float:
            return self.now

    return Clock()


@pytest.fixture
def app(clock):
    return App(parse_config(CONFIG, env={}), clock=clock)


def drain(app: App) -> list[tuple[str, str]]:
    messages = []
    while not app.queue.empty():
        messages.append(app.queue.get_nowait())
    return messages


__all__ = ["UUID_ENVIRONMENTAL", "UUID_XIAOMI"]
