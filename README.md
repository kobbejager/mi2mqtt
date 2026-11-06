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

mi2mqtt is a normal Python package (Python ≥ 3.12). Installing it into a virtual environment gives you a
`mi2mqtt` command at `.venv/bin/mi2mqtt`. Its dependencies are `bleak`, `aiomqtt`, `pydantic` and `PyYAML`.
Bleak talks to BlueZ (`bluetoothd`) over D-Bus with the pure Python package `dbus-fast`. No system
packages are needed beyond BlueZ itself.

The exact dependency versions that were tested are pinned in `uv.lock`. Option A uses that file
automatically. With option B you install from `pyproject.toml`, which only gives minimum versions, so
you may get newer releases.

### Option A: `uv venv` + `uv pip` (classic workflow)

```sh
cd mi2mqtt
uv venv                          # creates .venv/
uv pip install -e .              # installs mi2mqtt (editable) and its dependencies
uv pip install --group dev       # optional: pytest and ruff, for development
```

Then either activate the environment or call the binaries directly:

```sh
source .venv/bin/activate
mi2mqtt --help
# or, without activating:
.venv/bin/mi2mqtt --help
```

To use the tested versions from `uv.lock` with this workflow:

```sh
uv export --frozen --no-dev --no-emit-project -o requirements.txt
uv pip install -r requirements.txt
uv pip install --no-deps .
```

### Option B: `uv sync` (project workflow)

```sh
cd mi2mqtt
uv sync                          # add --no-dev on a production machine
```

`uv sync` creates `.venv/`, installs exactly the versions in `uv.lock` plus the dev tools, and installs
mi2mqtt itself in editable mode. `uv run mi2mqtt --help` runs a command in `.venv/` without activating it.

The rest of this README writes plain `mi2mqtt ...`. Activate `.venv` first, or put `.venv/bin/` or
`uv run` in front.

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

`ble.scanning_mode: active` works without this. In active mode, however, BlueZ sends a scan request for
every advertisement and the sensor answers it, which costs battery. The same happens when another program
(for example the Home Assistant Bluetooth integration) scans actively with the same adapter.

## Configuration

See [`mi2mqtt.example.yaml`](mi2mqtt.example.yaml) for every option.

### MQTT connection: `.env`

The broker host, port, username and password are not in `mi2mqtt.yaml`. They come from environment
variables, normally kept in a `.env` file. `.env` is in `.gitignore`, so it is never committed.

```sh
cp .env.example .env
chmod 600 .env
$EDITOR .env                                 # fill in MI_MQTT_HOST, _USERNAME, _PASSWORD
```

- **Where it looks:** mi2mqtt reads `.env` from the current directory. Use `--env-file PATH` to read
  another file.
- **Precedence:** a variable that is already set in the environment wins over the same one in the file.
- **Which settings:** every `mqtt:` setting can be set as `MI_MQTT_<KEY>`, and that overrides
  `mi2mqtt.yaml`.
- **Format:** plain `KEY=value` lines, the same format systemd's `EnvironmentFile=` uses.

### Adding a sensor

```sh
mi2mqtt --scan                               # listen for 30 seconds; or --scan 120
```

This lists every sensor in range, configured or not, with the strongest signal first. It doesn't need
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

### Checking the configuration

```sh
mi2mqtt -c mi2mqtt.yaml --check-config       # validate the configuration
mi2mqtt -c mi2mqtt.yaml --dump-discovery     # print what Home Assistant will receive
```

## Running

```sh
mi2mqtt -c mi2mqtt.yaml                      # reads .env; add --log-level DEBUG to see every packet
```

Stop it with Ctrl+C. mi2mqtt publishes `offline` on `<prefix>/status` before exiting.

### As a systemd service

The unit file [`deploy/mi2mqtt.service`](deploy/mi2mqtt.service) expects:

| what                       | where                                     |
|----------------------------|-------------------------------------------|
| the code and its `.venv/`  | `/opt/mi2mqtt`                            |
| the configuration          | `/etc/mi2mqtt/mi2mqtt.yaml`               |
| the MQTT settings (`.env`) | `/etc/mi2mqtt/env`                        |
| the service account        | user `mi2mqtt`, also in group `bluetooth` |

