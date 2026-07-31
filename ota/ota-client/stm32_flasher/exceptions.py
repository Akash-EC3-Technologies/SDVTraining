"""Custom exceptions for STM32 flashing operations."""

class STM32FlasherError(Exception):
    """Base class for flashing-related failures."""


class FlashError(STM32FlasherError):
    """Raised when programming the device fails."""


class VerifyError(STM32FlasherError):
    """Raised when readback verification fails."""


class InfoError(STM32FlasherError):
    """Raised when device info retrieval fails."""


class ToolNotFoundError(STM32FlasherError):
    """Raised when STM32CubeProgrammer CLI or required tools are missing."""
