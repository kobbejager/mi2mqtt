import json
import logging

from conftest import SLAAPKAMER, WOONKAMER, atc1441, drain, mibeacon, pvvx

from mi2mqtt.parsers import UUID_ENVIRONMENTAL, UUID_XIAOMI
from mi2mqtt.survey import Survey


def test_new_measurement_publishes_values(app):
    app.handle_packet(WOONKAMER, -70, UUID_ENVIRONMENTAL, pvvx(2143, 4820, 87, 5))
    assert drain(app) == [
        ("mi/woonkamer/availability", "online"),
        ("mi/woonkamer/temperature", "21.43"),
        ("mi/woonkamer/humidity", "48.2"),
        ("mi/woonkamer/battery", "87"),
        ("mi/woonkamer/rssi", "-70"),
    ]


def test_repeated_advertisement_is_skipped(app):
    app.handle_packet(WOONKAMER, -70, UUID_ENVIRONMENTAL, pvvx(2143, 4820, 87, 5))
    drain(app)
    app.handle_packet(WOONKAMER, -72, UUID_ENVIRONMENTAL, pvvx(2143, 4820, 87, 5))
    assert drain(app) == []
    # same values, new measurement: published (every measurement counts)
    app.handle_packet(WOONKAMER, -71, UUID_ENVIRONMENTAL, pvvx(2143, 4820, 87, 6))
    assert ("mi/woonkamer/temperature", "21.43") in drain(app)


def test_atc1441(app):
    app.handle_packet(WOONKAMER, -70, UUID_ENVIRONMENTAL, atc1441(214, 48, 87, 1))
    assert ("mi/woonkamer/humidity", "48") in drain(app)


def test_partial_mibeacon_packets(app):
    temp_hum = (214).to_bytes(2, "little") + (482).to_bytes(2, "little")
    app.handle_packet(SLAAPKAMER, -60, UUID_XIAOMI, mibeacon(0x100D, temp_hum, 1))
    app.handle_packet(SLAAPKAMER, -60, UUID_XIAOMI, mibeacon(0x100D, temp_hum, 1))
    app.handle_packet(SLAAPKAMER, -61, UUID_XIAOMI, mibeacon(0x100A, bytes([93]), 2))
    assert drain(app) == [
        ("mi/slaapkamer/availability", "online"),
        ("mi/slaapkamer/temperature", "21.4"),
        ("mi/slaapkamer/humidity", "48.2"),
        ("mi/slaapkamer/rssi", "-60"),
        ("mi/slaapkamer/battery", "93"),
        ("mi/slaapkamer/rssi", "-61"),
    ]


def test_unknown_sensor_is_logged_once(app, caplog):
    caplog.set_level(logging.INFO)
    for counter in (1, 2):
        app.handle_packet("A4:C1:38:AA:BB:CC", -80, UUID_ENVIRONMENTAL, pvvx(2000, 5000, 50, counter))
    assert drain(app) == []
    logged = [r.getMessage() for r in caplog.records if "unconfigured" in r.getMessage()]
    assert len(logged) == 1
    assert "A4:C1:38:AA:BB:CC (PVVX, -80 dBm" in logged[0]
    assert "sensor_aabbcc:\n    mac: A4:C1:38:AA:BB:CC" in logged[0]


def test_unparseable_packet_is_ignored(app, caplog):
    caplog.set_level(logging.INFO)
    app.handle_packet("11:22:33:44:55:66", -80, UUID_XIAOMI, mibeacon(0x1007, bytes([1, 2, 3]), 1))
    assert drain(app) == [] and not caplog.records


def test_availability(app, clock):
    app.check_availability()
    assert drain(app) == []  # retained state of the previous run stands until the timeout

    clock.now += 899
    app.handle_packet(WOONKAMER, -70, UUID_ENVIRONMENTAL, pvvx(2143, 4820, 87, 5))
    drain(app)
    clock.now += 1
    app.check_availability()
    assert drain(app) == [("mi/slaapkamer/availability", "offline")]

    clock.now += 900
    app.check_availability()
    app.check_availability()
    assert drain(app) == [("mi/woonkamer/availability", "offline")]

    # back: online again, even for a repeat of the last measurement
    app.handle_packet(WOONKAMER, -70, UUID_ENVIRONMENTAL, pvvx(2143, 4820, 87, 5))
    assert drain(app) == [("mi/woonkamer/availability", "online")]


