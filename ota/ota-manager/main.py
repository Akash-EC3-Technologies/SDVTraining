from __future__ import annotations

import atexit
import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, redirect, request, url_for
from mqtt import MqttService
from ui import ui_bp

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("DATA_DIR", str(APP_DIR))).resolve()
VEHICLE_DATA_FILE = Path(
    os.environ.get("VEHICLE_DATA_FILE", str(DATA_DIR / "vehicle-data.json"))
).resolve()
CAMPAIN_STATUS_FILE = Path(
    os.environ.get("CAMPAIN_STATUS_FILE", str(DATA_DIR / "campain-status.json"))
).resolve()

app = Flask(__name__)
app.register_blueprint(ui_bp)

_state_lock = threading.RLock()
_mqtt_lock = threading.Lock()
mqtt_service: MqttService | None = None
_mqtt_cleanup_registered = False


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
        f.write("\n")
    tmp_path.replace(path)


def _load_vehicles() -> list[dict[str, Any]]:
    data = _read_json(VEHICLE_DATA_FILE, {"vehicles": []})
    return data.get("vehicles", [])


def _load_campains() -> dict[str, Any]:
    data = _read_json(CAMPAIN_STATUS_FILE, {"campains": []})
    campains = data.get("campains", [])
    if isinstance(campains, list):
        return {
            c["CampainId"]: c
            for c in campains
            if isinstance(c, dict) and c.get("CampainId")
        }
    if isinstance(campains, dict):
        return campains
    return {}


def _save_campains(campains: dict[str, Any]) -> None:
    _write_json(CAMPAIN_STATUS_FILE, {"campains": list(campains.values())})


def _parse_targets(value: str | None) -> set[str] | None:
    if not value:
        return None
    value = value.strip()
    if not value or value.lower() == "all":
        return None
    return {item.strip() for item in value.split(",") if item.strip()}


def _matches_target(vehicle_value: str, targets: set[str] | None) -> bool:
    if targets is None:
        return True
    return vehicle_value in targets


def _select_vehicles(
    target_generation: str | None, target_region: str | None
) -> list[dict[str, Any]]:
    generations = _parse_targets(target_generation)
    regions = _parse_targets(target_region)
    selected: list[dict[str, Any]] = []
    for vehicle in _load_vehicles():
        if _matches_target(
            str(vehicle.get("Generation", "")), generations
        ) and _matches_target(str(vehicle.get("Region", "")), regions):
            selected.append(vehicle)
    return selected


def _create_campaign_record(
    payload: dict[str, Any], vehicles: list[dict[str, Any]]
) -> dict[str, Any]:
    campain_id = str(uuid.uuid4())
    return {
        "CampainId": campain_id,
        "CampainName": payload["CampainName"],
        "ReleaseVersion": payload["ReleaseVersion"],
        "TargetGeneration": payload.get("TargetGeneration", "All"),
        "TargetRegion": payload.get("TargetRegions", "All"),
        "FirmwareMetadataUrls": payload.get("Firmwares", []),
        "CreatedAt": _utc_now(),
        "Status": [
            {
                "VehicleName": vehicle.get("Name", ""),
                "VehicleId": vehicle.get("VehicleId", ""),
                "Status": "UpdateTriggered",
                "LastUpdatedAt": _utc_now(),
            }
            for vehicle in vehicles
        ],
    }


def _handle_mqtt_status(topic: str, payload: dict[str, Any]) -> None:
    campain_id = (
        payload.get("campainId")
        or payload.get("campain_id")
        or payload.get("CampainId")
    )
    vehicle_id = (
        topic.split("/")[2] if len(topic.split("/")) > 2 else payload.get("VehicleId")
    )
    status = payload.get("status") or payload.get("Status")
    if not campain_id or not vehicle_id or not status:
        return

    with _state_lock:
        campains = _load_campains()
        campaign = campains.get(campain_id)
        if not campaign:
            return
        for row in campaign.get("Status", []):
            if row.get("VehicleId") == vehicle_id:
                row["Status"] = status
                row["LastUpdatedAt"] = _utc_now()
                row["RawMessage"] = payload
                break
        _save_campains(campains)


def create_app_mqtt() -> MqttService:
    service = MqttService(message_handler=_handle_mqtt_status)
    service.connect()
    return service


