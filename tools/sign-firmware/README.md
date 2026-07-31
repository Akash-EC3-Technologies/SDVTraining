# Firmware Signing Tool

The Firmware Signing Tool generates a signed firmware manifest for an OTA firmware image and uploads both the firmware and manifest to a file server.

The tool performs the following steps:

1. Computes the SHA-256 hash of the firmware image.
2. Generates a `FirmwareManifest.json`.
3. Signs the manifest using an ECDSA P-256 private key.
4. Uploads the firmware image.
5. Uploads the manifest.

The generated manifest can later be verified by the OTA client before downloading and installing the firmware.

---

## Requirements

- Python 3.11+
- OpenSSL 3.x (or newer)

Install the Python dependencies:

```bash
pip install -r requirements.txt
```

---

## Generating Signing Keys

The OTA client must trust the public key corresponding to the private key used for signing firmware.

### Generate an ECDSA P-256 Private Key

```bash
openssl ecparam \
    -name prime256v1 \
    -genkey \
    -noout \
    -out firmware-signing-key.pem
```

### Generate the Public Key

```bash
openssl ec \
    -in firmware-signing-key.pem \
    -pubout \
    -out firmware-signing-pub.pem
```

The resulting files are:

```
firmware-signing-key.pem     # Keep secret
firmware-signing-pub.pem     # Install on OTA clients
```

---

## Generating a Self-Signed Certificate (Optional)

If your OTA infrastructure prefers distributing certificates instead of raw public keys, generate a self-signed certificate.

```bash
openssl req \
    -new \
    -x509 \
    -key firmware-signing-key.pem \
    -days 3650 \
    -subj "/CN=Firmware Signing Key/O=Example Corp" \
    -out firmware-signing-cert.pem
```

The OTA client can extract the public key from this certificate during provisioning.

---

## Installing the Trusted Signing Key

Provision one of the following on every OTA client:

- `firmware-signing-pub.pem`
- or `firmware-signing-cert.pem`

Associate it with the configured Key ID.

Example:

```python
trusted_keys = {
    "prod-signing-key-v1": "keys/firmware-signing-pub.pem"
}
```

The `keyId` embedded in the manifest must match this mapping.

---

## Manifest Format

Example:

```json
{
  "ecuId": "VCU",
  "version": "1.2.3",
  "created": "2026-08-04T00:00:00Z",
  "size": 4214123,
  "hashAlgorithm": "SHA-256",
  "hash": "9f02dd79d44a17d2...",
  "downloadUrl": "https://server/fw-1.2.3.bin",
  "signature": {
    "algorithm": "ECDSA-P256-SHA256",
    "keyId": "prod-signing-key-v1",
    "value": "MEUCIQC..."
  }
}
```

The signature is generated over the manifest **excluding** the `signature` object using canonical JSON serialization:

- UTF-8 encoding
- Sorted keys
- Compact separators (`(",", ":")`)

The downloader must use the same canonicalization before verifying the signature.

---

## Usage

Example:

```bash
python sign_firmware.py \
    --ecu-id VCU \
    --version 1.2.3 \
    --firmware build/vcu.bin \
    --private-key keys/firmware-signing-key.pem \
    --key-id prod-signing-key-v1 \
    --upload-url https://ota:secret@fileserver.example.com/uploads
```

The upload URL may include Basic Authentication credentials.

---

## Uploaded Files

After a successful run, the destination directory contains:

```
uploads/
├── vcu.bin
└── FirmwareManifest.json
```

---

## Verification Flow

The OTA client performs the following checks:

1. Download `FirmwareManifest.json`.
2. Verify the manifest signature using the trusted public key identified by `keyId`.
3. Download the firmware image from `downloadUrl`.
4. Verify the firmware hash matches the manifest.
5. Verify the firmware size matches the manifest.
6. Store the validated firmware locally.
7. Proceed with the installation.

If any verification step fails, the firmware must be rejected.

---

## Security Notes

- Never distribute the private signing key.
- Store the private key in a secure location or an HSM when used in production.
- Rotate signing keys periodically by introducing a new `keyId`.
- OTA clients should trust only explicitly provisioned public keys or certificates.
- Always use HTTPS for firmware distribution.
- Protect the upload endpoint using authentication (for example, Basic Authentication or mutual TLS).

---

## Project Structure

```
.
├── sign_firmware.py
├── requirements.txt
├── keys/
│   ├── firmware-signing-key.pem
│   ├── firmware-signing-pub.pem
│   └── firmware-signing-cert.pem
└── README.md
```
