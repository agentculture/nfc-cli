"""``nfc-cli ndef`` — registry-generated verbs (see :mod:`nfc_cli.cli._commands._nfcgroup`)."""

from __future__ import annotations

import argparse

from nfc_cli.cli._commands._nfcgroup import register_noun


def register(sub: argparse._SubParsersAction) -> None:
    register_noun(sub, "ndef")
