"""Konfiguration und Secret-Handling.

Secrets werden NIE im Code gehalten, sondern aus Umgebungsvariablen
oder einer .env-Datei gelesen:

    AGENDA_USERNAME       Login-Benutzer (E-Mail)
    AGENDA_PASSWORD       Passwort
    AGENDA_TOTP_SECRET    TOTP-Secret (Base32, wie in Apple Passwords hinterlegt)
    AGENDA_TOKEN_CACHE    Optional: Pfad für Token-Cache (Default: ~/.cache/agenda/tokens.json)

Die .env-Datei wird – falls vorhanden – aus dem aktuellen Verzeichnis oder
dem Projektwurzelverzeichnis geladen. Bestehende Umgebungsvariablen haben
Vorrang und werden nicht überschrieben.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


# ---- Endpunkte / feste Portal-Parameter (nicht geheim) ---------------------

AUTH_BASE = "https://agenda-auth.de/realms/kunden"
PORTAL_BASE = "https://agenda-unternehmens-portal.de/Unternehmensportal"
CLIENT_ID = "unpmobil"
REDIRECT_URI = "https://agenda-unternehmens-portal.de/Unternehmensportal/"
SCOPE = "openid"

# Eigener, erkennbarer User-Agent statt des requests-Library-Defaults
# ("python-requests/x.y.z") - Transparenz, dass hier ein inoffizielles
# Automatisierungs-Tool zugreift, kein getarnter Browser/App-Client.
USER_AGENT = (
    "agenda-unternehmensportal-automation/0.1 "
    "(+https://github.com/vincentadomat/Agenda-Unternehmens-Portal-Automation)"
)

DEFAULT_TOKEN_CACHE = Path.home() / ".cache" / "agenda" / "tokens.json"


def _load_dotenv() -> None:
    """Lädt Schlüssel=Wert-Paare aus einer .env-Datei in os.environ.

    Sucht .env im aktuellen Verzeichnis und im Projektwurzelverzeichnis
    (eine Ebene über diesem Paket). Vorhandene Variablen bleiben unangetastet.
    """
    candidates = [
        Path.cwd() / ".env",
        Path(__file__).resolve().parent.parent / ".env",
    ]
    seen: set[Path] = set()
    for path in candidates:
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


@dataclass
class Config:
    username: str
    password: str
    totp_secret: str
    token_cache: Path

    @property
    def has_credentials(self) -> bool:
        return bool(self.username and self.password and self.totp_secret)


def load_config() -> Config:
    """Liest die Konfiguration aus Umgebung/.env und validiert sie."""
    _load_dotenv()

    username = os.environ.get("AGENDA_USERNAME", "").strip()
    password = os.environ.get("AGENDA_PASSWORD", "").strip()
    totp_secret = os.environ.get("AGENDA_TOTP_SECRET", "").replace(" ", "").strip()

    cache_env = os.environ.get("AGENDA_TOKEN_CACHE", "").strip()
    token_cache = Path(cache_env) if cache_env else DEFAULT_TOKEN_CACHE

    missing = [
        name
        for name, val in (
            ("AGENDA_USERNAME", username),
            ("AGENDA_PASSWORD", password),
            ("AGENDA_TOTP_SECRET", totp_secret),
        )
        if not val
    ]
    if missing:
        raise RuntimeError(
            "Fehlende Konfiguration: "
            + ", ".join(missing)
            + ". Bitte per Umgebungsvariable oder .env-Datei setzen "
            "(siehe .env.example)."
        )

    return Config(
        username=username,
        password=password,
        totp_secret=totp_secret,
        token_cache=token_cache,
    )
