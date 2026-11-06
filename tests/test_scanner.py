import pytest
from bleak.assigned_numbers import AdvertisementDataType
from conftest import atc1441, pvvx

from mi2mqtt.scanner import OR_PATTERNS

# Raw advertisements as received in passive mode (btmon, BlueZ 5.82, ASUS USB-BT500)
MIBEACON = bytes.fromhex("020106151695fe5020aa014848e433342d580d1004c700ef01")  # LYWSDCGQ, with Flags
PVVX = bytes.fromhex("12161a18e5a6e238c1a40a09a811900a381204")  # LYWSD03MMC with PVVX firmware, no Flags
OTHER = bytes.fromhex("0516f1fc0102")  # service data 0xFCF1 of some other device, no Flags


def ad_structures(advertisement: bytes) -> list[tuple[int, bytes]]:
    structures = []
    while advertisement:
        length = advertisement[0]
        structures.append((advertisement[1], advertisement[2 : 1 + length]))
        advertisement = advertisement[1 + length :]
    return structures


def bluez_matches(advertisement: bytes) -> bool:
    """BlueZ's bt_ad_pattern_match (src/shared/ad.c): service data patterns are compared with the
    bytes after the UUID, other patterns with the data of an AD structure of the same type."""
    for pattern in OR_PATTERNS:
        start, content = pattern.start_position, pattern.content_of_pattern
        for ad_type, data in ad_structures(advertisement):
            if ad_type != pattern.ad_data_type:
                continue
            if ad_type == AdvertisementDataType.SERVICE_DATA_UUID16:
                data = data[2:]
            if data[start : start + len(content)] == content:
                return True
    return False


def service_data_16(uuid: int, data: bytes) -> bytes:
    header = bytes([len(data) + 3, AdvertisementDataType.SERVICE_DATA_UUID16])
    return header + uuid.to_bytes(2, "little") + data


@pytest.mark.parametrize(
    "advertisement",
    [
        MIBEACON,
        PVVX,
        service_data_16(0x181A, pvvx(2143, 4820, 87, 1)),
        service_data_16(0x181A, atc1441(214, 48, 87, 1)),
    ],
)
def test_sensor_advertisements_pass_the_passive_filter(advertisement):
    assert bluez_matches(advertisement)


def test_other_service_data_is_filtered():
    assert not bluez_matches(OTHER)
