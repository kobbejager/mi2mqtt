import argparse
import asyncio
import contextlib
import json
import logging
import signal
import sys
from pathlib import Path

from pydantic import ValidationError

from mi2mqtt.app import App
from mi2mqtt.config import Config, load_config, load_env_file
from mi2mqtt.scanner import ScannerError, scan
from mi2mqtt.survey import Survey


async def _run(app: App) -> None:
    task = asyncio.current_task()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, task.cancel)
    with contextlib.suppress(asyncio.CancelledError):
        await app.run()


async def _survey(config: Config, seconds: float) -> str:
    survey = Survey(config)
    with contextlib.suppress(TimeoutError):
        async with asyncio.timeout(seconds):
            await scan(config.ble, survey.handle_packet)
    return survey.table()


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="mi2mqtt", description="Xiaomi BLE thermometers to MQTT and Home Assistant"
    )
    parser.add_argument("-c", "--config", default="mi2mqtt.yaml", help="configuration file")
    parser.add_argument(
        "--env-file",
        help="file with MI_MQTT_* settings (default: .env in the current directory, if present)",
    )
    parser.add_argument(
        "--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    )
    parser.add_argument("--check-config", action="store_true", help="validate the configuration and exit")
    parser.add_argument(
        "--dump-discovery", action="store_true", help="print Home Assistant discovery payloads and exit"
    )
    parser.add_argument(
        "--scan",
        nargs="?",
        type=float,
        const=30,
        metavar="SECONDS",
        help="list all sensors in range (default 30 seconds) without MQTT, then exit",
    )
    args = parser.parse_args()

    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    env_file = args.env_file or (".env" if Path(".env").exists() else None)
    try:
        if env_file:
            load_env_file(env_file)
    except (OSError, ValueError) as error:
        print(f"env file: {error}", file=sys.stderr)
        sys.exit(1)

    if args.scan is not None and not Path(args.config).exists():
        config = Config()  # scanning works without a configuration
    else:
        try:
            config = load_config(args.config)
        except (OSError, ValueError, ValidationError) as error:
            print(f"{args.config}: {error}", file=sys.stderr)
            sys.exit(1)

    if args.check_config:
        print(f"{args.config}: OK ({len(config.devices)} devices)")
        return

    if args.dump_discovery:
        print(json.dumps(App(config).discovery(), indent=2, ensure_ascii=False))
        return

    try:
        if args.scan is not None:
            print(f"scanning for {args.scan:.0f}s...", file=sys.stderr)
            print(asyncio.run(_survey(config, args.scan)))
        else:
            asyncio.run(_run(App(config)))
    except* ScannerError as group:
        for error in group.exceptions:
            print(error, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
