from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Callable, Optional

import paho.mqtt.client as mqtt


class MqttService:
    """
    Thin wrapper around paho-mqtt that:
      - connects via mTLS using certs from CERTS_PATH
      - publishes OTA messages
      - subscribes to vehicle OTA status messages
    """

    def __init__(self, message_handler: Callable[[str, dict[str, Any]], None]) -> None:
        self.message_handler = message_handler
        self.client: Optional[mqtt.Client] = None
        self._connected = threading.Event()

    @staticmethod
    def _cert_paths() -> tuple[Path, Path, Path]:
        certs_path = Path(os.environ.get("CERTS_PATH", "./certs")).resolve()
        client_cert = certs_path / "client.crt"
        client_key = certs_path / "client.key"
        ca_cert = certs_path / "ca.crt"
        for p in (client_cert, client_key, ca_cert):
            if not p.exists():
                raise FileNotFoundError(f"Missing MQTT TLS file: {p}")
        return client_cert, client_key, ca_cert

    def connect(self) -> None:
        host = os.environ["MQTT_HOST"]
        port = int(os.environ.get("MQTT_PORT", "8883"))
        client_id = os.environ.get("MQTT_CLIENT_ID", "ota-manager-team-<id>")

        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_id,
            clean_session=True,
        )

        client.enable_logger()
        client.on_connect = self._on_connect
        client.on_message = self._on_message
        client.on_disconnect = self._on_disconnect

        client_cert, client_key, ca_cert = self._cert_paths()
        print(f"CA:     {ca_cert}")
        print(f"Client: {client_cert}")
        print(f"Key:    {client_key}")
        print(f"Host:   {host}:{port}")
        client.tls_set(
            ca_certs=str(ca_cert),
            certfile=str(client_cert),
            keyfile=str(client_key),
        )
        client.tls_insecure_set(False)

        err_code = client.connect(host, port, keepalive=60)
        if err_code != mqtt.MQTT_ERR_SUCCESS:
            raise ConnectionError(f"MQTT connection failed: {err_code}")

        err_code = client.loop_start()
        if err_code != mqtt.MQTT_ERR_SUCCESS:
            raise ConnectionError(f"MQTT connection failed: {err_code}")

        self.client = client
        if not self._connected.wait(timeout=15):
            raise TimeoutError("MQTT connection timed out")

    def cleanup(self) -> None:
        if self.client is None:
            return
        try:
            self.client.loop_stop()
        finally:
            self.client.disconnect()

    def publish_ota_update(self, vehicle_id: str, payload: dict[str, Any]) -> None:
        if self.client is None:
            raise RuntimeError("MQTT client not connected")
        topic = f"/vehicles/{vehicle_id}/commands/ota-update"
        result = self.client.publish(topic, json.dumps(payload), qos=1)
        result.wait_for_publish()

    def subscribe_status(self) -> None:
        if self.client is None:
            raise RuntimeError("MQTT client not connected")
        self.client.subscribe("/vehicles/+/status/ota-update", qos=1)

    def _on_connect(
        self, client: mqtt.Client | None, userdata, flags, reason_code, properties
    ) -> None:  # pragma: no cover
        if client is None or reason_code != 0:
            print(f"MQTT connection failed: {reason_code}")
            return
        self.client = client
        self._connected.set()
        print(f"connected to mqtt broker at {self.client.host}")
        self.subscribe_status()

    def _on_disconnect(
        self, client, userdata, disconnect_flags, reason_code, properties
    ) -> None:  # pragma: no cover
        self._connected.clear()

    def _on_message(self, client, userdata, msg) -> None:  # pragma: no cover
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except Exception:
            return
        self.message_handler(msg.topic, payload)
