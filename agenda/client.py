"""AgendaClient – authentifizierter Zugriff auf die Portal-REST-API.

Kümmert sich um:
  * gültigen Bearer-Token (via Authenticator)
  * Auflösung der IDs: orgId (aus id_token) -> office(s) -> Mandanten -> Ordner
  * generische GET/POST-Helfer gegen die /rest-API

Die eigentlichen Funktionen (z. B. Beleg-Upload) liegen in agenda.functions
und bekommen einen AgendaClient übergeben.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any, Optional

import requests

from .auth import AuthError, Authenticator
from .config import Config, PORTAL_BASE


def _decode_jwt_claims(token: str) -> dict:
    """Dekodiert den Payload eines JWT ohne Signaturprüfung."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except (IndexError, ValueError):
        return {}


@dataclass
class Mandator:
    id: str
    number: int
    name: str
    office_id: str
    office_name: str


@dataclass
class Folder:
    id: str
    name: str
    raw: dict


class AgendaClient:
    REST = f"{PORTAL_BASE}/rest"

    def __init__(self, config: Config) -> None:
        self.config = config
        self.session = requests.Session()
        self._auth = Authenticator(config, self.session)
        self._access_token: Optional[str] = None
        self._org_id: Optional[str] = None

    # -- Auth --------------------------------------------------------------

    def login(self, force: bool = False) -> None:
        """Stellt einen gültigen Token sicher und setzt den Auth-Header."""
        self._access_token = self._auth.access_token(force_login=force)
        self.session.headers["Authorization"] = f"Bearer {self._access_token}"

    def _ensure_login(self) -> None:
        if not self._access_token:
            self.login()

    @property
    def org_id(self) -> str:
        """Organisation-ID – steckt als client_id-Claim im id_token."""
        if self._org_id:
            return self._org_id
        self._ensure_login()
        cached = self._auth.store.load() or {}
        claims = _decode_jwt_claims(cached.get("id_token", ""))
        org = claims.get("client_id")
        if not org:
            raise AuthError("orgId (client_id-Claim) nicht im id_token gefunden.")
        self._org_id = org
        return org

    # -- generische REST-Helfer -------------------------------------------

    def get(self, path: str, **kw: Any) -> requests.Response:
        return self._request("GET", path, **kw)

    def post(self, path: str, **kw: Any) -> requests.Response:
        return self._request("POST", path, **kw)

    def _request(self, method: str, path: str, **kw: Any) -> requests.Response:
        self._ensure_login()
        url = path if path.startswith("http") else f"{self.REST}/{path.lstrip('/')}"
        kw.setdefault("timeout", 60)
        resp = self.session.request(method, url, **kw)
        # Token evtl. serverseitig invalidiert -> einmal frisch einloggen
        if resp.status_code == 401:
            self.login(force=True)
            resp = self.session.request(method, url, **kw)
        return resp

    # -- Auflösung: Offices / Mandanten / Ordner --------------------------

    def offices(self) -> list[dict]:
        resp = self.get(f"organization/{self.org_id}/offices")
        resp.raise_for_status()
        return resp.json()

    def mandators(self) -> list[Mandator]:
        """Alle Mandanten über alle Kanzleien (offices) hinweg."""
        result: list[Mandator] = []
        for office in self.offices():
            office_id = office["id"]
            resp = self.get(f"organization/{self.org_id}/{office_id}/mandators")
            resp.raise_for_status()
            for m in resp.json():
                result.append(
                    Mandator(
                        id=m["id"],
                        number=m.get("mandatorNumber"),
                        name=m.get("mandatorName", ""),
                        office_id=office_id,
                        office_name=office.get("officeName", ""),
                    )
                )
        return result

    def find_mandator(self, ref: str) -> Mandator:
        """Findet einen Mandanten per Nummer, UUID oder (Teil-)Name."""
        ref = str(ref).strip()
        mandators = self.mandators()
        # exakte Nummer
        if ref.isdigit():
            for m in mandators:
                if str(m.number) == ref:
                    return m
        # exakte UUID
        for m in mandators:
            if m.id == ref:
                return m
        # Name (case-insensitive, Teilstring)
        low = ref.lower()
        matches = [m for m in mandators if low in m.name.lower()]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            names = ", ".join(f"{m.number} {m.name}" for m in matches)
            raise LookupError(f"Mandant '{ref}' ist mehrdeutig: {names}")
        raise LookupError(f"Mandant '{ref}' nicht gefunden.")

    def folders(self, mandator_id: str) -> list[Folder]:
        resp = self.get(f"digibel/{mandator_id}/MDT/folders")
        resp.raise_for_status()
        data = resp.json()
        # Ordner können verschachtelt sein – flach durchsuchbar machen.
        return list(_flatten_folders(data))

    def find_folder(self, mandator_id: str, ref: str) -> Folder:
        """Findet einen Upload-Ordner per Name (Teilstring) oder UUID."""
        ref = str(ref).strip()
        folders = self.folders(mandator_id)
        for f in folders:
            if f.id == ref:
                return f
        low = ref.lower()
        exact = [f for f in folders if f.name.lower() == low]
        if len(exact) == 1:
            return exact[0]
        partial = [f for f in folders if low in f.name.lower()]
        if len(partial) == 1:
            return partial[0]
        if len(partial) > 1:
            names = ", ".join(sorted(f.name for f in partial))
            raise LookupError(f"Ordner '{ref}' ist mehrdeutig: {names}")
        available = ", ".join(sorted(f.name for f in folders))
        raise LookupError(f"Ordner '{ref}' nicht gefunden. Verfügbar: {available}")

    # -- Dokumente je Bearbeitungsschritt -----------------------------------
    # Live gegen die Produktiv-API verifiziert (2026-08-28). Erfordert für
    # "archive" das Recht MANAGE_DOC_ARCHIVE (muss ggf. vom Buchhalter/
    # Portal-Admin für den Nutzer freigeschaltet werden, sonst 403).

    def draft_documents(self, mandator_id: str, folder_id: str) -> list[dict]:
        """Belege im Status 'Belegseiten ordnen' (SORT_DOCUMENT)."""
        resp = self.get(f"digibel/{mandator_id}/{folder_id}/draft-documents")
        resp.raise_for_status()
        return resp.json() or []

    def edit_documents(
        self, mandator_id: str, folder_id: str, start: int = 0, length: int = 1000
    ) -> list[dict]:
        """Belege im Status 'Prüfen und Zahlen' (VERIFY_AND_PAY)."""
        resp = self.get(
            f"digibel/{mandator_id}/{folder_id}/edit-documents",
            params={"sort-asc": "true", "start": start, "length": length},
        )
        resp.raise_for_status()
        return resp.json() or []

    def archive_documents(
        self, mandator_id: str, folder_id: str, start: int = 0, length: int = 1000
    ) -> list[dict]:
        """Bereits bereitgestellte/verbuchte Belege (Belegarchiv). Braucht
        das Recht MANAGE_DOC_ARCHIVE, sonst HTTP 403."""
        resp = self.get(
            f"digibel/{mandator_id}/{folder_id}/archive-documents",
            params={"sort-asc": "true", "start": start, "length": length},
        )
        resp.raise_for_status()
        return resp.json() or []

    # -- Download (live verifiziert, 2026-08-28: Original-Datei kommt
    # byte-identisch zurück) ------------------------------------------------

    def create_download_token(self, document_ident: str, lifespan: str = "SHORT") -> str:
        """Kurzlebiges Download-Token für einen Beleg (JWT). `lifespan`:
        SHORT/MEDIUM/LONG. Wird als ?token=... an die Download-URL gehängt
        (kein Bearer-Header, das Original-Frontend macht es genauso)."""
        resp = self.post(
            "security/download-token", params={"lifespan": lifespan}, json=document_ident
        )
        resp.raise_for_status()
        return resp.json()["accessToken"]

    def download_source_files(self, mandator_id: str, document_ident: str) -> requests.Response:
        """Original-Datei(en) eines Belegs ('Original herunterladen' im
        Portal). Liefert die Roh-Response (Dateiname steht im
        Content-Disposition-Header, resp.content ist der Dateiinhalt)."""
        token = self.create_download_token(document_ident, lifespan="SHORT")
        resp = self.get(
            f"digibel/{mandator_id}/documents/{document_ident}/source-files",
            params={"token": token},
        )
        try:
            resp.raise_for_status()
        except requests.HTTPError as exc:
            # Fehlermeldung ohne Download-Token (URL enthält ?token=...) neu
            # werfen, damit der Token nicht in Logs/CLI-Ausgaben landet.
            raise requests.HTTPError(
                f"{resp.status_code} beim Herunterladen von Dokument "
                f"'{document_ident}': {resp.reason}",
                response=resp,
            ) from exc
        return resp

    # -- Beleg bearbeiten (Kommentar + Buchungsvorschlag) -------------------
    # Live gegen die Produktiv-API verifiziert (2026-08-28, HAR agenda3.har):
    # GET liefert das volle Dokumentobjekt (inkl. `optlock` für optimistisches
    # Locking); save-edited-document erwartet dasselbe Objekt zurück, nur mit
    # geänderten Feldern - kein partielles Patch.

    def get_document(self, mandator_id: str, document_ident: str) -> dict:
        """Volles Dokumentobjekt (state, accountingRecord, ocrValues, file,
        notice, optlock, ...) - Basis für save_edited_document()."""
        resp = self.get(f"digibel/{mandator_id}/documents/{document_ident}")
        resp.raise_for_status()
        return resp.json()

    def save_edited_document(
        self, mandator_id: str, document: dict, verify: bool = False
    ) -> dict:
        """Speichert ein (per get_document geladenes und angepasstes)
        Dokumentobjekt. `verify`: unbestätigte Bedeutung - im beobachteten
        Live-Fall wurde immer `verify=false` verwendet (reines Speichern,
        ohne den Beleg in den nächsten Bearbeitungsschritt zu verschieben);
        `true` bitte nur bewusst/nach Rücksprache verwenden."""
        resp = self.post(
            f"digibel/{mandator_id}/save-edited-document",
            params={"verify": "true" if verify else "false"},
            json=document,
        )
        resp.raise_for_status()
        return resp.json()

    # -- Belege gezielt freigeben ("Belege bereitstellen") ------------------
    # ACHTUNG: NICHT durch eine HAR-Aufnahme bestätigt, nur aus dem
    # minifizierten DigibelService-JS abgeleitet (2026-08-28):
    #   forwardDocuments(t,i){let n=i.map(s=>s.id),a=this.prepareUrl()
    #     .path(t).path("forward-by-ids").param("next-step",PROVIDE_DOCUMENT);
    #     return this.httpService.post(a,n).pipe(_(s=>s.count))}
    # -> POST digibel/{mandatorId}/{folderId}/forward-by-ids
    #      ?next-step=PROVIDE_DOCUMENT   Body: [documentIdent, ...]
    #      Response vermutlich {"count": N}
    # Übermittelt die genannten Belege sofort an den Buchhalter - endgültig,
    # nicht rückholbar (wie --next-step PROVIDE_DOCUMENT beim Upload). Vor
    # jedem Einsatz gegen echte Belege unbedingt mit dem Nutzer absprechen.

    def forward_documents(
        self, mandator_id: str, folder_id: str, document_idents: list[str]
    ) -> dict:
        """Gibt die genannten Belege direkt an den Buchhalter frei
        (next-step=PROVIDE_DOCUMENT). NICHT rückholbar."""
        resp = self.post(
            f"digibel/{mandator_id}/{folder_id}/forward-by-ids",
            params={"next-step": "PROVIDE_DOCUMENT"},
            json=document_idents,
        )
        resp.raise_for_status()
        try:
            return resp.json()
        except ValueError:
            return {"raw": resp.text}


def _flatten_folders(nodes: Any, out: Optional[list[Folder]] = None) -> list[Folder]:
    """Wandelt eine (evtl. verschachtelte) Ordnerstruktur in eine flache Liste."""
    if out is None:
        out = []
    if isinstance(nodes, dict):
        nodes = nodes.get("folders", nodes.get("children", [nodes]))
    if isinstance(nodes, list):
        for node in nodes:
            if not isinstance(node, dict):
                continue
            fid = node.get("id")
            name = node.get("name") or node.get("folderName") or ""
            if fid:
                out.append(Folder(id=fid, name=name, raw=node))
            for key in ("children", "subFolders", "folders"):
                if isinstance(node.get(key), list):
                    _flatten_folders(node[key], out)
    return out
