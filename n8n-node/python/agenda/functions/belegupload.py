"""Funktion 'belegupload' – PDF-Belege in einen Mandanten-Ordner hochladen
und optional in einen Folgeschritt versetzen (siehe NEXT_STEP_* in diesem
Modul und deren Docstring für die WICHTIGE Warnung zur Irreversibilität).

Rekonstruierter digibel-Ablauf (siehe HAR-Analyse):
  1. Ordner auflösen              GET  digibel/{mandatorId}/MDT/folders
  2. (optional) Dedup-Check       POST digibel/{mandatorId}/existing-md5s
  3. Upload (multipart)           POST digibel/{mandatorId}/{folderId}/upload?ngsw-bypass=true
       Felder: attachments (Datei), deliveryTime (ms), folderId, nextStep, notify=false
       -> `nextStep` bestimmt SOFORT beim Upload den Zielstatus des Belegs
          (siehe NEXT_STEP_* unten) - unabhängig vom notify-Call in Schritt 4!
  4. Zusätzliche Benachrichtigung POST digibel/{mandatorId}/{folderId}/notify   Body: <Anzahl>
       -> vermutlich nur eine E-Mail-Benachrichtigung an den Empfänger, KEIN
          Schalter für die Übermittlung selbst. Nur relevant/aufgerufen wenn
          nextStep == PROVIDE_DOCUMENT.
"""

from __future__ import annotations

import hashlib
import mimetypes
import time
from pathlib import Path
from urllib.parse import quote

from ..client import AgendaClient
from . import register

# nextStep-Enum (Wi) aus der Angular-App. Bestimmt, in welchen Bearbeitungs-
# schritt/Tab der Beleg nach dem Upload verschoben wird – DAS ist der
# eigentliche Sicherheits-Schalter, NICHT --no-notify (siehe unten)!
#
# ACHTUNG Irreversibilität: Erst PROVIDE_DOCUMENT ist von unserer (Mandanten-)
# Seite endgültig fix - übermittelt den Beleg sofort an den Buchhalter
# (Status "bereitgestellt", landet im Belegarchiv), unabhängig davon, ob
# zusätzlich --no-notify gesetzt ist. Aus SORT_DOCUMENT und VERIFY_AND_PAY
# kann ein Beleg dagegen noch gelöscht werden (laut Nutzerangabe).
NEXT_STEP_SORT = "SORT_DOCUMENT"        # sicher: Beleg landet in "Belegseiten ordnen"
NEXT_STEP_VERIFY = "VERIFY_AND_PAY"     # noch löschbar: "Prüfen und Zahlen"
NEXT_STEP_PROVIDE = "PROVIDE_DOCUMENT"  # NICHT rückholbar: direkt an Buchhalter übermitteln
VALID_NEXT_STEPS = {NEXT_STEP_SORT, NEXT_STEP_VERIFY, NEXT_STEP_PROVIDE}
# Kein NEXT_STEP_NONE: "NONE" ist im Frontend nur ein rein clientseitiger
# Sentinel-Wert für "Upload-Dialog abgebrochen" ({nextStep: NONE, cancelled:
# true}) und KEIN von der Upload-API akzeptierter Wert – daher hier bewusst
# nicht als gültige Option geführt.

_DELIVERY_MIN_GAP_MS = 50  # App vergibt aufsteigende, eindeutige deliveryTimes


def _md5(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _delivery_times(count: int) -> list[int]:
    """Eindeutige, aufsteigende Zeitstempel in ms – wie die App sie erzeugt."""
    now = int(time.time() * 1000)
    return [now + i * _DELIVERY_MIN_GAP_MS for i in range(count)]


def run(client: AgendaClient, args) -> dict:
    """Führt den Beleg-Upload aus. `args` ist ein Namespace mit:
        mandant (str), folder (str), files (list[str]),
        next_step (str), notify (bool), skip_duplicates (bool)
    Gibt ein Ergebnis-Dict zurück (n8n-freundlich).
    """
    next_step = args.next_step
    if next_step not in VALID_NEXT_STEPS:
        raise ValueError(
            f"Ungültiger next_step '{next_step}'. Erlaubt: {', '.join(sorted(VALID_NEXT_STEPS))}"
        )

    paths = [Path(f) for f in args.files]
    missing = [str(p) for p in paths if not p.is_file()]
    if missing:
        raise FileNotFoundError("Datei(en) nicht gefunden: " + ", ".join(missing))
    if not paths:
        raise ValueError("Keine Dateien angegeben.")

    mandator = client.find_mandator(args.mandant)
    folder = client.find_folder(mandator.id, args.folder)

    result = {
        "function": "belegupload",
        "mandant": {"id": mandator.id, "number": mandator.number, "name": mandator.name},
        "folder": {"id": folder.id, "name": folder.name},
        "next_step": next_step,
        "uploaded": [],
        "skipped": [],
        "notified": False,
    }

    # 2) Optionaler Dedup-Check via MD5
    upload_paths = paths
    if getattr(args, "skip_duplicates", False):
        md5_by_path = {p: _md5(p) for p in paths}
        resp = client.post(
            f"digibel/{mandator.id}/existing-md5s",
            json=list(md5_by_path.values()),
        )
        resp.raise_for_status()
        existing = set(resp.json() or [])
        upload_paths = []
        for p in paths:
            if md5_by_path[p] in existing:
                result["skipped"].append({"file": p.name, "reason": "duplicate"})
            else:
                upload_paths.append(p)

    if not upload_paths:
        return result

    # 3) Upload (jede Datei als eigener multipart-POST – robuster bei Fehlern)
    delivery_times = _delivery_times(len(upload_paths))
    success = 0
    for path, delivery in zip(upload_paths, delivery_times):
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        # Dateiname wie in der App url-encodieren.
        filename = quote(path.name)
        try:
            with path.open("rb") as fh:
                files = {"attachments": (filename, fh, content_type)}
                data = {
                    "deliveryTime": str(delivery),
                    "folderId": folder.id,
                    "nextStep": next_step,
                    "notify": "false",
                }
                resp = client.post(
                    f"digibel/{mandator.id}/{folder.id}/upload",
                    params={"ngsw-bypass": "true"},
                    files=files,
                    data=data,
                )
        except Exception as exc:  # noqa: BLE001 – eine Datei darf die anderen nicht stoppen
            result["uploaded"].append({"file": path.name, "error": str(exc)})
            continue
        if resp.status_code in (200, 201, 204):
            success += 1
            result["uploaded"].append({"file": path.name, "status": resp.status_code})
        else:
            result["uploaded"].append(
                {"file": path.name, "status": resp.status_code, "error": resp.text[:300]}
            )

    result["success_count"] = success

    # 4) Übermitteln an Buchhalter (nur bei PROVIDE_DOCUMENT und wenn gewünscht)
    if args.notify and next_step == NEXT_STEP_PROVIDE and success > 0:
        resp = client.post(f"digibel/{mandator.id}/{folder.id}/notify", json=success)
        resp.raise_for_status()
        result["notified"] = True

    return result


@register("belegupload")
def _entry(client: AgendaClient, args) -> dict:
    return run(client, args)
