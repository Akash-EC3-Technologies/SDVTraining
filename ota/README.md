# OTA ECU Firmware Update

---

# OTA ECU Firmware Update

This project demonstrates an end-to-end **Over-the-Air (OTA) firmware update workflow** for an automotive Electronic Control Unit (ECU). It provides a complete development environment that simulates both the **cloud/backend infrastructure** and the **vehicle-side components**, allowing developers to build, deploy, test, and validate the OTA update process.

The project is divided into two major domains:

- **Cloud Side** – Responsible for publishing firmware, managing OTA campaigns, and communicating with the vehicle.
- **Onboard Side** – Responsible for receiving OTA notifications, downloading firmware, validating it, flashing the ECU, and reporting the update status.

---

# Overview

The OTA update workflow consists of the following high-level steps:

1. A firmware image is built and uploaded to the artifact repository.
2. An OTA release is created through the OTA Manager.
3. The OTA Manager publishes an update notification over MQTT.
4. The onboard OTA client receives the notification.
5. The client downloads the firmware package.
6. The firmware integrity and authenticity are verified.
7. The target ECU is programmed.
8. The ECU reboots into the new firmware.
9. The OTA client reports the final update status back to the backend.

---

# Hardware

The demonstration setup consists of three devices.

| Device                | Purpose                                                                                                            |
| --------------------- | ------------------------------------------------------------------------------------------------------------------ |
| Raspberry Pi 5        | Central Compute Unit running the onboard OTA client                                                                |
| STM32 Nucleo F334R8   | Target ECU receiving firmware updates                                                                              |
| Raspberry Pi / Laptop | Simulates the cloud backend and hosts the Kubernetes cluster, MQTT broker, local Docker registry, and OTA services |

---

# Development Environment Setup

This section prepares the Raspberry Pi as teh development environment

---

## Boot the Raspberry Pi

The Raspberry Pi comes with Raspberry Pi OS (Raspbian) already flashed to the SD card.

1. Insert the SD card.
2. Connect the power supply.
3. Wait for the system to finish booting and power/activity led to turn green.

---

## Connect to the Raspberry Pi via SSH

Obtain the following information from the trainer:

- Team Id
- Raspberry Pi HostName
- Raspberry Pi Username
- Raspberry Pi Password

Connect using SSH.

```bash
ssh <username>@<host-name>.local
```

Example:

```bash
ssh dev@dev-env-<team-id>.local
```

---

## Update the Operating System

Update the package index and upgrade all installed packages.

```bash
sudo apt update        # Refresh package list
sudo apt full-upgrade -y     # Install the latest package updates
```

---

## Install Required Development Tools

### Install necessary development tools.

```bash
sudo apt install -y \
    git \
    vim \
    tree \
    curl \
    wget \
    mosquitto-clients \
    docker.io \
    kubectl \
    ca-certificates
```

## Setup certificates for communication to server

The server simulates the infrastructure components needed for the ota-manager service.

It consists of:

- Kubernetes cluster -> ota-lab.local
- MQTT Broker -> mqtt-broker.ota-lab.local:8883
- Docker Registry -> registry.ota-lab.local
- File Server -> file-server.ota-lab.local:9000

Map Server IP address to comonnet's dns name in known hosts

open /etc/hosts

```bash
sudo vim /etc/hosts
```

add server ip <-> host mapping to /etc/hosts

```
<server-ip-address> ota-lab.local ota-lab
<server-ip-address> file-server.ota-lab.local file-server.ota-lab
<server-ip-address> registry.ota-lab.local registry.ota-lab
<server-ip-address> mqtt-broker.ota-lab.local mqtt-broker.ota-lab
```

Add step cli package repository

```bash
sudo mkdir -p /etc/apt/keyrings
curl -fsSL https://packages.smallstep.com/keys/apt/repo-signing-key.gpg | sudo tee /etc/apt/keyrings/smallstep.asc >/dev/null
```

```bash
cat <<EOF | sudo tee /etc/apt/sources.list.d/smallstep.sources
Types: deb
URIs: https://packages.smallstep.com/stable/debian
Suites: debs
Components: main
Signed-By: /etc/apt/keyrings/smallstep.asc
EOF
```

### Install step cli

```bash
sudo apt update
sudo apt install -y step-cli
step version
```

### Verify TLS Trust Before Installing the Root CA

Attempt to access the file server before trusting the CA.

```bash
curl -u user:password https://file-server.ota-lab.local:9000/downloads/
```

Because the operating system does not yet trust the Step-CA Root CA, the TLS handshake should fail with a certificate trust error. This is expected, as the client cannot verify the server certificate.

### Bootstrap Root Ca certificate

```bash
step ca bootstrap --ca-url https://ota-lab.local:4000 --fingerprint 6cb3170d88b08a605c65c0dc14acbd0b242bc263440fe990ea62d29e38e49583
step ca health
```

Verify the Bootstrap Configuration by examining the step cli config directory `~/.step`

```bash
tree ~/.step
```

The folder should contain

```text
~/.step/
├── certs/
│ └── root_ca.crt
└── config/
 └── defaults.json
```

### Install the Root CA into the Operating System Trust Store

Installing the Step-CA Root CA allows applications such as web browsers, `curl`, and other TLS clients to trust certificates issued by your Step-CA.

```bash
step certificate install --all ~/.step/certs/root_ca.crt
```

### Verify the Root CA Installation

After installing the Root CA, retry the same request.

```bash
curl -u user:password https://file-server.ota-lab.local:9000/downloads/
```

If the server certificate is valid and was issued by your Step-CA, the TLS connection should succeed without certificate trust errors, and the file server directory listing (or index page) should be displayed.

### Enable Docker

```bash
sudo systemctl enable docker     # Start Docker automatically during boot
sudo systemctl start docker      # Start Docker service
```

Add permission for the current user to use Docker.

```bash
sudo usermod -aG docker $USER    # Add current user to docker group
```

reboot/logout and login for the Docker group membership to take effect.
Reconnect using SSH after the reboot.

```bash
sudo reboot
```

## Connect to Kubernetes Cluster and Verify Access

Create the kube configuration directory.

```bash
mkdir -p ~/.kube      # Create Kubernetes configuration directory
```

Download the Kubernetes configuration file from the file server.

```bash
curl -u user:password https://file-server.ota-lab.local:9000/downloads/kube_config > ~/.kube/config
```

Verify connectivity.

```bash
kubectl get nodes      # Verify cluster connectivity
```

You should see the available cluster nodes.

---

## Test the Docker Registry Connection

Pull the hello-world image.

```bash
docker pull hello-world     # Download test image
```

Tag it for the local registry.

```bash
docker tag hello-world registry.ota-lab.local/hello-world:latest
```

Push the image.

```bash
docker push registry.ota-lab.local/hello-world:latest
```

Verify the image can be deployed to kubernetes

```bash
kubectl create deployment test-deployment --image=registry.ota-lab.local/hello-world:latest
kubectl get pods
```

You should see test-deployment-xxxx-xxx pod in completed state

```text
test-deployment-6b5bd9f94b-lzgdg   0/1     Completed   1 (3s ago)   4s
```

Cleanup the test deployment

```bash
kubectl delete deployment test-deployment
```
