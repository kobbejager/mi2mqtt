"""Configuration file (mi2mqtt.yaml) loading and validation."""

import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
MAC_PATTERN = re.compile(r"^[0-9A-F]{2}(:[0-9A-F]{2}){5}$")


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MqttConfig(Model):
    host: str = "127.0.0.1"
    port: int = 1883
    username: str | None = None
    password: str | None = None
    password_file: Path | None = None
    prefix: str = "mi"
    client_id: str = "mi2mqtt"
    keepalive: int = 60
    qos: Literal[0, 1, 2] = 1

    def resolved_password(self) -> str | None:
        if self.password_file is not None:
            return self.password_file.read_text().strip()
        return self.password


class BleConfig(Model):
    adapter: str | None = None  # e.g. hci0; default: BlueZ's default adapter
    # passive: the sensors are only listened to. active: BlueZ sends scan requests, which the
    # sensors answer (costs battery). Only use active when passive scanning is not available.
    scanning_mode: Literal["passive", "active"] = "passive"


class AvailabilityConfig(Model):
    timeout: int = Field(900, gt=0)  # seconds without a packet before a sensor is reported offline


class DiscoveryConfig(Model):
    enabled: bool = True
    prefix: str = "homeassistant"
    default_entity_ids: bool = True  # needs Home Assistant >= 2025.10; disable on older versions


class DeviceConfig(Model):
    mac: str
    name: str | None = None
    area: str | None = None
    model: str | None = None

    @field_validator("mac")
    @classmethod
    def _mac(cls, value: str) -> str:
        value = value.strip().upper().replace("-", ":")
        if not MAC_PATTERN.match(value):
            raise ValueError(f"{value!r} is not a MAC address (AA:BB:CC:DD:EE:FF)")
        return value


class Config(Model):
    mqtt: MqttConfig = MqttConfig()
    ble: BleConfig = BleConfig()
    availability: AvailabilityConfig = AvailabilityConfig()
    discovery: DiscoveryConfig = DiscoveryConfig()
    devices: dict[str, DeviceConfig] = {}

    @model_validator(mode="after")
    def _check(self) -> "Config":
        macs: dict[str, str] = {}
        for device_id, device in self.devices.items():
            if not ID_PATTERN.match(device_id):
                raise ValueError(f"device id {device_id!r} may only contain letters, digits, '_' and '-'")
            if device.mac in macs:
                raise ValueError(f"MAC {device.mac} is used by both {macs[device.mac]!r} and {device_id!r}")
            macs[device.mac] = device_id
        return self


class _Loader(yaml.SafeLoader):
    """SafeLoader with YAML 1.2 booleans (so `on:`/`off:` stay strings) and an `!env` tag."""


_Loader.yaml_implicit_resolvers = {
    first: [(tag, regexp) for tag, regexp in resolvers if tag != "tag:yaml.org,2002:bool"]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_Loader.add_implicit_resolver(
    "tag:yaml.org,2002:bool", re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"), list("tTfF")
)


def _env(loader: yaml.SafeLoader, node: yaml.Node) -> str:
    name = loader.construct_scalar(node)
    if name not in os.environ:
        raise ValueError(f"environment variable {name} is not set")
    return os.environ[name]


_Loader.add_constructor("!env", _env)


ENV_PREFIX = "MI_MQTT_"


def mqtt_from_env(env: Mapping[str, str]) -> dict[str, str]:
    """MQTT settings from MI_MQTT_<FIELD> variables, e.g. MI_MQTT_HOST."""
    settings = {}
    for field in MqttConfig.model_fields:
        value = env.get(ENV_PREFIX + field.upper())
        if value not in (None, ""):
            settings[field] = value
    return settings


def parse_config(text: str, env: Mapping[str, str] | None = None) -> Config:
    """Parse mi2mqtt.yaml; MI_MQTT_* environment variables override its `mqtt:` section."""
    data = yaml.load(text, Loader=_Loader) or {}
    overrides = mqtt_from_env(os.environ if env is None else env)
    if overrides and isinstance(data, dict):
        data["mqtt"] = {**(data.get("mqtt") or {}), **overrides}
    return Config.model_validate(data)


def load_config(path: str | Path) -> Config:
    return parse_config(Path(path).read_text())


def load_env_file(path: str | Path) -> list[str]:
    """Load KEY=VALUE lines (systemd EnvironmentFile / .env syntax) into os.environ.

    Variables that are already set win, so the real environment overrides the file.
    Returns the names that were set.
    """
    loaded = []
    for number, raw in enumerate(Path(path).read_text().splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = line.removeprefix("export ").strip()
        key, sep, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not sep or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ValueError(f"{path}:{number}: expected KEY=VALUE")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded
