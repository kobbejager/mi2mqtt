"""Application wiring: BLE packets to MQTT, availability, Home Assistant discovery."""

import asyncio
import json
import logging
import secrets
import time
from collections.abc import Callable
from typing import Any

import aiomqtt

from mi2mqtt.config import Config
from mi2mqtt.discovery import (
    LEGACY_OBJECT_PREFIX,
    OBJECT_PREFIX,
    VALUES,
    discovery_messages,
    object_id,
    object_id_from_topic,
)
from mi2mqtt.parsers import MODELS, Reading, parse
from mi2mqtt.scanner import scan

log = logging.getLogger("mi2mqtt")

RESTORE_TIMEOUT = 5.0
RECONNECT_DELAY = 5.0
WATCHDOG_INTERVAL = 30.0
QUEUE_SIZE = 10000


class App:
    def __init__(self, config: Config, clock: Callable[[], float] = time.monotonic):
        self.config = config
        self.clock = clock
        self.prefix = config.mqtt.prefix
        self.by_mac = {device.mac: device_id for device_id, device in config.devices.items()}
        self.queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue(QUEUE_SIZE)

        start = clock()
        self.last_seen = dict.fromkeys(config.devices, start)
        self.available: dict[str, bool] = {}
        self._counters: dict[str, int] = {}
        self._unknown: set[str] = set()
        self._stale: set[str] = set()
        self._restored = False

    # --- BLE packets ---------------------------------------------------------------------

    def publish(self, topic: str, payload: str) -> None:
        """Queue a retained message (everything mi2mqtt publishes is retained)."""
        try:
            self.queue.put_nowait((topic, payload))
        except asyncio.QueueFull:
            log.warning("MQTT queue full, dropping %s", topic)

    def handle_packet(self, mac: str, rssi: int, uuid: str, data: bytes) -> None:
        reading = parse(uuid, data)
        if reading is None:
            return
        device_id = self.by_mac.get(mac)
        if device_id is None:
            self._log_unknown(mac, rssi, reading)
            return

        self.last_seen[device_id] = self.clock()
        if self.available.get(device_id) is not True:
            self._set_available(device_id, True)
        # Sensors repeat every measurement in many advertisements; only the first one counts.
        if self._counters.get(mac) == reading.counter:
            return
        self._counters[mac] = reading.counter

        log.debug("%s: %s, %d dBm", device_id, reading, rssi)
        for name in ("temperature", "humidity", "battery"):
            value = getattr(reading, name)
            if value is not None:
                self.publish(f"{self.prefix}/{device_id}/{name}", str(value))
        self.publish(f"{self.prefix}/{device_id}/rssi", str(rssi))

    def check_availability(self) -> None:
        now = self.clock()
        for device_id, seen in self.last_seen.items():
            if self.available.get(device_id) is not False and now - seen >= self.config.availability.timeout:
                self._set_available(device_id, False)

    def _set_available(self, device_id: str, available: bool) -> None:
        self.available[device_id] = available
        if available:
            log.info("%s: online", device_id)
        else:
            log.warning("%s: offline, nothing received for %ds", device_id, self.config.availability.timeout)
        self.publish(f"{self.prefix}/{device_id}/availability", "online" if available else "offline")

    def _log_unknown(self, mac: str, rssi: int, reading: Reading) -> None:
        if mac in self._unknown:
            return
        self._unknown.add(mac)
        values = ", ".join(
            f"{name} {value}"
            for name in ("temperature", "humidity", "battery")
            if (value := getattr(reading, name)) is not None
        )
        device_id = "sensor_" + mac.replace(":", "")[-6:].lower()
        log.info(
            "unconfigured sensor %s (%s, %d dBm, %s); to publish it add to mi2mqtt.yaml:\n"
            "  %s:\n    mac: %s\n    name: %s\n    model: %s",
            mac,
            reading.format,
            rssi,
            values,
            device_id,
            mac,
            device_id,
            MODELS[reading.format],
        )

    # --- MQTT messages -------------------------------------------------------------------

    def subscriptions(self) -> list[str]:
        if self.config.discovery.enabled:
            return [f"{self.config.discovery.prefix}/status"]
        return []

    def handle_message(self, topic: str, payload: bytes) -> None:
        if topic == f"{self.config.discovery.prefix}/status" and payload.strip() == b"online":
            log.info("Home Assistant came online, republishing discovery")
            self.publish_all()

    def expected_topics(self) -> set[str]:
        topics = {f"{self.prefix}/status"}
        for device_id in self.config.devices:
            topics.update(f"{self.prefix}/{device_id}/{name}" for name in (*VALUES, "availability"))
        return topics

    def apply_retained(self, topic: str, payload: bytes) -> None:
        """Remember retained topics that mi2mqtt no longer publishes (restore phase)."""
        if not payload:
            return
        if topic == self.prefix or topic.startswith(self.prefix + "/"):
            if topic not in self.expected_topics():
                self._stale.add(topic)
            return
        found = object_id_from_topic(self.config.discovery.prefix, topic)
        if found and found.startswith(LEGACY_OBJECT_PREFIX):
            self._stale.add(topic)
        elif found and found.startswith(OBJECT_PREFIX):
            if found not in {object_id(device) for device in self.config.devices.values()}:
                self._stale.add(topic)

    def publish_all(self) -> None:
        """Discovery, removal of stale retained topics, known availability, then online."""
        if self.config.discovery.enabled:
            for topic, payload in self.discovery().items():
                self.publish(topic, json.dumps(payload, ensure_ascii=False))
        for topic in sorted(self._stale):
            log.info("clearing stale retained topic %s", topic)
            self.publish(topic, "")
        self._stale.clear()
        for device_id, available in self.available.items():
            self.publish(f"{self.prefix}/{device_id}/availability", "online" if available else "offline")
        self.publish(f"{self.prefix}/status", "online")

    def discovery(self) -> dict[str, dict[str, Any]]:
        mqtt = self.config.mqtt
        discovery = self.config.discovery
        return discovery_messages(
            self.config.devices, discovery.prefix, mqtt.prefix, mqtt.qos, discovery.default_entity_ids
        )

    # --- running -------------------------------------------------------------------------

    async def run(self) -> None:
        async with asyncio.TaskGroup() as group:
            group.create_task(self._mqtt(), name="mqtt")
            group.create_task(self._watchdog(), name="availability")
            group.create_task(
                scan(self.config.ble, self.handle_packet, idle_timeout=self.config.availability.timeout),
                name="scanner",
            )

    async def _watchdog(self) -> None:
        while True:
            await asyncio.sleep(WATCHDOG_INTERVAL)
            self.check_availability()

    def _client(self) -> aiomqtt.Client:
        mqtt = self.config.mqtt
        return aiomqtt.Client(
            mqtt.host,
            mqtt.port,
            username=mqtt.username,
            password=mqtt.resolved_password(),
            identifier=mqtt.client_id,
            keepalive=mqtt.keepalive,
            will=aiomqtt.Will(f"{self.prefix}/status", "offline", qos=1, retain=True),
        )

    async def _mqtt(self) -> None:
        while True:
            try:
                async with self._client() as client:
                    log.info("connected to %s:%s", self.config.mqtt.host, self.config.mqtt.port)
                    await self._session(client)
            except aiomqtt.MqttError as error:
                log.warning("MQTT: %s; reconnecting in %.0fs", error, RECONNECT_DELAY)
                await asyncio.sleep(RECONNECT_DELAY)

    async def _session(self, client: aiomqtt.Client) -> None:
        if not self._restored:
            await self._restore(client)
            self._restored = True
        for topic in self.subscriptions():
            await client.subscribe(topic, self.config.mqtt.qos)
        self.publish_all()

        writer = asyncio.create_task(self._writer(client), name="mqtt writer")
        try:
            async for message in client.messages:
                try:
                    self.handle_message(message.topic.value, message.payload)
                except Exception:
                    log.exception("error handling message on %s", message.topic.value)
        except asyncio.CancelledError:
            writer.cancel()
            await self._shutdown(client)
            raise
        finally:
            writer.cancel()

    async def _restore(self, client: aiomqtt.Client) -> None:
        """Collect our retained topics, to clear the ones that are no longer used."""
        qos = self.config.mqtt.qos
        sync = f"{self.prefix}/_sync/{secrets.token_hex(4)}"
        topics = [f"{self.prefix}/#"]
        if self.config.discovery.enabled:
            topics.append(f"{self.config.discovery.prefix}/device/+/config")
        for topic in topics:
            await client.subscribe(topic, qos)
        # Retained messages are delivered before this message comes back to us.
        await client.publish(sync, "sync", qos=qos)
        try:
            async with asyncio.timeout(RESTORE_TIMEOUT):
                async for message in client.messages:
                    if message.topic.value == sync:
                        break
                    if message.retain:
                        self.apply_retained(message.topic.value, message.payload)
        except TimeoutError:
            log.warning("restore: no sync message within %.0fs, continuing", RESTORE_TIMEOUT)
        for topic in topics:
            await client.unsubscribe(topic)

    async def _writer(self, client: aiomqtt.Client) -> None:
        qos = self.config.mqtt.qos
        while True:
            topic, payload = await self.queue.get()
            await client.publish(topic, payload, qos=qos, retain=True)

    async def _shutdown(self, client: aiomqtt.Client) -> None:
        log.info("shutting down")
        try:
            async with asyncio.timeout(5):
                while not self.queue.empty():
                    topic, payload = self.queue.get_nowait()
                    await client.publish(topic, payload, qos=self.config.mqtt.qos, retain=True)
                await client.publish(f"{self.prefix}/status", "offline", qos=1, retain=True)
        except Exception as error:  # best effort
            log.warning("shutdown incomplete: %s", error)
