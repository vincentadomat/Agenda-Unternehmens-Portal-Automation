# Agenda Unternehmensportal – Automatisierung

Automatisierter Zugriff auf das **Agenda Unternehmensportal**, um Belege
programmatisch hochzuladen und an den Buchhalter zu übermitteln – ohne die
Web-Oberfläche zu bedienen.

Ergebnis der Reverse-Engineering-Analyse:

- **Auth:** Keycloak OIDC Authorization-Code-Flow mit **PKCE (S256)**,
  Realm `kunden`, Client `unpmobil`, Zweitfaktor per **TOTP**.
- **Upload:** `digibel`-REST-API (Digitaler Beleg).

## Installation

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env      # und ausfüllen (Passwort + TOTP-Secret)
```

## Konfiguration

Secrets kommen aus Umgebungsvariablen oder `.env` (nie im Code):

| Variable | Bedeutung |
|---|---|
| `AGENDA_USERNAME` | Login-Benutzer (E-Mail) |
| `AGENDA_PASSWORD` | Passwort |
| `AGENDA_TOTP_SECRET` | TOTP-Secret (Base32) |
| `AGENDA_TOKEN_CACHE` | optional, Pfad für Token-Cache |

Der Token-Cache (Default `~/.cache/agenda/tokens.json`, Rechte `0600`) speichert
den refresh_token (~24h gültig), sodass nicht bei jedem Aufruf ein neuer
TOTP-Code fällig wird.

## Verwendung

```bash
# Mandanten auflisten
.venv/bin/python -m agenda list-mandants --json

# Ordner eines Mandanten auflisten (folderIds sind pro Mandant verschieden)
.venv/bin/python -m agenda list-folders --mandant 12345 --json

# Belege eines Ordners auflisten (--state draft|edit|archive|all, Default: all)
# inkl. Betrag, Konto/Gegenkonto, Buchungstext, Vorschlag-oder-verbucht, Kommentar
# und von der OCR erkannte Rohdaten (USt-ID, Kundennummer, IBAN, Gegenpartei, ...)
.venv/bin/python -m agenda list-documents --mandant 12345 --folder Rechnungseingang --json

# Einen einzelnen Beleg direkt per documentIdent anzeigen (ohne den ganzen
# Ordner zu durchsuchen) - dieselben Infos wie oben, nur für einen Beleg
.venv/bin/python -m agenda show-document --mandant 12345 \
    --document a1b2c3d4-5e6f-7890-abcd-ef1234567890 --json

# Original-Datei(en) eines oder mehrerer Belege herunterladen (documentIdent aus list-documents)
# Bei mehreren Belegen ist --out zwingend ein Verzeichnis, Namenskollisionen
# bekommen automatisch ein "-2"/"-3"-Suffix statt überschrieben zu werden.
.venv/bin/python -m agenda download-document --mandant 12345 \
    --document a1b2c3d4-5e6f-7890-abcd-ef1234567890 a1b2c3d4-... --out ./downloads/

# Kommentar + Buchungsvorschlag an einem Beleg setzen (nur die angegebenen
# Felder werden geändert, der Rest des Belegs bleibt unangetastet)
.venv/bin/python -m agenda edit-document --mandant 12345 \
    --document a1b2c3d4-5e6f-7890-abcd-ef1234567890 \
    --comment "Bitte auf Konto 4400 buchen" \
    --account 4400 --contra-account 1600 --posting-text "Wareneinkauf" \
    --amount 119.00 --invoice-number "RE-2026-042" --invoice-date 2026-08-15

# Beleg(e) hochladen (Default-Folgeschritt: SORT_DOCUMENT, sicher/rückholbar)
.venv/bin/python -m agenda belegupload \
    --mandant 12345 --folder Rechnungseingang \
    --json beleg1.pdf beleg2.pdf

# Beleg(e) hochladen UND direkt an den Buchhalter übermitteln (nicht rückholbar!)
.venv/bin/python -m agenda belegupload \
    --mandant 12345 --folder Rechnungseingang --next-step PROVIDE_DOCUMENT \
    --json beleg1.pdf beleg2.pdf