The `bluetooth` group gives access to BlueZ over D-Bus (this is the policy on Debian and Raspberry Pi OS;
check `/usr/share/dbus-1/system.d/bluetooth.conf` on other systems). Every step:

```sh
# 1. service account and code
sudo useradd --system --home-dir /opt/mi2mqtt --shell /usr/sbin/nologin --groups bluetooth mi2mqtt
sudo git clone <repo-url> /opt/mi2mqtt
sudo chown -R mi2mqtt: /opt/mi2mqtt

# 2. virtual environment, as the mi2mqtt user (option A shown; option B: uv sync --frozen --no-dev)
cd /opt/mi2mqtt
sudo -u mi2mqtt uv venv
sudo -u mi2mqtt uv export --frozen --no-dev --no-emit-project -o requirements.txt
sudo -u mi2mqtt uv pip install -r requirements.txt
sudo -u mi2mqtt uv pip install --no-deps .
sudo -u mi2mqtt /opt/mi2mqtt/.venv/bin/mi2mqtt --scan  # check: Bluetooth access and passive scanning

# 3. configuration and MQTT settings
sudo install -d /etc/mi2mqtt
sudo cp mi2mqtt.example.yaml /etc/mi2mqtt/mi2mqtt.yaml
sudoedit /etc/mi2mqtt/mi2mqtt.yaml                 # your sensors
sudo install -m 600 .env.example /etc/mi2mqtt/env
sudoedit /etc/mi2mqtt/env                          # fill in host, username, password
sudo /opt/mi2mqtt/.venv/bin/mi2mqtt -c /etc/mi2mqtt/mi2mqtt.yaml --env-file /etc/mi2mqtt/env --check-config

# 4. service
sudo cp deploy/mi2mqtt.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now mi2mqtt
journalctl -u mi2mqtt -f                           # follow the log
```

To update:

```sh
cd /opt/mi2mqtt && sudo -u mi2mqtt git pull
sudo -u mi2mqtt uv pip install --no-deps .         # option B: sudo -u mi2mqtt uv sync --frozen --no-dev
sudo systemctl restart mi2mqtt
```

If `uv.lock` changed, first rerun the `uv export` and `uv pip install -r requirements.txt` lines from
step 2.

## Migrating from miscan

This project used to be called miscan. Version 2 of miscan and mi2mqtt are the same code, but the
names changed:

| what                      | miscan                             | mi2mqtt                         |
|---------------------------|------------------------------------|---------------------------------|
| command and service       | `miscan`, `miscan.service`         | `mi2mqtt`, `mi2mqtt.service`    |
| configuration file        | `/etc/miscan/miscan.yaml`          | `/etc/mi2mqtt/mi2mqtt.yaml`     |
| MQTT settings             | `/etc/miscan/env`, `MISCAN_MQTT_*` | `/etc/mi2mqtt/env`, `MI_MQTT_*` |
| default topic prefix      | `miscan`                           | `mi`                            |
| default client id         | `miscan`                           | `mi2mqtt`                       |
| Home Assistant object ids | `miscan_<mac>`                     | `mi2mqtt_<mac>`                 |

1. Stop the old service: `sudo systemctl disable --now miscan`, and remove
   `/etc/systemd/system/miscan.service`.
2. Install mi2mqtt as described above. Copy `/etc/miscan/miscan.yaml` to `/etc/mi2mqtt/mi2mqtt.yaml` and
   `/etc/miscan/env` to `/etc/mi2mqtt/env`, and rename the `MISCAN_MQTT_` variables to `MI_MQTT_`. Remove
   `mqtt.prefix` and `mqtt.client_id` if they were set to `miscan`, or keep `prefix: miscan` to keep the
   old topics.
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

Install with the dev tools (`uv pip install -e . && uv pip install --group dev`, or `uv sync`). Then:

```sh
pytest                                   # or: uv run pytest
ruff check . && ruff format --check .
```

Dependencies are declared in `pyproject.toml`. Add one with `uv add <package>` (or `uv add --dev
<package>`), which also updates `uv.lock`. If you only edit `pyproject.toml` by hand, run `uv lock`
afterwards.

The packet parsers (`parsers.py`) and the application logic (`app.py`) are tested without Bluetooth or
MQTT. The tests build packets in each format and use a fake clock for availability.
