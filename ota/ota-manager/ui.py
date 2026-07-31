from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from flask import Blueprint, render_template

ui_bp = Blueprint("ui", __name__, url_prefix="/ui")


def _campaigns_file() -> Path:
    from main import CAMPAIN_STATUS_FILE
    return CAMPAIN_STATUS_FILE


def _load_campaigns() -> dict[str, Any]:
    path = _campaigns_file()
    if not path.exists():
        return {"campains": []}
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {"campains": []}


@ui_bp.get("/campain/new")
def new_campaign():
    return render_template("new_campaign.html")


@ui_bp.get("/campain/<campain_id>/status")
def campain_status(campain_id: str):
    data = _load_campaigns()
    campains = data.get("campains", [])
    campaign = next((c for c in campains if c.get("CampainId") == campain_id), None)
    if campaign is None:
        return render_template("campaign_status.html", campaign=None, campain_id=campain_id), 404
    return render_template("campaign_status.html", campaign=campaign, campain_id=campain_id)
