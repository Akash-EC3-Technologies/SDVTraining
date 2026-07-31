from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .exceptions import FlashError, InfoError, ToolNotFoundError, VerifyError


@dataclass(frozen=True)
class FlashResult:
    returncode: int
    stdout: str
    stderr: str
    command: tuple[str, ...]


def _is_elf(path: Path) -> bool:
    return path.suffix.lower() in {".elf", ".out", ".axf"}


def _is_bin(path: Path) -> bool:
    return path.suffix.lower() == ".bin"


def _combine_output(proc: subprocess.CompletedProcess[str]) -> str:
    parts = []
    if proc.stdout:
        parts.append(proc.stdout.rstrip())
    if proc.stderr:
        parts.append(proc.stderr.rstrip())
    return "\n".join(parts).strip()


class CubeProgrammerBackend:
    """
    Thin wrapper around STM32CubeProgrammer CLI.

    Supports:
    - SWD via ST-LINK
    - UART via the STM32 ROM bootloader or other supported bootloader paths

    """

    def __init__(
        self,
        *,
        cli: str = "STM32_Programmer_CLI",
        interface: str = "swd",
        port: str | None = None,
        baudrate: int = 115200,
        flash_address: int = 0x08000000,
        verbose: int = 0,
    ) -> None:
        self.cli = cli
        self.interface = interface.lower().strip()
        self.port = port
        self.baudrate = baudrate
        self.flash_address = flash_address
        self.verbose = verbose

        if shutil.which(self.cli) is None:
            raise ToolNotFoundError(f"{self.cli!r} was not found in PATH.")

        if self.interface not in {"swd", "uart"}:
            raise ValueError("interface must be 'swd' or 'uart'")

        if self.interface == "uart" and not self.port:
            raise ValueError("port is required when interface='uart'")

    def _connect_arg(self) -> str:
        if self.interface == "swd":
            return "port=SWD"
        return f"port={self.port} br={self.baudrate}"

    def _base_cmd(self) -> list[str]:
        cmd = [self.cli, "-c", self._connect_arg()]
        if self.verbose > 0:
            cmd += ["-vb", str(self.verbose)]
        return cmd

    def _run(self, args: list[str], *, check: bool = True) -> FlashResult:
        cmd = self._base_cmd() + args
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
        )
        result = FlashResult(
            returncode=proc.returncode,
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
            command=tuple(cmd),
        )

        if check and proc.returncode != 0:
            raise FlashError(
                "STM32CubeProgrammer CLI failed\n"
                f"Command: {' '.join(cmd)}\n"
                f"Return code: {proc.returncode}\n"
                f"STDOUT:\n{result.stdout}\n"
                f"STDERR:\n{result.stderr}"
            )
        return result

    def info(self) -> str:
        """
        Connect to the target and return the CLI output.
        """
        result = self._run([], check=False)
        if result.returncode != 0:
            raise InfoError(
                "Failed to query device info\n"
                f"Command: {' '.join(result.command)}\n"
                f"Return code: {result.returncode}\n"
                f"STDOUT:\n{result.stdout}\n"
                f"STDERR:\n{result.stderr}"
            )
        return result.stdout

    def flash(
        self,
        firmware: str | os.PathLike[str],
        *,
        address: int | None = None,
        reset: bool = False,
    ) -> str:
        """
        Program a binary/ELF image into internal Flash.

        For raw .bin files, a start address is required (default: 0x08000000).
        For ELF/AXF/OUT files, CubeProgrammer can use the embedded addresses.
        """
        fw = Path(firmware).expanduser().resolve()
        if not fw.exists():
            raise FileNotFoundError(f"Firmware file not found: {fw}")

        cmd: list[str] = []

        if _is_bin(fw):
            addr = self.flash_address if address is None else address
            cmd += ["-w", str(fw), hex(addr)]
        else:
            cmd += ["-w", str(fw)]

        if reset:
            cmd += ["-rst"]

        result = self._run(cmd, check=False)
        if result.returncode != 0:
            raise FlashError(
                "Flash operation failed\n"
                f"Command: {' '.join(result.command)}\n"
                f"Return code: {result.returncode}\n"
                f"STDOUT:\n{result.stdout}\n"
                f"STDERR:\n{result.stderr}"
            )
        return result.stdout

    def _convert_elf_to_bin(self, elf_path: Path) -> Path:
        objcopy = shutil.which("arm-none-eabi-objcopy")
        if objcopy is None:
            raise ToolNotFoundError(
                "arm-none-eabi-objcopy is required to verify ELF files."
            )

        tmp_dir = Path(tempfile.mkdtemp(prefix="stm32_verify_"))
        out_bin = tmp_dir / (elf_path.stem + ".bin")
        proc = subprocess.run(
            [objcopy, "-O", "binary", str(elf_path), str(out_bin)],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise VerifyError(
                "Failed to convert ELF to BIN for verification\n"
                f"STDOUT:\n{proc.stdout}\n"
                f"STDERR:\n{proc.stderr}"
            )
        return out_bin

    def verify(
        self,
        firmware: str | os.PathLike[str],
        *,
        address: int | None = None,
    ) -> str:
        """
        Read back flash and compare it against the given image.

        Works by:
          1. converting ELF/AXF/OUT to a raw BIN when needed,
          2. reading the flashed region back with STM32CubeProgrammer,
          3. comparing the files byte-for-byte.

        For raw BIN files, the default address is 0x08000000.
        """
        fw = Path(firmware).expanduser().resolve()
        if not fw.exists():
            raise FileNotFoundError(f"Firmware file not found: {fw}")

        if _is_elf(fw):
            ref_bin = self._convert_elf_to_bin(fw)
            compare_address = self.flash_address if address is None else address
        elif _is_bin(fw):
            ref_bin = fw
            compare_address = self.flash_address if address is None else address
        else:
            # Treat unknown extensions as raw binaries.
            ref_bin = fw
            compare_address = self.flash_address if address is None else address

        expected = ref_bin.read_bytes()
        if not expected:
            raise VerifyError("Reference image is empty; nothing to verify.")

        with tempfile.TemporaryDirectory(prefix="stm32_readback_") as tmp:
            readback = Path(tmp) / "readback.bin"
            # -r reads and uploads the device memory content into a binary file.
            # Official docs: -r <start_address> <size> <file_path>.
            self._run(
                ["-r", hex(compare_address), hex(len(expected)), str(readback)],
                check=True,
            )

            actual = readback.read_bytes()

        if actual[: len(expected)] != expected:
            raise VerifyError(
                "Verification failed: readback does not match the firmware image."
            )
        return "Verification OK"

    def reset(self) -> str:
        """
        Start execution at the given address.
        If no address is provided, use the flash base address.
        """

        result = self._run(["-rst"], check=False)
        if result.returncode != 0:
            raise FlashError(
                "Start failed\n"
                f"Command: {' '.join(result.command)}\n"
                f"Return code: {result.returncode}\n"
                f"STDOUT:\n{result.stdout}\n"
                f"STDERR:\n{result.stderr}"
            )
        return result.stdout

    def update(
        self,
        firmware: str | os.PathLike[str],
        *,
        address: int | None = None,
    ) -> str:
        """
        Flash, verify, and start the application.
        """
        flash_out = self.flash(firmware, address=address, reset=False)
        verify_out = self.verify(firmware, address=address)
        start_out = self.reset()
        return "\n".join(
            part
            for part in [flash_out.strip(), verify_out.strip(), start_out.strip()]
            if part
        )
