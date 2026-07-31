#!/usr/bin/env python3

from __future__ import annotations

import argparse
import base64
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

Manifest = dict[str, Any]


def sha256(path: Path) -> str:
    h = hashlib.sha256()

    with path.open("rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)

    return h.hexdigest()


def canonical_json(manifest: Manifest) -> bytes:
    payload = {k: v for k, v in manifest.items() if k != "signature"}

    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sign_manifest(
    manifest: Manifest,
    private_key_path: Path,
    key_id: str,
) -> Manifest:

    with private_key_path.open("rb") as f:
        private_key = serialization.load_pem_private_key(
            f.read(),
            password=None,
        )

    signature = private_key.sign(
        canonical_json(manifest),
        ec.ECDSA(hashes.SHA256()),
    )

    manifest["signature"] = {
        "algorithm": "ECDSA-P256-SHA256",
        "keyId": key_id,
        "value": base64.b64encode(signature).decode(),
    }

    return manifest


def upload_file(upload_url: str, file_path: Path) -> None:

    with file_path.open("rb") as f:
        response = requests.put(
            f"{upload_url.rstrip('/')}/{file_path.name}",
            data=f,
        )

    response.raise_for_status()


def main() -> None:

    parser = argparse.ArgumentParser()

    parser.add_argument("--ecu-id", required=True)
    parser.add_argument("--version", required=True)

    parser.add_argument(
        "--firmware",
        type=Path,
        required=True,
    )

    parser.add_argument(
        "--private-key",
        type=Path,
        required=True,
    )

    parser.add_argument(
        "--key-id",
        required=True,
    )

    parser.add_argument(
        "--download-url",
        required=True,
        help="Destination directory URL.",
    )

    args = parser.parse_args()

    firmware = args.firmware.resolve()

    firmware_hash = sha256(firmware)

    manifest: Manifest = {
        "ecuId": args.ecu_id,
        "version": args.version,
        "created": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "size": firmware.stat().st_size,
        "hashAlgorithm": "SHA-256",
        "hash": firmware_hash,
        "downloadUrl": (f"{args.download_url}"),
    }

    manifest = sign_manifest(
        manifest,
        args.private_key,
        args.key_id,
    )

    manifest_file = firmware.parent / "FirmwareManifest.json"

    manifest_file.write_text(json.dumps(manifest, indent=2))

    print()
    print("Done")
    print(f"Firmware : {firmware.name}")
    print(f"Manifest : {manifest_file}")
    print(f"SHA256   : {firmware_hash}")


if __name__ == "__main__":
    main()
