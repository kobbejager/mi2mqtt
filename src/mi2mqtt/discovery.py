"""Home Assistant MQTT device-based discovery (``<prefix>/device/<object_id>/config``)."""

from importlib.metadata import PackageNotFoundError, version
from typing import Any

from mi2mqtt.config import DeviceConfig

OBJECT_PREFIX = "mi2mqtt_"
LEGACY_OBJECT_PREFIX = "miscan_"  # before the rename to mi2mqtt; always cleared
VALUES = ("temperature", "humidity", "battery", "rssi")

try:
    VERSION = version("mi2mqtt")
except PackageNotFoundError:  # pragma: no cover - running from a source tree
    VERSION = "dev"

ORIGIN = {"name": "mi2mqtt", "sw_version": VERSION}

# The entity name is left out, so Home Assistant names it after the (translated) device class.
SENSORS: dict[str, dict[str, Any]] = {
    "temperature": {
        "device_class": "temperature",
        "unit_of_measurement": "°C",
        "state_class": "measurement",
        "suggested_display_precision": 1,
    },
    "humidity": {
        "device_class": "humidity",
        "unit_of_measurement": "%",
        "state_class": "measurement",
        "suggested_display_precision": 0,
    },
    "battery": {
        "device_class": "battery",
        "unit_of_measurement": "%",
        "state_class": "measurement",
        "entity_category": "diagnostic",
    },
    "rssi": {
        "device_class": "signal_strength",
        "unit_of_measurement": "dBm",
        "state_class": "measurement",
        "entity_category": "diagnostic",
    },
}


def object_id(device: DeviceConfig) -> str:
    return OBJECT_PREFIX + device.mac.replace(":", "").lower()


def config_topic(discovery_prefix: str, object_id_: str) -> str:
    return f"{discovery_prefix}/device/{object_id_}/config"


def object_id_from_topic(discovery_prefix: str, topic: str) -> str | None:
    parts = topic.split("/")
    base = discovery_prefix.split("/")
    if len(parts) == len(base) + 3 and parts[: len(base)] == base and parts[-1] == "config":
        return parts[len(base) + 1]
    return None


def device_payload(
    device_id: str, device: DeviceConfig, prefix: str, qos: int, default_entity_ids: bool = True
) -> dict[str, Any]:
    oid = object_id(device)
    info: dict[str, Any] = {
        "identifiers": [oid],
        "name": device.name or device_id,
        "manufacturer": "Xiaomi",
    }
    if device.model:
        info["model"] = device.model
    if device.area:
        info["suggested_area"] = device.area

    components = {}
    for value, settings in SENSORS.items():
        component = {
            "platform": "sensor",
            "unique_id": f"{oid}_{value}",
            "state_topic": f"{prefix}/{device_id}/{value}",
            **settings,
        }
        if default_entity_ids:
            component["default_entity_id"] = f"sensor.{device_id.lower()}_{value}"
        components[value] = component

    return {
        "device": info,
        "origin": ORIGIN,
        "availability": [
            {"topic": f"{prefix}/status", "payload_available": "online", "payload_not_available": "offline"},
            {
                "topic": f"{prefix}/{device_id}/availability",
                "payload_available": "online",
                "payload_not_available": "offline",
            },
        ],
        "availability_mode": "all",
        "qos": qos,
        "components": components,
    }


def discovery_messages(
    devices: dict[str, DeviceConfig],
    discovery_prefix: str,
    prefix: str,
    qos: int,
    default_entity_ids: bool = True,
) -> dict[str, dict[str, Any]]:
    """All discovery topics and payloads."""
    return {
        config_topic(discovery_prefix, object_id(device)): device_payload(
            device_id, device, prefix, qos, default_entity_ids
        )
        for device_id, device in devices.items()
    }