```

Der Mandant lässt sich per **Nummer** (`12345`), **Name** (`"Musterfirma GmbH"`)
oder **UUID** angeben; der Ordner per Name (Teilstring) oder UUID.

`list-documents --state archive` (bereits bereitgestellte/verbuchte Belege)
benötigt das Recht `MANAGE_DOC_ARCHIVE` für den genutzten Portal-Account –
ohne dieses Recht liefert der Aufruf HTTP 403 (im JSON-Output unter
`errors.archive` sichtbar, restliche States funktionieren trotzdem).

Nützliche Flags für `belegupload`:

| Flag | Wirkung |
|---|---|
| `--next-step` | Folgeschritt nach dem Upload. **Default: `SORT_DOCUMENT`** (sicher/rückholbar). |
| `--no-notify` | **experimentell/unbestätigt** – unterdrückt nur den zusätzlichen `notify`-Call; die Web-Oberfläche selbst bietet dafür keine Auswahl und ruft ihn beim Bereitstellen offenbar immer auf. Was genau dadurch anders ist, ist noch nicht mit dem Buchhalter geklärt. Wirkt nur bei `--next-step PROVIDE_DOCUMENT` |
| `--allow-duplicates` | Dedup-Check per MD5 **deaktivieren** (Default: aktiv). Das Backend blockiert Duplikate NICHT selbst – ohne Dedup-Check landet dieselbe Datei doppelt im Ordner, was bei `PROVIDE_DOCUMENT` zu einer Doppelbuchung beim Buchhalter führt. Nur bewusst verwenden. |
| `--force-login` | Token-Cache ignorieren, komplett neu einloggen |

**Dedup-Check ist Default:** Jeder Upload prüft standardmäßig per MD5 gegen bereits
vorhandene Dateien im Mandanten und überspringt Duplikate automatisch – das Backend
selbst würde eine Doppel-Datei anstandslos annehmen und (bei `PROVIDE_DOCUMENT`) zu
einer Doppelbuchung führen.

**⚠️ WICHTIG – `--next-step` ist der eigentliche Sicherheits-Schalter, nicht `--no-notify`:**
`nextStep` wird direkt beim Upload mitgeschickt und bestimmt sofort, in welchen
Bearbeitungsschritt der Beleg wandert:

| Wert | Bedeutung | Rückholbar? |
|---|---|---|
| `SORT_DOCUMENT` (Default) | "Belegseiten ordnen" | ✅ ja |
| `VERIFY_AND_PAY` | "Prüfen und Zahlen" | ✅ ja – von dort kann der Beleg noch gelöscht werden |
| `PROVIDE_DOCUMENT` | direkt an den Buchhalter übermitteln (Belegarchiv, Status "bereitgestellt") | ❌ nein |

Erst ab `PROVIDE_DOCUMENT` ist ein Beleg von der (Mandanten-)Seite aus
**endgültig fix** – er wird sofort an den Buchhalter übermittelt, **auch mit
gesetztem `--no-notify`**. Aus `SORT_DOCUMENT` und `VERIFY_AND_PAY` kann ein
Beleg dagegen noch gelöscht werden. Für Tests unbedingt beim sicheren Default
`SORT_DOCUMENT` bleiben und `PROVIDE_DOCUMENT` nur bewusst und absichtlich
verwenden.

Bei `--json` gibt es ausschließlich JSON auf **stdout** (Statusmeldungen und
Fehler laufen über stderr), Exit-Code `!= 0` bei Fehlern – ideal zum Parsen.

## Integration in n8n

Das CLI ist der Integrationspunkt. Zwei Wege:

1. **Execute-Command-Node** (n8n läuft auf demselben Host):
   ```
   /pfad/.venv/bin/python -m agenda belegupload \
     --mandant {{$json.mandant}} --folder {{$json.folder}} --json {{$json.file}}
   ```
   Die JSON-Ausgabe im Folge-Node parsen.

2. **Webhook → Execute-Command**: n8n-Webhook nimmt `mandant`, `folder` und
   Datei(en) entgegen und ruft das CLI auf. So können andere Scripts den Upload
   per HTTP anstoßen und dabei Mandant + Funktion + Ordner übergeben.

## Beleg freigeben (`provide-document`)

Für bereits hochgeladene Belege (z. B. nach Kommentar/Buchungsvorschlag per
`edit-document`) – live verifiziert (2026-08-28) an einer echten Rechnung:

```bash
.venv/bin/python -m agenda provide-document --mandant 12345 --folder Rechnungseingang \
  --document a1b2c3d4-5e6f-7890-abcd-ef1234567890
