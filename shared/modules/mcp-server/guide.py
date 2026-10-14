"""Backwards-compatible alias for :mod:`common.guide`.

The answering guide moved to ``shared/modules/common`` when the web admin's
chat needed the same text: one guide for every assistant, the in-UI one
included. This shim keeps ``import guide`` working for the server, the
connector and their tests.
"""

from __future__ import annotations

from common.guide import ASSISTANT_INSTRUCTIONS, GUIDE

__all__ = ["ASSISTANT_INSTRUCTIONS", "GUIDE"]
