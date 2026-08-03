"""Configuration error type used across the validated configuration layer."""

from __future__ import annotations


class ConfigurationError(Exception):
    """Raised when project configuration is invalid or a requested
    taxonomy, template, profile, or structured selection cannot be resolved.

    The message always includes the complete failing field path so operators
    (and automated validation) can pinpoint the offending key.
    """