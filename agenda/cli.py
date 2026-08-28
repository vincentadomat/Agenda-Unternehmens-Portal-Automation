"""Kommandozeilen-Schnittstelle.

Aufruf-Muster (für andere Scripts / n8n Execute-Command gedacht):

    python -m agenda belegupload --mandant 12345 --folder Rechnungseingang \
        --json datei1.pdf datei2.pdf

    python -m agenda list-mandants --json
    python -m agenda list-folders --mandant 12345 --json

Bei --json wird ausschliesslich JSON auf stdout ausgegeben (gut parsebar);
Statusmeldungen laufen dann über stderr. Exit-Code != 0 bei Fehlern.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import unquote

import requests

from .config import load_config
from .client import AgendaClient
from .auth import AuthError
from .functions import get_function
from .functions.belegupload import NEXT_STEP_SORT


def _fmt_mandators(result: dict) -> str:
    mandators = result.get("mandators", [])
    if not mandators:
        return "Keine Mandanten gefunden."
    lines = ["Mandanten:"]
    for m in mandators:
        lines.append(f"  {m['number']:<8} {m['name']:<45} [{m['office']}]")
    return "\n".join(lines)


def _fmt_folders(result: dict) -> str:
    mandant = result["mandant"]
    folders = result.get("folders", [])
    lines = [f"Ordner für {mandant['name']} ({mandant['number']}):"]
    if not folders:
        lines.append("  (keine Ordner gefunden)")
    for f in folders:
        lines.append(f"  - {f['name']}")
    return "\n".join(lines)


def _fmt_belegupload(result: dict) -> str:
    mandant = result["mandant"]
    folder = result["folder"]
    lines = [f"Mandant: {mandant['name']} ({mandant['number']})  Ordner: {folder['name']}"]
    for u in result.get("uploaded", []):
        if "error" in u:
            status = f" ({u['status']})" if "status" in u else ""
            lines.append(f"  ✗ {u['file']}: Fehler{status} – {u['error']}")
        else:
            lines.append(f"  ✓ {u['file']} hochgeladen (Status {u['status']})")
    for s in result.get("skipped", []):
        lines.append(f"  ⏭  {s['file']} übersprungen ({s['reason']})")
    total = len(result.get("uploaded", [])) + len(result.get("skipped", []))
    success = result.get("success_count", 0)
    summary = f"{success}/{total} erfolgreich hochgeladen (Folgeschritt: {result['next_step']})"
    if result.get("notified"):
        summary += " – an Buchhalter übermittelt"
    lines.append("")
    lines.append(summary)
    return "\n".join(lines)


def _fmt_documents(result: dict) -> str:
    mandant = result["mandant"]
    folder = result["folder"]
    docs = result.get("documents", [])
    lines = [f"Belege in {mandant['name']} ({mandant['number']}) / {folder['name']}:"]
    if not docs:
        lines.append("  (keine Belege gefunden)")
    for d in docs:
        files = ", ".join(f["name"] for f in d["files"]) if d["files"] else "(kein Dateiname)"
        date = d["creationDate"] or "?"
        amount = f"{d['amount']} EUR" if d.get("amount") else "kein Betrag erkannt"
        acc = d.get("accounting") or {}
        booking = ""
        if acc.get("account") or acc.get("postingText"):
            kind = "Vorschlag" if acc.get("proposal") else "verbucht"
            booking = (
                f" | Konto {acc.get('account') or '?'} -> {acc.get('contraAccount') or '?'}"
                f" \"{acc.get('postingText') or ''}\" ({kind})"
            )
        lines.append(f"  [{d['view']:<7}] {d['state']:<10} {date}  {files}  ({amount}){booking}")
        if d.get("comment"):
            lines.append(f"    Kommentar: {d['comment']}")
        lines.append(f"    documentIdent: {d['documentIdent']}")
    for state, err in result.get("errors", {}).items():
        lines.append(f"  ⚠ {state}: {err} (z. B. fehlendes Recht MANAGE_DOC_ARCHIVE)")
    return "\n".join(lines)


def _fmt_download(result: dict) -> str:
    lines = []
    for d in result.get("downloaded", []):
        lines.append(f"  ✓ {d['file']} ({d['size']} Bytes, {d['content_type']})")
    for e in result.get("errors", []):
        lines.append(f"  ✗ {e['documentIdent']}: {e['error']}")
    if not lines:
        lines.append("Keine Dateien heruntergeladen.")
    return "\n".join(lines)


def _fmt_edit_document(result: dict) -> str:
    lines = [f"Beleg {result['documentIdent']} aktualisiert:"]
    for key, value in result.get("changed", {}).items():
        lines.append(f"  {key} = {value}")
    lines.append(f"  -> Status: {result['result'].get('state')}")
    return "\n".join(lines)


_HUMAN_FORMATTERS = {
    "list-mandants": _fmt_mandators,
    "list-folders": _fmt_folders,
    "list-documents": _fmt_documents,
    "belegupload": _fmt_belegupload,
    "download-document": _fmt_download,
    "edit-document": _fmt_edit_document,
}


def _fmt_generic(obj: Any, indent: int = 0) -> str:
    """Fallback-Formatierung für (künftige) Funktionen ohne eigenen Formatter."""
    pad = "  " * indent
    lines: list[str] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            if isinstance(value, (dict, list)) and value:
                lines.append(f"{pad}{key}:")
                lines.append(_fmt_generic(value, indent + 1))
            else:
                lines.append(f"{pad}{key}: {value}")
    elif isinstance(obj, list):
        for item in obj:
            if isinstance(item, (dict, list)):
                lines.append(_fmt_generic(item, indent))
                lines.append("")
            else:
                lines.append(f"{pad}- {item}")
    else:
        lines.append(f"{pad}{obj}")
    return "\n".join(lines)


def _print_result(obj: Any, as_json: bool, command: str | None = None) -> None:
    if as_json:
        print(json.dumps(obj, ensure_ascii=False, indent=2))
        return
    formatter = _HUMAN_FORMATTERS.get(command or "")
    if formatter:
        print(formatter(obj))
    else:
        print(_fmt_generic(obj))


def _cmd_belegupload(client: AgendaClient, args: argparse.Namespace) -> dict:
    fn = get_function("belegupload")
    return fn(client, args)


def _cmd_edit_document(client: AgendaClient, args: argparse.Namespace) -> dict:
    fn = get_function("edit-document")
    return fn(client, args)


def _cmd_list_mandants(client: AgendaClient, args: argparse.Namespace) -> dict:
    mandators = client.mandators()
    return {
        "mandators": [
            {
                "number": m.number,
                "id": m.id,
                "name": m.name,
                "office": m.office_name,
            }
            for m in mandators
        ]
    }


def _cmd_list_folders(client: AgendaClient, args: argparse.Namespace) -> dict:
    mandator = client.find_mandator(args.mandant)
    folders = client.folders(mandator.id)
    return {
        "mandant": {"id": mandator.id, "number": mandator.number, "name": mandator.name},
        "folders": [{"id": f.id, "name": f.name} for f in folders],
    }


def _epoch_ms_to_iso(value: Any) -> Optional[str]:
    if not value:
        return None
    try:
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _accounting_summary(doc: dict) -> Optional[dict]:
    """Buchungsinfo aus `accountingRecord` (siehe digibel.accountingRecord im
    Frontend-Code): Konto/Gegenkonto/Buchungstext/Betrag/Buchungsschlüssel.
    `proposal: true` heißt Vorschlag (noch nicht vom Buchhalter verbucht),
    `false` heißt tatsächlich verbucht."""
    records = doc.get("accountingRecord") or []
    if not records:
        return None
    # Bevorzugt einen bereits verbuchten (nicht mehr Vorschlag-)Eintrag,
    # sonst den ersten (i. d. R. den OCR-Vorschlag).
    record = next((r for r in records if r.get("proposal") is False), records[0])
    return {
        "proposal": record.get("proposal"),
        "grossAmount": record.get("grossAmount") or None,
        "account": record.get("account") or None,
        "contraAccount": record.get("contraAccount") or None,
        "postingText": record.get("postingText") or None,
        "postingKey": record.get("postingKey") or None,
        "cost1": record.get("cost1") or None,
        "cost2": record.get("cost2") or None,
        "invoiceNumber": record.get("field1") or None,
        "invoiceDate": record.get("invoiceDate") or None,
        "bookedDate": _epoch_ms_to_iso(record.get("bookedDate")),
    }


def _file_summary(f: dict) -> dict:
    source = f.get("sourceDocumentFile") or {}
    return {"name": f.get("name"), "md5": source.get("md5")}


def _doc_summary(doc: dict, view: str) -> dict:
    files = doc.get("file") or []
    accounting = _accounting_summary(doc)
    ocr = doc.get("ocrValues") or {}
    return {
        "documentIdent": doc.get("documentIdent") or doc.get("id"),
        "view": view,
        "state": doc.get("state"),
        # Original-MD5 dabei, damit sich ein gerade hochgeladener Beleg
        # zuverlässig wiederfinden lässt (belegupload kennt dieselbe MD5).
        "files": [_file_summary(f) for f in files if isinstance(f, dict)],
        "creationDate": _epoch_ms_to_iso(doc.get("creationDate")),
        "comment": doc.get("notice") or None,
        "amount": (accounting or {}).get("grossAmount") or ocr.get("grossAmount") or None,
        "accounting": accounting,
    }


def _cmd_list_documents(client: AgendaClient, args: argparse.Namespace) -> dict:
    mandator = client.find_mandator(args.mandant)
    folder = client.find_folder(mandator.id, args.folder)

    states = ["draft", "edit", "archive"] if args.state == "all" else [args.state]
    documents: list[dict] = []
    errors: dict[str, str] = {}
    fetchers = {
        "draft": lambda: client.draft_documents(mandator.id, folder.id),
        "edit": lambda: client.edit_documents(mandator.id, folder.id),
        "archive": lambda: client.archive_documents(mandator.id, folder.id),
    }
    for state in states:
        try:
            for doc in fetchers[state]():
                documents.append(_doc_summary(doc, state))
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else "?"
            errors[state] = f"HTTP {status}"

    result = {
        "mandant": {"id": mandator.id, "number": mandator.number, "name": mandator.name},
        "folder": {"id": folder.id, "name": folder.name},
        "documents": documents,
    }
    if errors:
        result["errors"] = errors
    return result


def _filename_from_content_disposition(header: Optional[str]) -> Optional[str]:
    if not header:
        return None
    m = re.search(r"filename\*=UTF-8''([^;]+)", header)
    if m:
        return unquote(m.group(1))
    m = re.search(r'filename="([^"]+)"', header)
    return m.group(1) if m else None


def _unique_path(path: Path) -> Path:
    """Hängt bei Kollision (z. B. zwei Belege mit gleichem Original-Dateinamen
    im selben Batch) `-2`, `-3`, … vor die Dateiendung, statt zu überschreiben."""
    if not path.exists():
        return path
    stem, suffix, i = path.stem, path.suffix, 2
    while True:
        candidate = path.with_name(f"{stem}-{i}{suffix}")
        if not candidate.exists():
            return candidate
        i += 1


def _cmd_download_document(client: AgendaClient, args: argparse.Namespace) -> dict:
    mandator = client.find_mandator(args.mandant)
    idents: list[str] = args.documents
    multiple = len(idents) > 1

    # Bei mehreren Belegen ist --out zwingend ein Verzeichnis (jeder Beleg
    # behält seinen Original-Dateinamen); bei einem einzelnen bleibt --out
    # wie bisher wahlweise Datei- oder Verzeichnispfad.
    out_arg = Path(args.out) if args.out else Path.cwd()

    downloaded: list[dict] = []
    errors: list[dict] = []
    for ident in idents:
        try:
            resp = client.download_source_files(mandator.id, ident)
        except Exception as exc:  # noqa: BLE001 – ein Fehlschlag darf die anderen nicht stoppen
            errors.append({"documentIdent": ident, "error": str(exc)})
            continue

        filename = (
            _filename_from_content_disposition(resp.headers.get("Content-Disposition"))
            or f"{ident}.pdf"
        )
        if multiple or out_arg.is_dir():
            target = _unique_path(out_arg / filename)
        else:
            target = out_arg
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(resp.content)

        downloaded.append(
            {
                "documentIdent": ident,
                "file": str(target),
                "filename": filename,
                "size": len(resp.content),
                "content_type": resp.headers.get("Content-Type"),
            }
        )

    result = {
        "mandant": {"id": mandator.id, "number": mandator.number, "name": mandator.name},
        "downloaded": downloaded,
    }
    if errors:
        result["errors"] = errors
    return result


def build_parser() -> argparse.ArgumentParser:
    # Gemeinsame Flags – gültig sowohl vor als auch nach dem Subcommand.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="Ausgabe als JSON (stdout).")
    common.add_argument(
        "--force-login",
        action="store_true",
        help="Token-Cache ignorieren und komplett neu einloggen (Passwort + TOTP).",
    )

    parser = argparse.ArgumentParser(
        prog="agenda",
        description="Agenda Unternehmensportal – Automatisierung (Beleg-Upload u. a.).",
        parents=[common],
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # belegupload
    up = sub.add_parser(
        "belegupload", parents=[common], help="PDF-Belege hochladen und übermitteln."
    )
    up.add_argument("--mandant", required=True, help="Mandant: Nummer, Name oder UUID.")
    up.add_argument("--folder", required=True, help="Ziel-Ordner, z. B. 'Rechnungseingang'.")
    up.add_argument("files", nargs="+", help="Eine oder mehrere Dateien.")
    up.add_argument(
        "--next-step",
        choices=["SORT_DOCUMENT", "VERIFY_AND_PAY", "PROVIDE_DOCUMENT"],
        default=NEXT_STEP_SORT,
        help=(
            "Folgeschritt nach dem Upload (Default: SORT_DOCUMENT = "
            "'Belegseiten ordnen', sicher/rückholbar). "
            "VERIFY_AND_PAY ('Prüfen und Zahlen') und PROVIDE_DOCUMENT "
            "(direkt an den Buchhalter übermitteln, landet im Belegarchiv) "
            "sind von unserer Seite NICHT mehr rückholbar - bewusst wählen!"
        ),
    )
    up.add_argument(
        "--no-notify",
        dest="notify",
        action="store_false",
        help=(
            "EXPERIMENTELL/UNBESTAETIGT: Im Frontend gibt es dafuer keine "
            "Auswahlmoeglichkeit, die Web-Oberflaeche ruft den notify-Call "
            "beim Bereitstellen offenbar immer auf. Was genau ohne diesen "
            "Call anders ist (E-Mail? interner Hinweis? nichts?), ist noch "
            "nicht mit dem Buchhalter abgeglichen. Verhindert so oder so "
            "NICHT die Uebermittlung selbst - die steuert ausschliesslich "
            "--next-step. Wirkt ausserdem nur, wenn --next-step "
            "PROVIDE_DOCUMENT ist."
        ),
    )
    up.add_argument(
        "--allow-duplicates",
        dest="skip_duplicates",
        action="store_false",
        help=(
            "Dedup-Check per MD5 deaktivieren und bereits vorhandene Dateien "
            "trotzdem hochladen. Das Backend blockiert Duplikate NICHT selbst "
            "- ohne Dedup-Check landet dieselbe Datei ein zweites Mal im "
            "Ordner, was bei --next-step PROVIDE_DOCUMENT zu einer "
            "Doppelbuchung beim Buchhalter führt. Nur bewusst verwenden."
        ),
    )
    up.set_defaults(func=_cmd_belegupload, notify=True, skip_duplicates=True)

    # list-mandants
    lm = sub.add_parser("list-mandants", parents=[common], help="Alle Mandanten auflisten.")
    lm.set_defaults(func=_cmd_list_mandants)

    # list-folders
    lf = sub.add_parser(
        "list-folders", parents=[common], help="Ordner eines Mandanten auflisten."
    )
    lf.add_argument("--mandant", required=True, help="Mandant: Nummer, Name oder UUID.")
    lf.set_defaults(func=_cmd_list_folders)

    # list-documents
    ld = sub.add_parser(
        "list-documents", parents=[common], help="Belege eines Ordners je Status auflisten."
    )
    ld.add_argument("--mandant", required=True, help="Mandant: Nummer, Name oder UUID.")
    ld.add_argument("--folder", required=True, help="Ordner: Name (Teilstring) oder UUID.")
    ld.add_argument(
        "--state",
        choices=["draft", "edit", "archive", "all"],
        default="all",
        help=(
            "Welcher Status abgefragt wird: draft='Belegseiten ordnen', "
            "edit='Prüfen und Zahlen', archive=bereitgestellt/verbucht "
            "(braucht das Recht MANAGE_DOC_ARCHIVE, sonst HTTP 403). "
            "Default: alle drei."
        ),
    )
    ld.set_defaults(func=_cmd_list_documents)

    # download-document
    dd = sub.add_parser(
        "download-document",
        parents=[common],
        help="Original-Datei eines Belegs herunterladen ('Original herunterladen').",
    )
    dd.add_argument("--mandant", required=True, help="Mandant: Nummer, Name oder UUID.")
    dd.add_argument(
        "--document",
        dest="documents",
        required=True,
        nargs="+",
        help="Ein oder mehrere documentIdent(s) des Belegs/der Belege (siehe list-documents).",
    )
    dd.add_argument(
        "--out",
        help="Zielpfad. Bei einem einzelnen Beleg: Datei- oder Verzeichnispfad "
        "(Default: aktuelles Verzeichnis, Original-Dateiname). Bei mehreren "
        "Belegen zwingend ein Verzeichnis, jeder Beleg behält seinen "
        "Original-Dateinamen (Kollisionen bekommen automatisch ein Suffix).",
    )
    dd.set_defaults(func=_cmd_download_document)

    # edit-document
    ed = sub.add_parser(
        "edit-document",
        parents=[common],
        help="Kommentar und/oder Buchungsvorschlag an einem Beleg setzen.",
    )
    ed.add_argument("--mandant", required=True, help="Mandant: Nummer, Name oder UUID.")
    ed.add_argument(
        "--document", required=True, help="documentIdent des Belegs (siehe list-documents)."
    )
    ed.add_argument("--comment", help="Kommentar (Feld 'notice', max. 255 Zeichen im Portal).")
    ed.add_argument("--account", help="Konto (Buchungsvorschlag).")
    ed.add_argument("--contra-account", help="Gegenkonto (Buchungsvorschlag).")
    ed.add_argument("--posting-text", help="Buchungstext (Buchungsvorschlag).")
    ed.add_argument("--amount", type=float, help="Betrag/Brutto (Buchungsvorschlag).")
    ed.add_argument("--posting-key", help="Buchungsschlüssel (Buchungsvorschlag).")
    ed.add_argument("--cost1", help="Kostenstelle 1 (Buchungsvorschlag).")
    ed.add_argument("--cost2", help="Kostenstelle 2 (Buchungsvorschlag).")
    ed.add_argument("--invoice-number", help="Rechnungsnummer (Buchungsvorschlag).")
    ed.add_argument("--invoice-date", help="Rechnungsdatum, Format YYYY-MM-DD.")
    ed.add_argument(
        "--verify",
        action="store_true",
        help=(
            "Markiert den Beleg als geprueft (Einschaetzung des Nutzers, "
            "nicht unabhaengig live getestet): wandert dann von 'Pruefen "
            "und Zahlen' ins Belegarchiv, wird dem Buchhalter bereitgestellt "
            "und ist danach NICHT MEHR BEARBEITBAR - endgueltiger "
            "Freigabe-Schritt, analog zu --next-step PROVIDE_DOCUMENT. "
            "Nur bewusst verwenden."
        ),
    )
    ed.set_defaults(func=_cmd_edit_document)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    def log(msg: str) -> None:
        print(msg, file=sys.stderr)

    try:
        config = load_config()
        client = AgendaClient(config)
        client.login(force=args.force_login)
        result = args.func(client, args)
    except (AuthError, LookupError, FileNotFoundError, ValueError, RuntimeError) as exc:
        err = {"ok": False, "error": str(exc), "type": type(exc).__name__}
        if args.json:
            print(json.dumps(err, ensure_ascii=False, indent=2))
        else:
            log(f"Fehler: {exc}")
        return 1
    except Exception as exc:  # noqa: BLE001 – letzte Absicherung für saubere Ausgabe
        err = {"ok": False, "error": str(exc), "type": type(exc).__name__}
        if args.json:
            print(json.dumps(err, ensure_ascii=False, indent=2))
        else:
            log(f"Unerwarteter Fehler: {exc}")
        return 2

    if not args.json:
        # Menschenlesbare Ausgabe zeigt nur die eigentlichen Ergebnisdaten,
        # das "ok"-Wrapping ist nur für --json relevant.
        _print_result(result, args.json, command=args.command)
        return 0
    result = {"ok": True, **result} if isinstance(result, dict) else {"ok": True, "result": result}
    _print_result(result, args.json, command=args.command)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
