import pytest
from conftest import CONFIG
from pydantic import ValidationError

from mi2mqtt.config import load_env_file, parse_config


def test_example_config():
    config = parse_config(CONFIG, env={})
    assert config.devices["woonkamer"].mac == "A4:C1:38:11:22:33"
    assert config.ble.scanning_mode == "passive"


def test_env_overrides_mqtt():
    config = parse_config(CONFIG, env={"MI_MQTT_HOST": "broker", "MI_MQTT_PORT": "8883"})
    assert (config.mqtt.host, config.mqtt.port, config.mqtt.prefix) == ("broker", 8883, "mi")


def test_example_file_is_valid():
    with open("mi2mqtt.example.yaml") as file:
        parse_config(file.read(), env={})


@pytest.mark.parametrize(
    "devices",
    [
        "a: {mac: 'A4:C1:38:11:22'}",
        "a: {mac: 'A4:C1:38:11:22:33'}\n  b: {mac: 'a4-c1-38-11-22-33'}",
        "'bad id': {mac: 'A4:C1:38:11:22:33'}",
        "a: {mac: 'A4:C1:38:11:22:33', colour: red}",
    ],
)
def test_invalid_devices(devices):
    with pytest.raises(ValidationError):
        parse_config(f"devices:\n  {devices}\n", env={})


def test_env_file(tmp_path, monkeypatch):
    monkeypatch.delenv("MI_MQTT_HOST", raising=False)
    monkeypatch.setenv("MI_MQTT_PORT", "1")
    path = tmp_path / "env"
    path.write_text("# comment\nMI_MQTT_HOST='broker'\nexport MI_MQTT_PORT=2\n")
    assert load_env_file(path) == ["MI_MQTT_HOST"]
    import os

    assert os.environ["MI_MQTT_HOST"] == "broker"
    assert os.environ["MI_MQTT_PORT"] == "1"
    monkeypatch.delenv("MI_MQTT_HOST")
