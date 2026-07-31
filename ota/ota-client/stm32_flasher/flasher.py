from __future__ import annotations

from pathlib import Path

from .cubeprogrammer import CubeProgrammerBackend


class STM32Flasher:
    """
    Public facade used by the TCU application.
    """

    def __init__(
        self,
        port: str | None = None,
        *,
        interface: str = "swd",
        baudrate: int = 115200,
        cli: str = "STM32_Programmer_CLI",
        flash_address: int = 0x08000000,
        verbose: int = 0,
    ) -> None:
        self.backend = CubeProgrammerBackend(
            cli=cli,
            interface=interface,
            port=port,
            baudrate=baudrate,
            flash_address=flash_address,
            verbose=verbose,
        )

    def info(self) -> str:
        return self.backend.info()

    def flash(
        self, firmware: str | Path, *, address: int | None = None, reset: bool = False
    ) -> str:
        return self.backend.flash(firmware, address=address, reset=reset)

    def verify(self, firmware: str | Path, *, address: int | None = None) -> str:
        return self.backend.verify(firmware, address=address)

    def reset(self) -> str:
        return self.backend.reset()

    def update(self, firmware: str | Path, *, address: int | None = None) -> str:
        return self.backend.update(firmware, address=address)
