"""Registry der Portal-Funktionen.

Jede Funktion registriert sich mit einem Namen (der von aussen – CLI, n8n –
übergeben wird). So lassen sich später weitere Portal-Funktionen ergänzen,
ohne den Aufrufweg zu ändern.

    from agenda.functions import get_function, list_functions
"""

from __future__ import annotations

from typing import Callable, Dict

# name -> callable(client, args) -> dict
_REGISTRY: Dict[str, Callable] = {}


def register(name: str) -> Callable:
    def deco(fn: Callable) -> Callable:
        _REGISTRY[name] = fn
        return fn
    return deco


def get_function(name: str) -> Callable:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise KeyError(
            f"Unbekannte Funktion '{name}'. Verfügbar: {', '.join(sorted(_REGISTRY))}"
        )


def list_functions() -> list[str]:
    return sorted(_REGISTRY)


# Funktionen importieren, damit sie sich registrieren.
from . import belegupload  # noqa: E402,F401
from . import editdocument  # noqa: E402,F401
