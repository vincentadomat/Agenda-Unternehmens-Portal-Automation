"""Funktion 'edit-document' – Kommentar und/oder Buchungsvorschlag an einem
bereits hochgeladenen Beleg setzen (das "Kür"-Feature).

Rekonstruiert aus einer echten HAR-Aufnahme (HARs/agenda3.har, 2026-08-28) eines
manuellen "Beleg bearbeiten & speichern"-Vorgangs im Portal:

  1. Laden      GET  digibel/{mandatorId}/documents/{documentIdent}
                 -> volles Dokumentobjekt inkl. `optlock` (optimistisches
                    Locking) und `accountingRecord` (Buchungsvorschlag-Array).
  2. Speichern   POST digibel/{mandatorId}/save-edited-document?verify=false
                 -> Body: dasselbe Objekt, mit geänderten Feldern - KEIN
                    partielles Patch, das volle Objekt muss mitgeschickt
                    werden (siehe Schritt 1).

Feld-Zuordnung (aus Freitext-Hinweisen in der HAR-Aufnahme bestätigt):
  notice                          -> Kommentar
  accountingRecord[].postingText  -> Buchungstext
  accountingRecord[].account      -> Konto (Nummer)
  accountingRecord[].contraAccount-> Gegenkonto (Nummer)
  accountingRecord[].grossAmount  -> Betrag (Brutto)
  accountingRecord[].postingKey   -> Buchungsschlüssel
  accountingRecord[].cost1/cost2  -> Kostenstelle 1/2
  accountingRecord[].field1       -> Rechnungsnummer
  accountingRecord[].invoiceDate  -> Rechnungsdatum, ACHTUNG: Epoch-Millisekunden
                                      (anders als ocrValues.invoiceDate, das ein
                                      ISO-Datumsstring ist - beide Felder existieren
                                      parallel im selben Dokument mit unterschiedlichem
                                      Format, wird hier nur für accountingRecord gesetzt)

`verify` (Query-Parameter): Nach Einschätzung des Nutzers (nicht unabhängig live
getestet) markiert `verify=true` den Beleg als geprüft - er wandert dann von
"Prüfen und Zahlen" ins Belegarchiv, wird dem Buchhalter bereitgestellt und ist
danach NICHT MEHR BEARBEITBAR. Das wäre also der endgültige Freigabe-Schritt,
analog zu --next-step PROVIDE_DOCUMENT beim Upload. Deshalb Default `False`
(reines Speichern des Vorschlags), `--verify` nur bewusst und mit Absicht setzen.

Live verifiziert (2026-08-28), was pro Status tatsächlich funktioniert (die
Web-Oberfläche ist hier keine verlässliche Quelle - sie verbietet mehr, als
die REST-API tatsächlich blockiert):
  - DRAFT ("Belegseiten ordnen"): --comment funktioniert, Buchungsfelder vom
    Tool bewusst blockiert (siehe _pick_accounting_record).
  - OCR_FINISHED/OCR_SKIPPED/VERIFIED ("Prüfen und Zahlen"): alles funktioniert.
  - PROVIDED (bereitgestellt, noch nicht abgeholt/verbucht): alles funktioniert
    trotz gegenteiliger Anzeige im Frontend - hier bewusst weiterhin erlaubt.
  - FETCHED (abgeholt) / BOOKED (verbucht): technisch von der API akzeptiert,
    aber auf Nutzerwunsch vom Tool hart gesperrt (siehe _LOCKED_STATES), um
    keine Inkonsistenz mit dem bereits vom Buchhalter verarbeiteten Stand zu
    erzeugen. Nicht live gegengetestet (kein solcher Beleg verfügbar).

Zahlungsdaten (IBAN/BIC/Betrag/Verwendungszweck, `finapi/.../payments/...` +
`digibel/.../connect-payment-item`) werden hier bewusst NICHT verarbeitet/erstellt
- das Anlegen oder Auslösen echter Überweisungen bleibt außerhalb dieses Tools.
`paymentItem` wird beim Speichern nur unverändert durchgereicht (vorhandene
`id` beibehalten bzw. `{}`), nie neu gesetzt.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from ..client import AgendaClient
from . import register


def _date_to_epoch_ms(date_str: str) -> int:
    """'YYYY-MM-DD' -> Epoch-Millisekunden (UTC-Mitternacht), das Format, das
    accountingRecord[].invoiceDate laut HAR-Aufnahme erwartet."""
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _pick_accounting_record(doc: dict) -> dict:
    """Wählt den zu bearbeitenden Buchungsvorschlag-Eintrag: bevorzugt einen
    mit proposal=true (Vorschlag, editierbar), sonst den ersten vorhandenen.

    Belege ohne abgeschlossene OCR (Status DRAFT/"Belegseiten ordnen") haben
    `accountingRecord: []` - dort GIBT es noch keinen Vorschlag zum Bearbeiten.
    Live verifiziert (2026-08-28): Legt man dort trotzdem einen neuen Eintrag
    an, akzeptiert der Server das zwar (kein Fehler), speichert ihn aber mit
    `proposal: false` statt `true` - er sieht dann wie eine bereits erfolgte
    Buchung aus, obwohl der Beleg die OCR/Prüfung nie durchlaufen hat. Um
    diesen irreführenden Zustand zu vermeiden, hier bewusst ein ValueError."""
    records = doc.get("accountingRecord") or []
    if not records:
        raise ValueError(
            "Dieser Beleg hat noch keinen Buchungsvorschlag (Status "
            f"'{doc.get('state')}', hat die OCR/Texterkennung noch nicht "
            "durchlaufen). Ein hier neu angelegter Eintrag würde vom Portal "
            "als 'proposal: false' (= bereits gebucht) statt als Vorschlag "
            "gespeichert - daher blockiert. Buchungsfelder (--account, "
            "--posting-text, --amount, ...) bitte erst setzen, sobald der "
            "Beleg im Status 'Prüfen und Zahlen' ist. --comment funktioniert "
            "schon jetzt."
        )
    return next((r for r in records if r.get("proposal")), records[0])


# Zustände, in denen ein Beleg vom Buchhalter bereits abgeholt oder verbucht
# wurde. Die REST-API lässt Änderungen hier technisch trotzdem zu (live
# verifiziert, 2026-08-28) - die Web-Oberfläche verbietet es aber explizit,
# vermutlich um Inkonsistenzen mit dem bereits verarbeiteten Stand beim
# Buchhalter zu vermeiden. Auf Nutzerwunsch hart gesperrt, kein Override-Flag.
# PROVIDED (bereitgestellt, aber noch nicht abgeholt/verbucht) bleibt bewusst
# erlaubt - das ist der Status, den auch unser Live-Test erfolgreich bearbeitet
# hat.
_LOCKED_STATES = {"FETCHED", "BOOKED"}


def run(client: AgendaClient, args) -> dict:
    mandator = client.find_mandator(args.mandant)
    doc = client.get_document(mandator.id, args.document)

    state = doc.get("state")
    if state in _LOCKED_STATES:
        raise ValueError(
            f"Dieser Beleg hat den Status '{state}' (abgeholt/verbucht) und "
            "wird deshalb nicht mehr bearbeitet, um keine Inkonsistenz mit "
            "dem bereits vom Buchhalter verarbeiteten Stand zu erzeugen."
        )

    # Belege ohne abgeschlossene OCR (Status DRAFT/"Belegseiten ordnen") haben
    # kein `ocrValues`-Objekt. Fehlt es beim Speichern komplett, bricht der
    # Server mit HTTP 500 ab (live verifiziert, 2026-08-28) - ein leeres
    # Objekt reicht als Fix, der Server ergänzt id/optlock selbst.
    doc.setdefault("ocrValues", {})

    changed: dict[str, Any] = {}

    if args.comment is not None:
        doc["notice"] = args.comment
        changed["notice"] = args.comment

    field_map = {
        "account": args.account,
        "contraAccount": args.contra_account,
        "postingText": args.posting_text,
        "postingKey": args.posting_key,
        "cost1": args.cost1,
        "cost2": args.cost2,
        "field1": args.invoice_number,
    }
    wants_accounting_change = (
        any(v is not None for v in field_map.values())
        or args.amount is not None
        or args.invoice_date is not None
    )
    # accountingRecord NUR anfassen, wenn tatsächlich ein Buchungsfeld
    # gesetzt werden soll - Belege ohne abgeschlossene OCR (z. B. Status
    # DRAFT/"Belegseiten ordnen") haben `accountingRecord: []`, und dort
    # einen neuen (unvollständigen) Eintrag anzulegen provoziert einen
    # HTTP 500 vom Server (live verifiziert, 2026-08-28).
    if wants_accounting_change:
        record = _pick_accounting_record(doc)
        for key, value in field_map.items():
            if value is not None:
                record[key] = value
                changed[f"accountingRecord.{key}"] = value
        if args.amount is not None:
            record["grossAmount"] = args.amount
            changed["accountingRecord.grossAmount"] = args.amount
        if args.invoice_date is not None:
            epoch_ms = _date_to_epoch_ms(args.invoice_date)
            record["invoiceDate"] = epoch_ms
            changed["accountingRecord.invoiceDate"] = args.invoice_date

    if not changed and not args.verify:
        raise ValueError(
            "Keine Änderung angegeben - mindestens ein Feld setzen "
            "(z. B. --comment, --account, --posting-text, --amount, ...) "
            "oder --verify, um den Beleg unverändert freizugeben."
        )

    # paymentItem-Handling exakt wie im Frontend beobachtet: immer ein Objekt
    # mitschicken, mit vorhandener id oder leer ({}), nie ganz weglassen.
    existing_payment_id: Optional[str] = (doc.get("paymentItem") or {}).get("id")
    doc["paymentItem"] = {"id": existing_payment_id} if existing_payment_id else {}

    saved = client.save_edited_document(mandator.id, doc, verify=args.verify)

    return {
        "function": "edit-document",
        "mandant": {"id": mandator.id, "number": mandator.number, "name": mandator.name},
        "documentIdent": args.document,
        "changed": changed,
        "verify": args.verify,
        "result": {
            "state": saved.get("state"),
            "notice": saved.get("notice"),
            "accountingRecord": saved.get("accountingRecord"),
        },
    }


@register("edit-document")
def _entry(client: AgendaClient, args) -> dict:
    return run(client, args)