def test_discovery(app):
    messages = app.discovery()
    payload = messages["homeassistant/device/mi2mqtt_a4c138112233/config"]
    assert payload["device"] == {
        "identifiers": ["mi2mqtt_a4c138112233"],
        "name": "Woonkamer",
        "manufacturer": "Xiaomi",
        "model": "LYWSD03MMC",
        "suggested_area": "Woonkamer",
    }
    assert [a["topic"] for a in payload["availability"]] == ["mi/status", "mi/woonkamer/availability"]
    assert payload["availability_mode"] == "all"
    temperature = payload["components"]["temperature"]
    assert temperature["state_topic"] == "mi/woonkamer/temperature"
    assert temperature["unique_id"] == "mi2mqtt_a4c138112233_temperature"
    assert temperature["default_entity_id"] == "sensor.woonkamer_temperature"
    assert set(payload["components"]) == {"temperature", "humidity", "battery", "rssi"}
    assert messages["homeassistant/device/mi2mqtt_582d34445566/config"]["device"]["name"] == "slaapkamer"


def test_publish_all_clears_stale_topics(app):
    for topic, payload in [
        ("mi", b"online"),  # v1 bridge status
        ("mi/woonkamer", b"{}"),  # v1 JSON
        ("mi/woonkamer/timestamp", b"2026-01-01T00:00:00"),
        ("mi/a4:c1:38:aa:bb:cc/temperature", b"20.1"),  # v1 unconfigured device
        ("mi/woonkamer/temperature", b"21.4"),  # still used
        ("mi/old/battery", b""),  # already cleared
        ("homeassistant/device/mi2mqtt_aabbccddeeff/config", b"{}"),  # removed device
        ("homeassistant/device/mi2mqtt_a4c138112233/config", b"{}"),  # still configured
        ("homeassistant/device/miscan_a4c138112233/config", b"{}"),  # from before the rename
        ("homeassistant/device/elektro_x/config", b"{}"),  # not ours
    ]:
        app.apply_retained(topic, payload)
    app.publish_all()
    messages = drain(app)
    cleared = [topic for topic, payload in messages if payload == ""]
    assert cleared == [
        "homeassistant/device/mi2mqtt_aabbccddeeff/config",
        "homeassistant/device/miscan_a4c138112233/config",
        "mi",
        "mi/a4:c1:38:aa:bb:cc/temperature",
        "mi/woonkamer",
        "mi/woonkamer/timestamp",
    ]
    assert messages[-1] == ("mi/status", "online")
    json.loads(messages[0][1])

    app.publish_all()  # cleared only once
    assert not [topic for topic, payload in drain(app) if payload == ""]


def test_home_assistant_birth_republishes(app):
    app.handle_message("homeassistant/status", b"offline")
    assert drain(app) == []
    app.handle_message("homeassistant/status", b"online")
    assert len([t for t, _ in drain(app) if t.startswith("homeassistant/device/")]) == 2


def test_survey(app):
    survey = Survey(app.config)
    survey.handle_packet(WOONKAMER, -70, UUID_ENVIRONMENTAL, pvvx(2143, 4820, 87, 5))
    survey.handle_packet(WOONKAMER, -70, UUID_ENVIRONMENTAL, pvvx(2143, 4820, 87, 5))
    survey.handle_packet("A4:C1:38:AA:BB:CC", -50, UUID_ENVIRONMENTAL, atc1441(199, 40, 60, 1))
    lines = survey.table().splitlines()
    assert lines[0].split() == ["MAC", "id", "format", "RSSI", "temp", "hum", "batt", "measurements"]
    assert lines[1].split() == ["A4:C1:38:AA:BB:CC", "-", "ATC1441", "-50", "19.9", "40", "60", "1"]
    assert lines[2].split() == [WOONKAMER, "woonkamer", "PVVX", "-70", "21.43", "48.2", "87", "1"]
