# FirmwareDownloader.py
from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlsplit, urlunsplit

import requests
from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec


class FirmwareDownloadError(Exception):
    """Base class for firmware download / verification errors."""


class ManifestDownloadError(FirmwareDownloadError):
    pass


class SignatureVerificationError(FirmwareDownloadError):
    pass


class HashVerificationError(FirmwareDownloadError):
    pass


class SizeVerificationError(FirmwareDownloadError):
    pass


class UnsupportedAlgorithmError(FirmwareDownloadError):
    pass


@dataclass(frozen=True)
class FirmwareResult:
    manifest: dict[str, Any]
    firmware_path: Path


def _canonical_json_without_signature(manifest: dict[str, Any]) -> bytes:
    """
    Canonicalize the manifest for signature verification.

    IMPORTANT:
    The server and client must agree on the exact serialization format.
    This implementation signs/verifies the manifest JSON with the `signature`
    field removed, using sorted keys and compact separators.
    """
    payload = {k: v for k, v in manifest.items() if k != "signature"}
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


class FirmwareDownloader:
    """
    Download firmware from a file server, verify manifest signature,
    download the firmware blob, verify its hash/size, and save it locally.
    """

    def __init__(
        self,
        firmware_dir: str | Path = "firmware",
        trusted_public_keys: Optional[Dict[str, str | bytes | Path]] = None,
        timeout_seconds: int = 30,
        verify_tls: bool = True,
        session: Optional[requests.Session] = None,
    ) -> None:
        """
        Parameters
        ----------
        firmware_dir:
            Local directory where validated firmware files will be stored.
        trusted_public_keys:
            Mapping from `keyId` -> public key material. Each value may be:
            - PEM string
            - PEM bytes
            - path to a PEM file
        timeout_seconds:
            HTTP timeout for downloads.
        verify_tls:
            Whether to verify TLS certificates.
        session:
            Optional requests.Session to reuse connections.
        """
        self.firmware_dir = Path(firmware_dir)
        self.firmware_dir.mkdir(parents=True, exist_ok=True)

        self.trusted_public_keys = trusted_public_keys or {}
        self.timeout_seconds = timeout_seconds
        self.verify_tls = verify_tls
        self.session = session or requests.Session()

    def DownloadAndVerifyFirmware(
        self, manifest_url: str
    ) -> Tuple[dict[str, Any], Path]:
        """
        Download the manifest, verify its signature, download the firmware,
        verify its hash/size, save it under firmware_dir, and return:

            (manifest_json_dict, local_firmware_path)

        Parameters
        ----------
        firmware_url:
            URL of the firmware manifest
        """
        manifest = self._download_manifest(manifest_url)
        self._verify_manifest_signature(manifest)

        # Use manifest's downloadUrl if present, otherwise fall back to the input firmware_url.
        download_url = manifest.get("downloadUrl")
        ecu_id = manifest.get("ecuId", "unknown-ecu")
        version = manifest.get("version", "unknown-version")

        expected_size = manifest.get("size")
        expected_hash = manifest.get("hash")
        hash_algorithm = manifest.get("hashAlgorithm", "SHA-256")

        local_dir = self.firmware_dir / str(ecu_id) / str(version)
        local_dir.mkdir(parents=True, exist_ok=True)

        filename = os.path.basename(urlsplit(download_url).path) or "firmware.bin"
        final_path = local_dir / filename

        tmp_fd, tmp_name = tempfile.mkstemp(
            prefix="fw-", suffix=".download", dir=str(local_dir)
        )
        os.close(tmp_fd)
        tmp_path = Path(tmp_name)

        try:
            self._download_to_file(download_url, tmp_path)

            # Verify size.
            actual_size = tmp_path.stat().st_size
            if expected_size is not None and actual_size != int(expected_size):
                raise SizeVerificationError(
                    f"Size mismatch: expected {expected_size}, got {actual_size}"
                )

            # Verify hash.
            actual_hash = self._compute_hash(tmp_path, hash_algorithm)
            if (
                expected_hash is not None
                and actual_hash.lower() != str(expected_hash).lower()
            ):
                raise HashVerificationError(
                    f"Hash mismatch: expected {expected_hash}, got {actual_hash}"
                )

            # Move into final location only after validation succeeds.
            shutil.move(str(tmp_path), str(final_path))
            return manifest, final_path

        finally:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass

    def _download_manifest(self, manifest_url: str) -> dict[str, Any]:
        try:
            resp = self.session.get(
                manifest_url,
                timeout=self.timeout_seconds,
                verify=self.verify_tls,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            raise ManifestDownloadError(
                f"Failed to download manifest: {manifest_url}"
            ) from exc
        except json.JSONDecodeError as exc:
            raise ManifestDownloadError("Manifest is not valid JSON") from exc

    def _verify_manifest_signature(self, manifest: dict[str, Any]) -> None:
        signature = manifest.get("signature")
        if not isinstance(signature, dict):
            raise SignatureVerificationError(
                "Manifest does not contain a valid signature object"
            )

        algorithm = signature.get("algorithm")
        key_id = signature.get("keyId")
        value = signature.get("value")

        if not algorithm or not key_id or not value:
            raise SignatureVerificationError(
                "Signature object must contain algorithm, keyId, and value"
            )

        public_key = self._load_trusted_public_key(str(key_id))

        payload = _canonical_json_without_signature(manifest)
        sig_bytes = self._decode_signature_value(str(value))

        if str(algorithm).upper() == "ECDSA-P256-SHA256":
            try:
                public_key.verify(sig_bytes, payload, ec.ECDSA(hashes.SHA256()))
            except InvalidSignature as exc:
                raise SignatureVerificationError(
                    "Manifest signature verification failed"
                ) from exc
            return

        raise UnsupportedAlgorithmError(f"Unsupported signature algorithm: {algorithm}")

    def _load_trusted_public_key(self, key_id: str):
        material = self.trusted_public_keys.get(key_id)
        if material is None:
            raise SignatureVerificationError(
                f"No trusted public key configured for keyId={key_id}"
            )

        if isinstance(material, Path):
            pem = material.read_bytes()
        elif isinstance(material, bytes):
            pem = material
        elif isinstance(material, str):
            path = Path(material)
            pem = path.read_bytes() if path.exists() else material.encode("utf-8")
        else:
            raise SignatureVerificationError(
                f"Unsupported public key material type for keyId={key_id}"
            )

        try:
            return serialization.load_pem_public_key(pem)
        except ValueError as exc:
            raise SignatureVerificationError(
                f"Invalid PEM public key for keyId={key_id}"
            ) from exc

    @staticmethod
    def _decode_signature_value(value: str) -> bytes:
        # The example uses Base64 ("MEUCIQC..."), so decode as Base64 here.
        try:
            return base64.b64decode(value, validate=True)
        except Exception as exc:
            raise SignatureVerificationError(
                "Signature value is not valid Base64"
            ) from exc

    @staticmethod
    def _compute_hash(path: Path, hash_algorithm: str) -> str:
        algo = hash_algorithm.strip().upper().replace("-", "")
        if algo == "SHA256":
            h = hashlib.sha256()
        elif algo == "SHA384":
            h = hashlib.sha384()
        elif algo == "SHA512":
            h = hashlib.sha512()
        else:
            raise UnsupportedAlgorithmError(
                f"Unsupported hash algorithm: {hash_algorithm}"
            )

        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()

    def _download_to_file(self, url: str, dest_path: Path) -> None:
        try:
            with self.session.get(
                url,
                stream=True,
                timeout=self.timeout_seconds,
                verify=self.verify_tls,
            ) as resp:
                resp.raise_for_status()
                with dest_path.open("wb") as f:
                    for chunk in resp.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            f.write(chunk)
        except requests.RequestException as exc:
            raise FirmwareDownloadError(f"Failed to download firmware: {url}") from exc