```

Übermittelt die genannten Belege sofort an den Buchhalter (`state` wechselt zu
`PROVIDED`) – **endgültig, nicht rückholbar**, genau wie `--next-step
PROVIDE_DOCUMENT` beim Upload. Belege, die bereits final sind (`PROVIDED`,
`FETCHED`, `BOOKED`, `DELETED`), werden vorher erkannt und übersprungen statt
einen Fehler zu werfen. Mehrere `--document`-Werte auf einmal möglich.

## Kommentar & Buchungsvorschlag (`edit-document`)

`edit-document` lädt den aktuellen Beleg (inkl. `optlock` für optimistisches
Locking), überschreibt nur die per Flag angegebenen Felder und schickt das
volle Dokument zurück (`save-edited-document` erwartet kein partielles Patch).
Nicht angegebene Felder bleiben unverändert.

**`--verify` / `--unverify` – live verifiziert (2026-08-28), korrigiert eine
frühere Fehlannahme:** `--verify` setzt den Status auf `VERIFIED` ("geprüft"),
bleibt aber weiterhin im Bereich "Prüfen und Zahlen" – **kein** Freigabe-Schritt,
der Beleg wird dadurch **nicht** an den Buchhalter übermittelt/ins Belegarchiv
verschoben. `--unverify` setzt einen so markierten Beleg per API wieder zurück
auf `OCR_FINISHED` (das geht auch im Web-Frontend).

Der tatsächliche Freigabe-Schritt für bereits hochgeladene Belege ist
**`provide-document`** (siehe eigener Abschnitt weiter oben) – live
verifiziert an einer echten Rechnung.

**Live verifiziert (2026-08-28), was auf welchem Beleg-Status funktioniert:**

| Status | `--comment` | Buchungsfelder (`--account`, `--posting-text`, `--amount`, …) |
|---|---|---|
| `DRAFT` ("Belegseiten ordnen") | ✅ funktioniert | ❌ vom Tool blockiert (siehe unten) |
| `OCR_FINISHED`/`OCR_SKIPPED`/`VERIFIED` ("Prüfen und Zahlen") | ✅ funktioniert | ✅ funktioniert |
| `PROVIDED` (bereitgestellt, noch nicht abgeholt/verbucht) | ✅ funktioniert (entgegen Portal-Anzeige) | ✅ funktioniert (entgegen Portal-Anzeige) |
| `FETCHED`/`BOOKED` (abgeholt/verbucht) | ❌ vom Tool gesperrt | ❌ vom Tool gesperrt |

**Wichtiger Fund:** Die REST-API blockiert Änderungen an bereits bereitgestellten
Belegen NICHT selbst – das ist reine Beschränkung der Web-Oberfläche. Live
getestet: Kommentar und Buchungsfelder ließen sich auf einem bereits
`PROVIDED`-Beleg problemlos ändern und unabhängig zurücklesen. Für `FETCHED`
(abgeholt) und `BOOKED` (verbucht) sperrt das Tool die Bearbeitung dennoch hart
(kein Override-Flag) – um keine Inkonsistenz mit dem bereits vom Buchhalter
verarbeiteten Stand zu erzeugen. Das ist **nicht** live gegengetestet (kein
solcher Beleg verfügbar), sondern eine bewusste Vorsichtsmaßnahme.

Buchungsfelder sind für `DRAFT`-Belege bewusst blockiert (klare Fehlermeldung
statt Server-Fehler): Ein Beleg ohne abgeschlossene OCR hat noch keinen
Buchungsvorschlag (`accountingRecord: []`). Legt man dort trotzdem einen neuen
Eintrag an, nimmt das Portal ihn zwar an, speichert ihn aber mit
`proposal: false` statt `true` – er sieht dann wie eine bereits erfolgte
Buchung aus, obwohl der Beleg die Prüfung nie durchlaufen hat. Um diesen
irreführenden Zustand zu vermeiden, prüft `edit-document` das vorher und
bricht mit einer verständlichen Fehlermeldung ab.

Technischer Nebenfund: Belege ohne abgeschlossene OCR haben außerdem kein
`ocrValues`-Objekt – fehlt das beim Speichern komplett, bricht das Portal mit
HTTP 500 ab. Das Tool ergänzt es automatisch als leeres Objekt.

**Zahlungsdaten werden nicht angefasst:** Das Portal kann Belegen auch echte
SEPA-Zahlungsanweisungen zuordnen (IBAN/BIC/Betrag/Verwendungszweck, Bereich
"Zahlungen"). Dieses Tool erstellt oder verändert solche Zahlungen **nicht** –
das bleibt bewusst außerhalb der Automatisierung. `paymentItem` wird beim
Speichern nur unverändert durchgereicht.

### Workflow: Upload + Kommentar/Buchung setzen

Der Upload-Endpunkt liefert keine `documentIdent` zurück (Status 204, leerer
Body) – das ist eine Beschränkung der Portal-API. Deshalb ist das immer ein
Zwei-Schritte-Vorgang: hochladen, dann den Beleg per **MD5** (nicht per
Dateiname – der kann sich wiederholen) wiederfinden und bearbeiten.

```bash
# 1. Hochladen
.venv/bin/python -m agenda belegupload --mandant 12345 --folder Rechnungseingang --json beleg.pdf

