"""Single application version source."""

__version__ = "0.1.0-rc1"
APP_NAME = "Clipper / Stadium Signal"


def application_version() -> str:
    return __version__


def version_info() -> dict:
    return {"name": APP_NAME, "version": __version__}