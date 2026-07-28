"""Shared pytest configuration.

No logging monkey patches are required: logging safety is implemented
inside backend.core.logging.
"""


def _normalize_httpx_record(record):
    """Compatibility helper retained for the legacy httpx logging test."""
    if record.name != "httpx" or not isinstance(record.args, tuple):
        return record
    if len(record.args) >= 4 and isinstance(record.args[3], str):
        try:
            normalized = list(record.args)
            normalized[3] = int(normalized[3])
            record.args = tuple(normalized)
        except ValueError:
            pass
    return record