# 2. Per MD5 wiederfinden und Kommentar/Buchung setzen
MD5=$(md5 -q beleg.pdf)   # macOS; unter Linux: md5sum beleg.pdf | cut -d' ' -f1
IDENT=$(.venv/bin/python -m agenda list-documents --mandant 12345 --folder Rechnungseingang --json \
  | jq -r --arg md5 "$MD5" '.documents[] | select(.files[].md5 == $md5) | .documentIdent')

.venv/bin/python -m agenda edit-document --mandant 12345 --document "$IDENT" \
  --comment "Bitte auf Konto 4400 buchen" --account 4400 --posting-text "Wareneinkauf"

# 3. Optional: direkt freigeben (endgültig, nicht rückholbar)
.venv/bin/python -m agenda provide-document --mandant 12345 --folder Rechnungseingang \
  --document "$IDENT"
```

Für n8n: dieselben zwei (bzw. drei, mit einem kleinen Function-Node fürs
MD5-Matching dazwischen) Execute-Command-Nodes hintereinander – bewusst nicht
zu einem Komfort-Befehl zusammengefasst, damit jeder Schritt einzeln sichtbar
und fehlerbehandelbar bleibt.

## Erweiterbarkeit

Weitere Portal-Funktionen lassen sich als Modul in `agenda/functions/`
ergänzen und mit `@register("<name>")` registrieren – der Aufrufweg
(`python -m agenda <name> ...`) bleibt gleich.

## Struktur

```
agenda/
  config.py              Konfiguration & Secret-Handling (.env)
  auth.py                Keycloak OIDC+PKCE Login, TOTP, Token-Cache/Refresh
  client.py              AgendaClient: Bearer-Token, ID-Auflösung, REST-Helfer,
                          Dokumente/Download/Kommentar+Buchungsvorschlag
  functions/
    __init__.py          Funktions-Registry
    belegupload.py       Funktion: Beleg-Upload + notify
    editdocument.py      Funktion: Kommentar + Buchungsvorschlag setzen
  cli.py                 Kommandozeile / Dispatch
```

## Hinweis zur KI-Nutzung

Dieses Projekt wurde mit Unterstützung moderner KI-Werkzeuge (Claude) entwickelt.

Implementierung, Architektur, Dokumentation und Tests entstanden durch eine
Kombination aus menschlichem Engineering und KI-gestützter
Softwareentwicklung – einschließlich der Reverse-Engineering-Analyse der
HAR-Aufnahmen, aus denen die API-Struktur rekonstruiert wurde.

## Wichtiger Hinweis

Dieses Projekt entstand **ohne vorherige Absprache, Genehmigung, Autorisierung
oder Prüfung durch den Anbieter des Agenda Unternehmensportals**.

Die Software wurde ausschließlich durch Reverse Engineering öffentlich
zugänglicher Browser-Interaktionen mit dem offiziellen Kundenportal
entwickelt, mit dem Ziel, wiederkehrende interne Aufgaben (Beleg-Uploads an
den eigenen Buchhalter) zu automatisieren.

Der Autor erhebt **keinen Anspruch darauf, dass diese Software vom Anbieter
des Agenda Unternehmensportals genehmigt, kompatibel oder offiziell
unterstützt wird.**

Zukünftige Änderungen am Agenda Unternehmensportal oder dessen Schnittstellen
können dazu führen, dass diese Software teilweise oder vollständig
funktionsunfähig wird.

## Haftungsausschluss

DIESE SOFTWARE WIRD **"WIE VORLIEGEND" ("AS IS")**, OHNE JEGLICHE
GEWÄHRLEISTUNG, BEREITGESTELLT.

Der Autor garantiert insbesondere nicht:

- Kompatibilität mit zukünftigen Versionen des Agenda Unternehmensportals
- unterbrechungsfreien Betrieb
- Richtigkeit oder Vollständigkeit hochgeladener, heruntergeladener oder
  bearbeiteter Belege und Buchungsdaten
- Einhaltung vertraglicher Vereinbarungen zwischen Nutzern, dem Anbieter des
  Agenda Unternehmensportals oder deren Buchhalter/Steuerberater

Nutzer sind allein dafür verantwortlich zu prüfen, dass die Verwendung dieser
Software mit allen geltenden Vereinbarungen und rechtlichen Anforderungen
übereinstimmt.
