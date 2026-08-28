"""Keycloak OIDC-Login mit PKCE und TOTP-Zweitfaktor.

Rekonstruiert aus HAR-Analyse des Agenda Unternehmensportals:
  1. GET  authorize  (PKCE S256)                 -> Login-HTML
  2. POST username/password an das Formular      -> OTP-HTML (oder 302)
  3. POST TOTP-Code an das OTP-Formular           -> 302 mit ?code=...
  4. POST token (authorization_code + verifier)   -> access_/refresh_/id_token

Der refresh_token (Gültigkeit ~24h) wird gecacht, damit nicht bei jedem
Aufruf ein neuer TOTP-Code fällig wird.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

import pyotp
import requests

from .config import (
    AUTH_BASE,
    CLIENT_ID,
    Config,
    REDIRECT_URI,
    SCOPE,
)


class AuthError(RuntimeError):
    """Fehler im Login-/Token-Flow."""


# ---------------------------------------------------------------------------
# HTML-Formular-Parser (findet das erste <form> und seine Felder)
# ---------------------------------------------------------------------------

class _FormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.action: Optional[str] = None
        self.fields: dict[str, str] = {}
        self._in_form = False
        self._done = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        a = dict(attrs)
        if tag == "form" and not self._done:
            self._in_form = True
            self.action = a.get("action")
        elif tag == "input" and self._in_form:
            name = a.get("name")
            if name:
                self.fields[name] = a.get("value") or ""

    def handle_endtag(self, tag: str) -> None:
        if tag == "form" and self._in_form:
            self._in_form = False
            self._done = True


def _parse_form(html: str) -> tuple[str, dict[str, str]]:
    p = _FormParser()
    p.feed(html)
    if not p.action:
        raise AuthError("Kein Login-Formular in der Keycloak-Antwort gefunden.")
    return p.action, p.fields


def _pkce_pair() -> tuple[str, str]:
    """Erzeugt (code_verifier, code_challenge) für PKCE S256."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


# ---------------------------------------------------------------------------
# Token-Cache
# ---------------------------------------------------------------------------

class _TokenStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> Optional[dict]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def save(self, tokens: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(tokens), encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Authenticator
# ---------------------------------------------------------------------------

class Authenticator:
    """Besorgt einen gültigen access_token – aus Cache, per Refresh oder
    per vollständigem Login (Passwort + TOTP)."""

    # Sicherheitsmarge, bevor ein Token als abgelaufen gilt (Sekunden)
    LEEWAY = 30

    def __init__(self, config: Config, session: Optional[requests.Session] = None) -> None:
        self.config = config
        self.session = session or requests.Session()
        self.store = _TokenStore(config.token_cache)

    # -- öffentliche API ----------------------------------------------------

    def access_token(self, force_login: bool = False) -> str:
        if not force_login:
            cached = self.store.load()
            if cached:
                token = self._from_cache(cached)
                if token:
                    return token
        return self._full_login()

    # -- interne Schritte ---------------------------------------------------

    def _from_cache(self, cached: dict) -> Optional[str]:
        now = time.time()
        if cached.get("access_expires_at", 0) - self.LEEWAY > now:
            return cached["access_token"]
        # access_token abgelaufen -> per refresh_token erneuern
        if cached.get("refresh_expires_at", 0) - self.LEEWAY > now:
            try:
                return self._refresh(cached["refresh_token"])
            except AuthError:
                pass
        return None

    def _refresh(self, refresh_token: str) -> str:
        resp = self.session.post(
            f"{AUTH_BASE}/protocol/openid-connect/token",
            data={
                "grant_type": "refresh_token",
                "scope": SCOPE,
                "refresh_token": refresh_token,
                "client_id": CLIENT_ID,
            },
            timeout=30,
        )
        if resp.status_code != 200:
            raise AuthError(f"Token-Refresh fehlgeschlagen ({resp.status_code}).")
        return self._store_tokens(resp.json())

    def _full_login(self) -> str:
        verifier, challenge = _pkce_pair()
        state = secrets.token_urlsafe(24)
        nonce = secrets.token_urlsafe(24)

        # 1) authorize -> Login-Seite
        resp = self.session.get(
            f"{AUTH_BASE}/protocol/openid-connect/auth",
            params={
                "response_type": "code",
                "client_id": CLIENT_ID,
                "state": state,
                "redirect_uri": REDIRECT_URI,
                "scope": SCOPE,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "nonce": nonce,
            },
            timeout=30,
        )
        if resp.status_code != 200:
            raise AuthError(f"Authorize-Request fehlgeschlagen ({resp.status_code}).")

        # 2) Passwort-Formular abschicken
        action, _ = _parse_form(resp.text)
        resp = self.session.post(
            action,
            data={
                "username": self.config.username,
                "password": self.config.password,
                "credentialId": "",
            },
            allow_redirects=False,
            timeout=30,
        )

        code = self._extract_code(resp)
        if code is None:
            # 3) Kein direkter Redirect -> OTP-Schritt erwartet
            if resp.status_code != 200:
                raise AuthError(
                    f"Passwort-Login fehlgeschlagen ({resp.status_code}). "
                    "Zugangsdaten prüfen."
                )
            code = self._submit_otp(resp.text)

        # 4) Code gegen Tokens tauschen
        return self._exchange_code(code, verifier)

    def _submit_otp(self, html: str) -> str:
        action, fields = _parse_form(html)
        otp = pyotp.TOTP(self.config.totp_secret).now()

        # Das kbTheme nutzt Einzelziffern (digit00..05) + ein zusammengesetztes
        # otp-Feld. Wir befüllen beides, plus vorhandene Hidden-Felder.
        data = dict(fields)
        for i, digit in enumerate(otp):
            data[f"digit{i:02d}"] = digit
        data["otp"] = otp
        data.setdefault("login", "")

        resp = self.session.post(action, data=data, allow_redirects=False, timeout=30)
        code = self._extract_code(resp)
        if code is None:
            raise AuthError(
                f"OTP-Login fehlgeschlagen ({resp.status_code}). "
                "TOTP-Secret/Uhrzeit prüfen."
            )
        return code

    @staticmethod
    def _extract_code(resp: requests.Response) -> Optional[str]:
        """Liest den Authorization-Code aus dem Location-Redirect (302)."""
        if resp.status_code not in (302, 303):
            return None
        location = resp.headers.get("Location", "")
        query = parse_qs(urlparse(location).query)
        codes = query.get("code")
        return codes[0] if codes else None

    def _exchange_code(self, code: str, verifier: str) -> str:
        resp = self.session.post(
            f"{AUTH_BASE}/protocol/openid-connect/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT_URI,
                "code_verifier": verifier,
                "client_id": CLIENT_ID,
            },
            timeout=30,
        )
        if resp.status_code != 200:
            raise AuthError(f"Token-Exchange fehlgeschlagen ({resp.status_code}).")
        return self._store_tokens(resp.json())

    def _store_tokens(self, payload: dict) -> str:
        now = time.time()
        tokens = {
            "access_token": payload["access_token"],
            "refresh_token": payload.get("refresh_token", ""),
            "id_token": payload.get("id_token", ""),
            "access_expires_at": now + payload.get("expires_in", 0),
            "refresh_expires_at": now + payload.get("refresh_expires_in", 0),
        }
        self.store.save(tokens)
        return tokens["access_token"]
