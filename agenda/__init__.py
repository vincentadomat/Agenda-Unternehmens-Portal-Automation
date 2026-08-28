"""Agenda Unternehmensportal – Automatisierungs-Client.

Reverse-engineerter Zugriff auf das Agenda Unternehmensportal
(Keycloak OIDC + PKCE Login, digibel-Beleg-Upload).

Öffentliche API:
    from agenda import AgendaClient, load_config
"""

from .config import Config, load_config
from .client import AgendaClient
from .auth import AuthError

__all__ = ["AgendaClient", "Config", "load_config", "AuthError"]
__version__ = "0.1.0"
