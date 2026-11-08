"""Typed NFC errors. Pure module: no CLI, USB or PC/SC imports.

Every error carries a human ``remediation`` string so the CLI/MCP/web layers can
render an ``error:`` + ``hint:`` pair without knowing the failure details.
"""

from __future__ import annotations


class NfcError(Exception):
    """Base class for every NFC/reader failure surfaced to callers."""

    default_message = "NFC operation failed"
    default_remediation = "re-run with --trace to see the reader exchange"

    def __init__(self, message: str | None = None, remediation: str | None = None):
        super().__init__(message or self.default_message)
        self.message = message or self.default_message
        self.remediation = remediation or self.default_remediation

    def __str__(self) -> str:
        return self.message


class UsbUnavailable(NfcError):
    default_message = "USB support is not available (pyusb or libusb-1.0 missing)"
    default_remediation = "pip install 'nfc-cli[usb]' and make sure libusb-1.0 is installed"


class ReaderNotFound(NfcError):
    default_message = "no ACR1552 reader (072f:2303) found"
    default_remediation = (
        "plug in the reader and check `lsusb`; use --reader INDEX or BUS:ADDR to pick one"
    )


class ReaderPermission(NfcError):
    default_message = "permission denied opening the USB reader"
    default_remediation = (
        "install the udev rule from docs/udev/60-nfc-cli-acr1552.rules "
        "(see docs/hardware.md), then replug the reader"
    )


class ReaderBusy(NfcError):
    default_message = "the reader is in use by another process"
    default_remediation = (
        "wait for the other nfc-cli process to finish, or stop the process holding the "
        "reader (e.g. pcscd)"
    )


class NoCard(NfcError):
    default_message = "no card on the reader"
    default_remediation = "place a tag on the reader (or pass --wait SECONDS)"


class CardRemoved(NfcError):
    default_message = "the card was removed during the operation"
    default_remediation = "hold the tag still on the reader and retry"


class AuthFailed(NfcError):
    default_message = "authentication with the card failed"
    default_remediation = "supply the right key (--key or --keys-file) for this sector"


class ApduError(NfcError):
    """A card answered with a non-success status word."""

    default_remediation = "check the command against the tag type; re-run with --trace"

    def __init__(self, sw: bytes | int, message: str | None = None, remediation: str | None = None):
        if isinstance(sw, int):
            sw = sw.to_bytes(2, "big")
        self.sw = bytes(sw)
        super().__init__(message or f"card returned status {self.sw.hex().upper()}", remediation)
