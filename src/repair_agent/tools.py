"""Allowlisted agent tools: list, read, search, patch, and visible tests.

The tool dispatcher arrives with the repair loop in week 2. Patch checks live
in ``patch_policy`` and container execution lives in ``runner``.
"""


def dispatch(_name: str, _arguments: object) -> object:
    raise NotImplementedError("Tool dispatch is implemented in week 2.")
