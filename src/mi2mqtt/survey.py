"""`mi2mqtt --scan`: list every sensor in range, to find the MAC of a new one."""

from dataclasses import dataclass, field

from mi2mqtt.config import Config
from mi2mqtt.parsers import parse


@dataclass
class Seen:
    format: str
    rssi: int
    temperature: float | None = None
    humidity: float | None = None
    battery: int | None = None
    counters: set[int] = field(default_factory=set)


class Survey:
    def __init__(self, config: Config):
        self.ids = {device.mac: device_id for device_id, device in config.devices.items()}
        self.seen: dict[str, Seen] = {}

    def handle_packet(self, mac: str, rssi: int, uuid: str, data: bytes) -> None:
        reading = parse(uuid, data)
        if reading is None:
            return
        seen = self.seen.setdefault(mac, Seen(reading.format, rssi))
        seen.format, seen.rssi = reading.format, rssi
        seen.counters.add(reading.counter)
        for name in ("temperature", "humidity", "battery"):
            if (value := getattr(reading, name)) is not None:
                setattr(seen, name, value)

    def table(self) -> str:
        if not self.seen:
            return "no sensors found"
        rows = [("MAC", "id", "format", "RSSI", "temp", "hum", "batt", "measurements")]
        for mac, seen in sorted(self.seen.items(), key=lambda item: -item[1].rssi):
            rows.append(
                (
                    mac,
                    self.ids.get(mac, "-"),
                    seen.format,
                    str(seen.rssi),
                    _show(seen.temperature),
                    _show(seen.humidity),
                    _show(seen.battery),
                    str(len(seen.counters)),
                )
            )
        widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
        return "\n".join(
            "  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True)).rstrip()
            for row in rows
        )


def _show(value: float | None) -> str:
    return "-" if value is None else str(value)
