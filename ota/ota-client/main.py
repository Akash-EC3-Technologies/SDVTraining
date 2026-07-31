from __future__ import annotations

import argparse
import atexit
import os
import threading
from enum import StrEnum
from pathlib import Path
from typing import Any

from firmware_downloader import FirmwareDownloader
from mqtt import MqttService
from stm32_flasher import STM32Flasher

_mqtt_lock = threading.Lock()
mqtt_service: MqttService | None = None
_mqtt_cleanup_registered = False


class Status(StrEnum):
    DOWNLOAD_FAILED = "download-failed"
    FIRWARE_FALSH_FAILED = "firmware-falsh-failed"
    COMPLETED = "completed"
    MALFORMED_REQUEST = "malformed-request"


trusted_keys = {
    "signing-key-1": "keys/firmware-signing-pub.pem",
}

downloader = FirmwareDownloader(
    firmware_dir="./firmwares",
    trusted_public_keys=trusted_keys,
)


def update_firmware(
    firmware: str | Path,
    *,
    cli: str = "STM32_Programmer_CLI",
    interface: str = "swd",
    verbose: int = 0,
) -> None:
    """
    Flash the firmware, verify it, and start execution.
    """
    flasher = STM32Flasher(interface=interface, cli=cli, verbose=verbose)

    print(flasher.info())
    print(flasher.flash(firmware, reset=False))
    print(flasher.verify(firmware))
    print(flasher.reset())


def _status_payload(campainId: str, status: Status, message: str) -> dict[str, Any]:
    return {"CampainId": campainId, "Status": status, "Message": message}


def _handle_ota_update(topic: str, payload: dict[str, Any]) -> None:

    required = [
        "CampainId",
        "CampainName",
        "ReleaseVersion",
        "TargetGeneration",
        "TargetRegions",
        "FirmwareMetadataUrls",
    ]
    missing = [field for field in required if field not in payload]
    if missing:
        if payload["CampainId"]:
            get_mqtt_service().publish_ota_update_status(
                _status_payload(
                    payload["CampainId"],
                    Status.MALFORMED_REQUEST,
                    f"missing required fileds {missing}",
                )
            )
        return
    if not isinstance(payload.get("Firmwares"), list):
        get_mqtt_service().publish_ota_update_status(
            _status_payload(
                payload["CampainId"],
                Status.MALFORMED_REQUEST,
                f"missing required fileds {missing}",
            )
        )
        return
    print(f"Recived OTA Update for {payload['CampainId']}")
    downloaded_firmwares = []
    for firmware_url in payload.get("Firmwares"):
        try:
            manifest, firmware_path = downloader.DownloadAndVerifyFirmware(firmware_url)
            downloaded_firmwares.append((manifest, firmware_path))
            print(
                f"Downloaded {manifest['ecuId']} {manifest['version']} -> {firmware_path}"
            )
        except Exception as e:
            get_mqtt_service().publish_ota_update_status(
                _status_payload(
                    payload["CampainId"],
                    Status.DOWNLOAD_FAILED,
                    f"failed to download firmware: {e}",
                )
            )

    for firmware_manifest, firmware_path in downloaded_firmwares:
        try:
            update_firmware(firmware=firmware_path)
        except Exception as e:
            get_mqtt_service().publish_ota_update_status(
                _status_payload(
                    payload["CampainId"],
                    Status.MALFORMED_REQUEST,
                    f"failed to download firmware: {e}",
                )
            )


def create_mqtt_service() -> MqttService:
    service = MqttService(message_handler=_handle_ota_update)
    service.connect()
    return service


def _cleanup_mqtt_service() -> None:
    global mqtt_service
    service = mqtt_service
    mqtt_service = None
    if service is not None:
        service.cleanup()


def get_mqtt_service() -> MqttService:
    global mqtt_service, _mqtt_cleanup_registered

    if mqtt_service is not None:
        return mqtt_service
    with _mqtt_lock:
        if mqtt_service is None:
            mqtt_service = create_mqtt_service()
            if not _mqtt_cleanup_registered:
                _ = atexit.register(_cleanup_mqtt_service)
                _mqtt_cleanup_registered = True
    return mqtt_service


def main() -> None:
    _ = get_mqtt_service()
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
