# mi2mqtt

mi2mqtt listens to Xiaomi Bluetooth (BLE) thermometers and publishes their readings on MQTT. It also
announces every sensor to Home Assistant through MQTT discovery.

It never connects to a sensor. The sensors broadcast their readings in BLE advertisements, and mi2mqtt
only listens (passive scanning). That is what keeps the battery life long. Supported broadcast formats:

| format   | sensor                                        | service data | readings                                             |
|----------|-----------------------------------------------|--------------|------------------------------------------------------|
| PVVX     | LYWSD03MMC with [pvvx custom firmware][fw]    | `0x181A`     | temperature, humidity, battery in one packet         |
| ATC1441  | LYWSD03MMC with ATC firmware (atc1441 format) | `0x181A`     | temperature, humidity, battery in one packet         |
| MiBeacon | LYWSDCGQ (round, stock firmware)              | `0xFE95`     | temperature+humidity and battery in separate packets |

The stock LYWSD03MMC encrypts its broadcasts and needs the custom firmware. Encrypted MiBeacon packets
are ignored.

Version 1 (bluepy, JSON configuration, JSON payloads) is on the `v1` branch (tag `v1-final`).

[fw]: https://github.com/pvvx/ATC_MiThermometer

## How it works

- **Only configured sensors are published.** Sensors that are not in `mi2mqtt.yaml` are logged once per run,
  with a configuration snippet (see [Adding a sensor](#adding-a-sensor)).
- **Every value has its own topic.** A packet publishes the values it contains, plus its RSSI. The
  LYWSDCGQ sends its battery level in a separate packet, so that packet only publishes `battery` and `rssi`.
- **Every measurement is published, once.** A sensor repeats each measurement in many advertisements. All
  formats carry a counter that changes with every new measurement. mi2mqtt skips packets with the same
  counter as the previous one. A new measurement is published even when the values have not changed, so
  long-term graphs (VictoriaMetrics, Grafana) have no gaps.
- **Everything is retained**, so a subscriber that connects gets the latest values immediately.
- **Silent sensors are reported.** When a sensor has sent nothing for `availability.timeout` seconds
  (default 15 minutes), mi2mqtt publishes `offline` on its availability topic, for example when the battery
  is dead. Home Assistant then shows the sensor as unavailable. The next packet sets it back to `online`.

## MQTT topics

`<prefix>` is `mqtt.prefix` (default `mi`). `<id>` is the device id from `mi2mqtt.yaml`.

| topic                                       | retained | payload                          |
|---------------------------------------------|----------|----------------------------------|
| `<prefix>/status`                           | yes      | `online` / `offline` (last will) |
| `<prefix>/<id>/temperature`                 | yes      | `21.43` (°C)                     |
| `<prefix>/<id>/humidity`                    | yes      | `48.2` (%)                       |
| `<prefix>/<id>/battery`                     | yes      | `87` (%)                         |
| `<prefix>/<id>/rssi`                        | yes      | `-71` (dBm)                      |
| `<prefix>/<id>/availability`                | yes      | `online` / `offline` (see above) |
| `homeassistant/device/mi2mqtt_<mac>/config` | yes      | Home Assistant device discovery  |

In Home Assistant, every sensor is a device with four entities: temperature, humidity, battery and signal
strength (the last two as diagnostics). An entity is available when both `<prefix>/status` and
`<prefix>/<id>/availability` are `online`. The unique ids are based on the MAC address, so renaming a
device id or name in `mi2mqtt.yaml` does not create a new device in Home Assistant.

At startup mi2mqtt clears every retained topic under `<prefix>/` that it no longer publishes, and the
discovery of sensors that were removed from the configuration. This also removes what version 1 left
behind. mi2mqtt republishes discovery when Home Assistant restarts (`homeassistant/status` = `online`).

## Installation

mi2mqtt runs as a systemd service. Everything lives in one directory: the code, the `.venv/`,
`mi2mqtt.yaml` and `.env`. The examples below use `/srv/scripts/mi2mqtt`. It needs Python ≥ 3.12,
[uv](https://docs.astral.sh/uv/) and BlueZ (`bluetoothd`); no other system packages.

```sh
cd /srv/scripts
git clone https://github.com/kobbejager/mi2mqtt.git
cd mi2mqtt
uv sync --frozen --no-dev                # creates .venv/ with the versions from uv.lock

cp mi2mqtt.example.yaml mi2mqtt.yaml
$EDITOR mi2mqtt.yaml                     # your sensors, see "Adding a sensor"
cp .env.example .env
chmod 600 .env
$EDITOR .env                             # fill in MI_MQTT_HOST, _USERNAME, _PASSWORD
.venv/bin/mi2mqtt --check-config

$EDITOR deploy/mi2mqtt.service           # adapt User, Group and the paths, see below
sudo cp deploy/mi2mqtt.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now mi2mqtt
journalctl -u mi2mqtt -f                 # follow the log
```

> [!IMPORTANT]
> [`deploy/mi2mqtt.service`](deploy/mi2mqtt.service) is an example from my own setup. Adapt it to your
> system before you install it:
>
> - `User=` and `Group=`: the account that runs mi2mqtt. It must be able to read the install directory,
>   including `.env`.
> - `WorkingDirectory=` and `ExecStart=`: the directory you cloned into. mi2mqtt reads `mi2mqtt.yaml` and
>   `.env` from its working directory.
> - `ProtectHome=true` hides `/home`, `/root` and `/run/user` from the service. If you install under a home
>   directory, change it to `ProtectHome=read-only`.
>
> `SupplementaryGroups=bluetooth` gives the service access to BlueZ over D-Bus. That is the policy on
> Debian and Raspberry Pi OS; check `/usr/share/dbus-1/system.d/bluetooth.conf` on other systems.

`mi2mqtt.yaml` and `.env` are in `.gitignore`, so `git pull` never touches them. To update:

```sh
cd /srv/scripts/mi2mqtt
git pull
uv sync --frozen --no-dev
sudo systemctl restart mi2mqtt
```

If `deploy/mi2mqtt.service` changed, merge the changes into your copy in `/etc/systemd/system/` and run
`sudo systemctl daemon-reload` first.

### Passive scanning

mi2mqtt scans passively by default. BlueZ only allows that through its AdvertisementMonitor API, which
needs `bluetoothd` with experimental features enabled (and Linux ≥ 5.10). Without it mi2mqtt stops with
`cannot start passive scanning`. Enable it in `/etc/bluetooth/main.conf`:

```ini
[General]
Experimental = true
```

or start `bluetoothd` with `--experimental` (`sudo systemctl edit bluetooth`; see `systemctl cat bluetooth`
for the `ExecStart=` line to copy). Then `sudo systemctl restart bluetooth`.

In passive mode BlueZ only passes on advertisements that match a filter. ATC1441 and PVVX firmware
advertise nothing but their service data, so mi2mqtt recognises them by the MAC prefix `A4:C1:38` of the
LYWSD03MMC in that data. A sensor whose MAC was changed in the firmware is only seen in active mode.

`ble.scanning_mode: active` works without the experimental features. In active mode, however, BlueZ sends a scan request for
every advertisement and the sensor answers it, which costs battery. The same happens when another program
(for example the Home Assistant Bluetooth integration) scans actively with the same adapter.

## Configuration

mi2mqtt reads `mi2mqtt.yaml` and `.env` from the current directory, which for the service is its
`WorkingDirectory`. Use `-c PATH` and `--env-file PATH` to read other files.

- `mi2mqtt.yaml` holds the sensors. See [`mi2mqtt.example.yaml`](mi2mqtt.example.yaml) for every option.
- `.env` holds the broker host, port, username and password. It is not committed. Every `mqtt:` setting can
  be set here as `MI_MQTT_<KEY>`, which overrides `mi2mqtt.yaml`. A variable already set in the
  environment wins over the file.

Useful commands:

```sh
.venv/bin/mi2mqtt --scan                 # list the sensors in range (30 s; or --scan 120)
.venv/bin/mi2mqtt --check-config         # validate the configuration
.venv/bin/mi2mqtt --dump-discovery       # print what Home Assistant will receive
.venv/bin/mi2mqtt --log-level DEBUG      # run by hand and show every packet
```

When run by hand, stop it with Ctrl+C. mi2mqtt publishes `offline` on `<prefix>/status` before exiting.

### Adding a sensor

`--scan` lists every sensor in range, configured or not, with the strongest signal first. It doesn't need
MQTT or a configuration file:

```
MAC                id          format    RSSI  temp   hum   batt  measurements
A4:C1:38:AA:BB:CC  -           PVVX      -48   21.43  48.2  87    3
58:2D:34:44:55:66  slaapkamer  MiBeacon  -77   19.4   55.1  93    2
```

To find out which one is the new sensor, hold it next to the receiver (strongest RSSI), or breathe on it
(humidity rises). While mi2mqtt is running, unconfigured sensors also show up in the log, once per MAC,
with a snippet to paste into `mi2mqtt.yaml`:

```sh
journalctl -u mi2mqtt | grep -A4 unconfigured
```

## Migrating from miscan

This project used to be called miscan. Version 2 of miscan and mi2mqtt are the same code, but the
names changed:

| what                      | miscan                             | mi2mqtt                           |
|---------------------------|------------------------------------|-----------------------------------|
| command and service       | `miscan`, `miscan.service`         | `mi2mqtt`, `mi2mqtt.service`      |
| configuration file        | `/etc/miscan/miscan.yaml`          | `mi2mqtt.yaml` (install dir)      |
| MQTT settings             | `/etc/miscan/env`, `MISCAN_MQTT_*` | `.env` (install dir), `MI_MQTT_*` |
| default topic prefix      | `miscan`                           | `mi`                              |
| default client id         | `miscan`                           | `mi2mqtt`                         |
| Home Assistant object ids | `miscan_<mac>`                     | `mi2mqtt_<mac>`                   |

1. Stop the old service: `sudo systemctl disable --now miscan`, and remove
   `/etc/systemd/system/miscan.service`.
2. Install mi2mqtt as described above, but copy `/etc/miscan/miscan.yaml` to `mi2mqtt.yaml` and
   `/etc/miscan/env` to `.env` in the install directory instead of the examples. Rename the
   `MISCAN_MQTT_` variables to `MI_MQTT_`. Remove `mqtt.prefix` and `mqtt.client_id` if they were set to
   `miscan`, or keep `prefix: miscan` to keep the old topics.
3. Start mi2mqtt. It removes the `miscan_<mac>` discovery, so Home Assistant deletes the old devices and
   creates new ones. Entity ids, custom names and history of the old entities do not carry over.
4. With a new prefix, the old retained `miscan/...` topics stay on the broker. Clear them with
   `mosquitto_sub -h <broker> -u <user> -P <password> -t 'miscan/#' --remove-retained -W 2`.

## Migrating from v1

1. Stop v1. Both versions cannot share the Bluetooth adapter reliably: bluepy bypasses `bluetoothd`.
2. Convert the `devices` section of the v1 JSON configuration: `"a4:c1:38:11:22:33": "woonkamer"` becomes
   `woonkamer:` with `mac: A4:C1:38:11:22:33`. Keep the same names as ids, and set `mqtt.prefix: miscan`,
   so the `miscan/<name>/temperature`, `humidity` and `battery` topics stay the same.
3. Start mi2mqtt. It clears the retained v1 leftovers under its prefix: `miscan` (the old status topic),
   `miscan/<name>` (JSON), `miscan/<name>/timestamp` and the topics of unconfigured sensors.

Differences that subscribers may notice: there is no JSON payload any more, the bridge status moved from
`miscan` to `miscan/status`, and messages are always retained. With the default prefix `mi` instead, all
topics move to `mi/...`; clear the v1 topics as in step 4 above.

## Development

```sh
uv sync                                  # installs mi2mqtt (editable) plus pytest and ruff
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

Add a dependency with `uv add <package>` (or `uv add --dev <package>`), which also updates `uv.lock`.

The packet parsers (`parsers.py`) and the application logic (`app.py`) are tested without Bluetooth or
MQTT. The tests build packets in each format and use a fake clock for availability.
