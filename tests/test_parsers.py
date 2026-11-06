from conftest import atc1441, mibeacon, pvvx

from mi2mqtt.parsers import UUID_ENVIRONMENTAL, UUID_XIAOMI, Reading, parse


def test_pvvx():
    assert parse(UUID_ENVIRONMENTAL, pvvx(2143, 4820, 87, 5)) == Reading("PVVX", 5, 21.43, 48.2, 87)


def test_pvvx_negative_temperature():
    assert parse(UUID_ENVIRONMENTAL, pvvx(-512, 9000, 100, 0)).temperature == -5.12


def test_atc1441():
    assert parse(UUID_ENVIRONMENTAL, atc1441(214, 48, 87, 9)) == Reading("ATC1441", 9, 21.4, 48, 87)


def test_atc1441_negative_temperature():
    assert parse(UUID_ENVIRONMENTAL, atc1441(-35, 48, 87, 9)).temperature == -3.5


def test_custom_unknown_length():
    assert parse(UUID_ENVIRONMENTAL, b"\x00" * 10) is None


def test_mibeacon_temperature_humidity():
    data = mibeacon(0x100D, (214).to_bytes(2, "little") + (482).to_bytes(2, "little"), 7)
    assert parse(UUID_XIAOMI, data) == Reading("MiBeacon", 7, temperature=21.4, humidity=48.2)


def test_mibeacon_single_values():
    assert parse(UUID_XIAOMI, mibeacon(0x1004, (-12).to_bytes(2, "little", signed=True), 1)) == Reading(
        "MiBeacon", 1, temperature=-1.2
    )
    assert parse(UUID_XIAOMI, mibeacon(0x1006, (555).to_bytes(2, "little"), 2)) == Reading(
        "MiBeacon", 2, humidity=55.5
    )
    assert parse(UUID_XIAOMI, mibeacon(0x100A, bytes([93]), 3)) == Reading("MiBeacon", 3, battery=93)


def test_mibeacon_with_capability():
    data = mibeacon(0x100A, bytes([93]), 3, control=0x2070)
    data = data[:11] + b"\x08" + data[11:]
    assert parse(UUID_XIAOMI, data) == Reading("MiBeacon", 3, battery=93)


def test_mibeacon_ignored():
    assert parse(UUID_XIAOMI, mibeacon(0x100A, bytes([93]), 3, control=0x2058)) is None  # encrypted
    assert parse(UUID_XIAOMI, mibeacon(0x1007, bytes([1, 2, 3]), 3)) is None  # illuminance: other device
    assert parse(UUID_XIAOMI, mibeacon(0x100A, bytes([93]), 3)[:-1]) is None  # truncated
    assert parse(UUID_XIAOMI, mibeacon(0x100A, b"", 3, control=0x2010)[:11]) is None  # no object


def test_other_uuid():
    assert parse("0000180f-0000-1000-8000-00805f9b34fb", pvvx(2143, 4820, 87, 5)) is None
