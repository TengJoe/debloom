"""Resolve user directories using the XDG default and absolute-path rules."""

import os
from pathlib import Path

DEFAULTS = {
    "config": ("XDG_CONFIG_HOME", ".config"),
    "data": ("XDG_DATA_HOME", ".local/share"),
    "state": ("XDG_STATE_HOME", ".local/state"),
}


def xdg_home(kind):
    variable, fallback = DEFAULTS[kind]
    value = os.environ.get(variable)
    if value:
        directory = Path(value)
        if directory.is_absolute():
            return directory
    return Path.home() / fallback