def _cleanup_mqtt_service() -> None:
    global mqtt_service
    service = mqtt_service
    mqtt_service = None
    if service is not None:
        service.cleanup()


def _prepare_mqtt_client_id() -> None:
    """
    If the caller did not set MQTT_CLIENT_ID, make it unique per process so
    multiple Gunicorn workers do not stomp on each other at the broker.
    """
    if not os.environ.get("MQTT_CLIENT_ID"):
        os.environ["MQTT_CLIENT_ID"] = f"ota-manager-team-<id>"


def get_mqtt_service() -> MqttService:
    global mqtt_service, _mqtt_cleanup_registered

    if mqtt_service is not None:
        return mqtt_service
    with _mqtt_lock:
        if mqtt_service is None:
            _prepare_mqtt_client_id()
            mqtt_service = create_app_mqtt()
            if not _mqtt_cleanup_registered:
                atexit.register(_cleanup_mqtt_service)
                _mqtt_cleanup_registered = True
    return mqtt_service


@app.get("/")
def root():
    return redirect("/ota-manager-team-<id>" + url_for("ui.new_campaign"))


@app.post("/api/campain/new")
def create_campaign():
    if not request.is_json:
        return jsonify({"error": "Expected application/json"}), 415

    body = request.get_json(silent=True) or {}
    required = [
        "CampainName",
        "ReleaseVersion",
        "TargetGeneration",
        "TargetRegions",
        "Firmwares",
    ]
    missing = [field for field in required if field not in body]
    if missing:
        return jsonify({"error": f"Missing fields: {', '.join(missing)}"}), 400

    if not isinstance(body.get("Firmwares"), list):
        return jsonify({"error": "Firmwares must be a list"}), 400

    with _state_lock:
        vehicles = _select_vehicles(
            body.get("TargetGeneration"), body.get("TargetRegions")
        )
        campaign = _create_campaign_record(body, vehicles)

        campains = _load_campains()
        campains[campaign["CampainId"]] = campaign
        _save_campains(campains)

    try:
        service = get_mqtt_service()
    except Exception as exc:
        return jsonify({"error": f"MQTT connection failed: {exc}"}), 503

    publish_payload = {
        "CampainId": campaign["CampainId"],
        "CampainName": campaign["CampainName"],
        "ReleaseVersion": campaign["ReleaseVersion"],
        "TargetGeneration": campaign["TargetGeneration"],
        "TargetRegion": campaign["TargetRegion"],
        "FirmwareMetadataUrls": campaign["FirmwareMetadataUrls"],
    }

    publish_errors: list[dict[str, str]] = []
    for vehicle in vehicles:
        try:
            service.publish_ota_update(
                vehicle["VehicleId"],
                {
                    **publish_payload,
                    "VehicleId": vehicle["VehicleId"],
                    "VehicleName": vehicle.get("Name", ""),
                    "VehicleGeneration": vehicle.get("Generation", ""),
                    "VehicleRegion": vehicle.get("Region", ""),
                },
            )
        except Exception as exc:  # pragma: no cover
            publish_errors.append(
                {"VehicleId": str(vehicle.get("VehicleId", "")), "error": str(exc)}
            )

    if publish_errors:
        with _state_lock:
            campains = _load_campains()
            campaign = campains[campaign["CampainId"]]
            for row in campaign.get("Status", []):
                if any(
                    err["VehicleId"] == row.get("VehicleId") for err in publish_errors
                ):
                    row["Status"] = "UpdateFailed"
                    row["LastUpdatedAt"] = _utc_now()
                    row["RawMessage"] = {"publish_error": True}
            _save_campains(campains)
        return jsonify({"campaign": campaign, "publish_errors": publish_errors}), 207

    return jsonify({"campaign": campaign}), 201


@app.get("/api/campain/<campain_id>/status")
def campaign_status_json(campain_id: str):
    campains = _load_campains()
    campaign = campains.get(campain_id)
    if not campaign:
        return jsonify({"error": "Campaign not found"}), 404
    return jsonify(campaign)


if __name__ == "__main__":
    port = int(os.environ.get("HTTP_PORT", "8000"))
    get_mqtt_service()
    app.run(host="0.0.0.0", port=port, debug=False)
