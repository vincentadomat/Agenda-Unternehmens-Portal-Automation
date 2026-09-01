# n8n-nodes-agenda-unternehmensportal

Eine echte n8n-Node ("Agenda Unternehmensportal") für Workflows – kein
Execute-Command-Workaround mehr. Sie bündelt das Python-CLI aus dem
[Hauptprojekt](../README.md) und ruft es intern auf; alle Login-/API-Logik
(Keycloak OIDC+PKCE+TOTP, digibel-REST-API) bleibt unverändert in Python.

⚠️ Dieses Projekt ist – wie das Hauptprojekt – **inoffiziell und ohne
Zustimmung des Anbieters** entstanden (Reverse Engineering). Siehe die
Hinweise in der [Haupt-README](../README.md#hinweis-zur-ki-nutzung) zu
KI-Nutzung, Unabhängigkeit und Haftungsausschluss – die gelten hier genauso.

## Architektur in Kürze

```
n8n-Workflow
  └─ Agenda-Node (TypeScript, läuft im n8n-Prozess)
       └─ execFile(<bundled venv>/bin/python, ["-m", "agenda", ...])
            └─ Python-CLI (dist/../python/agenda/, aus dem Hauptprojekt kopiert)
                 └─ HTTPS ↔ Agenda Unternehmensportal
```

Die Node selbst spricht **nicht** direkt mit dem Portal – sie ist ein
dünner Wrapper, der bei jeder Ausführung einen Python-Subprozess startet,
die Zugangsdaten als Umgebungsvariablen übergibt und die `--json`-Ausgabe
des CLI parst. Das bedeutet:

- Die gesamte, live gegen die echte API verifizierte Logik (Auth, Dedup,
  Sicherheits-Learnings zu `--next-step`/`--verify`/`provide-document`, …)
  existiert nur einmal, im Python-Code – kein doppelt zu pflegender
  TypeScript-Nachbau.
- Ein System-Python (`python3` oder `python`) muss auf dem n8n-Host
  vorhanden sein. Node.js kann keinen Python-Code ausführen – das lässt
  sich nicht "wegbündeln", nur der manuelle Setup-Schritt (venv anlegen,
  Abhängigkeiten installieren) automatisieren (siehe unten).

## Voraussetzungen

- n8n (getestet mit **2.30.8**, selbst gehostet/npm-Installation)
- Node.js ≥ 18 auf dem n8n-Host (ohnehin da, da n8n selbst darauf läuft)
- **System-Python 3** (`python3` oder `python` im `PATH` des n8n-Prozesses,
  z. B. `/usr/bin/python3`) – nur für die einmalige venv-Erstellung beim
  Installieren nötig, danach läuft alles im eigenen venv

## Installation

### Option A: Fertiges Release nutzen (empfohlen)

Fertig gebautes Tarball von der [Releases-Seite](https://github.com/vincentadomat/Agenda-Unternehmens-Portal-Automation/releases)
herunterladen (`n8n-nodes-agenda-unternehmensportal-<version>.tgz`) und
direkt bei Schritt 2 ("In n8n installieren") weitermachen – kein eigenes
Bauen nötig.

### Option B: Selbst bauen

```bash
cd n8n-node
npm install
npm run build     # tsc + Icon kopieren -> dist/
npm pack          # erzeugt n8n-nodes-agenda-unternehmensportal-<version>.tgz
```

> **Node-Version beim Bauen beachten:** `n8n-workflow` zieht transitiv
> `isolated-vm` (native Kompilierung) als Dependency. Auf sehr neuen
> Node-Versionen (getestet: kaputt auf v26 auf macOS) schlägt der
> node-gyp-Build fehl. Am zuverlässigsten baut man auf **derselben
> Node-Version, mit der n8n selbst läuft** (z. B. via `nvm`, oder direkt
> auf dem n8n-Host).

### 2. In n8n installieren (ohne npm-Registry, private/manuelle Installation)

n8n sucht Community-Nodes unter `<N8N_USER_FOLDER>/.n8n/nodes/`. Ohne die
Node bei npm zu veröffentlichen, installiert man das Tarball direkt dort:

```bash
cd "$N8N_USER_FOLDER/.n8n/nodes"   # z. B. /opt/n8n/data/.n8n/nodes
npm install /pfad/zu/n8n-nodes-agenda-unternehmensportal-0.1.0.tgz
```

Das führt automatisch das `postinstall`-Script aus (siehe unten) und legt
das venv für das mitgelieferte CLI an. Danach n8n neu starten (z. B.
`systemctl restart n8n`), damit die neue Node geladen wird.

**Wichtig:** Diese manuelle Installation trägt die Node **nicht** in n8n's
"Community Nodes"-Verwaltungsseite (Settings → Community Nodes) ein – das
passiert nur bei Installation über deren UI-Feature (das npm-Registry
voraussetzt). Die Node funktioniert trotzdem identisch, sie taucht nur nicht
in dieser Verwaltungsliste auf. Update/Deinstallation entsprechend auch
manuell: altes `node_modules/n8n-nodes-agenda-unternehmensportal` löschen,
neues Tarball installieren, n8n neu starten.

### 3. Automatisches Python-Setup (`postinstall`)

`npm install` (egal ob lokal beim Bauen oder beim Installieren in n8n)
führt `scripts/postinstall.js` aus:

1. Prüft, ob unter `python/.venv` bereits ein venv existiert (überspringt
   dann alles).
2. Sucht `python3`, dann `python` im `PATH`.
3. Legt `python/.venv` an und installiert `python/requirements.txt`
   (`requests`, `pyotp`) darin.
4. Findet kein System-Python: nur eine Warnung, kein Abbruch – ohne venv
   schlägt dann jede Ausführung der Node mit einem klaren Fehler fehl
   (`ENOENT`, Pfad zum venv fehlt). In dem Fall das venv manuell unter
   `<installierte-node>/python/.venv` anlegen (siehe Abschnitt Debugging).

Kein manuelles `git clone` mehr nötig – das Python-Tool ist Teil des
npm-Pakets (`python/agenda/`, eine Kopie des Hauptprojekt-Codes zum
Build-Zeitpunkt). Der Python-Interpreter/Projektverzeichnis ist fest auf
diese gebündelte Umgebung verdrahtet, es gibt bewusst keine
Override-Möglichkeit über die Credentials mehr (siehe "Bekannte Probleme"
unten, warum).

## Credentials einrichten

In n8n: **Credentials → New → "Agenda Unternehmensportal"**.

| Feld | Pflicht | Bedeutung |
|---|---|---|
| Benutzername (E-Mail) | ja | Login-Benutzer des Portals |
| Passwort | ja | Portal-Passwort (maskiert dargestellt) |
| TOTP-Secret (Base32) | ja | Wie in Apple Passwords/Authenticator hinterlegt. **Bewusst als Klartextfeld** (siehe "Bekannte Probleme") |

Die Zugangsdaten werden **nicht** für HTTP-Requests der Node selbst
verwendet, sondern bei jeder Ausführung als Umgebungsvariablen
(`AGENDA_USERNAME`, `AGENDA_PASSWORD`, `AGENDA_TOTP_SECRET`) an den
Python-Subprozess übergeben – der eigentliche Login läuft komplett in
Python (siehe Haupt-README). n8n verschlüsselt die Credentials in seiner
eigenen Datenbank; es liegt keine Klartext-`.env`-Datei mehr auf der
Platte (im Gegensatz zur direkten CLI-Nutzung ohne n8n).

Der Token-Cache (`~/.cache/agenda/tokens.json` des n8n-Prozess-Users, meist
`root`) sorgt weiterhin dafür, dass nicht bei jeder Ausführung ein neuer
TOTP-Code fällig wird (siehe Haupt-README, ca. 24h Refresh-Token-Gültigkeit).

## Operationen

Ein "Operation"-Dropdown wählt die Aktion, die restlichen Felder passen
sich entsprechend an (`displayOptions`).

| Operation | Pflichtfelder | Entspricht CLI-Befehl |
|---|---|---|
| Mandanten auflisten | – | `list-mandants` |
| Ordner auflisten | Mandant | `list-folders` |
| Belege auflisten | Mandant, Ordner, Status | `list-documents` |
| Beleg anzeigen | Mandant, Beleg-ID | `show-document` |
| Beleg hochladen | Mandant, Ordner, Binary-Property(s) | `belegupload` |
| Beleg herunterladen | Mandant, Beleg-ID(s) | `download-document` |
| Beleg bearbeiten | Mandant, Beleg-ID | `edit-document` |
| Beleg freigeben | Mandant, Ordner, Beleg-ID(s) | `provide-document` |

**Mehrere Belege pro Item:** Bei "Beleg-ID(s)" akzeptieren `Beleg
herunterladen` und `Beleg freigeben` mehrere `documentIdent`, getrennt durch
Komma oder Leerzeichen. `Beleg anzeigen` und `Beleg bearbeiten` nutzen nur
den ersten Wert (ein Beleg pro Aufruf/Item).

### Beleg hochladen – Dateiübergabe

Erwartet die Datei(en) als **Binary-Data** auf dem eingehenden Item (z. B.
von einem "Read/Write Files from Disk"-, "HTTP Request"- oder
E-Mail-Attachment-Node davor). Das Feld "Binary-Property(s)" nennt den/die
Property-Namen (Default `data`), mehrere getrennt durch Komma. Die Node
schreibt sie in ein temporäres Verzeichnis, ruft `belegupload` damit auf und
löscht das Verzeichnis danach wieder.

**Folgeschritt-Default ist `SORT_DOCUMENT`** (sicher, rückholbar) – wer
direkt an den Buchhalter übermitteln will, muss das bewusst auf
"Direkt an Buchhalter übermitteln" umstellen (siehe Warnungen unten).

### Beleg herunterladen – Ausgabe

- **"Zielverzeichnis (Host)" leer lassen** (Default): Datei(en) werden in
  ein temporäres Verzeichnis geladen, als **Binary-Output** an das Item
  gehängt (Property-Name = bereinigter Dateiname) und das temporäre
  Verzeichnis danach gelöscht. So lässt sich das Ergebnis direkt an einen
  nachfolgenden Node (z. B. "Move Binary Data", E-Mail-Versand,
  Cloud-Upload) weiterreichen.
- **Zielverzeichnis gesetzt:** Datei(en) landen dauerhaft dort auf dem
  Host-Dateisystem (Pfad steht im JSON-Output unter `downloaded[].file`),
  kein Binary-Output.

## ⚠️ Sicherheitshinweise (aus dem Hauptprojekt übernommen)

Diese Erkenntnisse gelten für die Node genauso wie fürs CLI direkt – bitte
vor Produktiveinsatz lesen:

- **`--next-step` / "Folgeschritt"** ist der eigentliche Freigabe-Schalter
  beim Hochladen, nicht "Benachrichtigung senden". Erst `PROVIDE_DOCUMENT`
  ("Direkt an Buchhalter übermitteln") ist endgültig fix und übermittelt den
  Beleg sofort – unabhängig vom Notify-Häkchen. `SORT_DOCUMENT` und
  `VERIFY_AND_PAY` sind von dort noch löschbar.
- **"Beleg freigeben" (`provide-document`) ist endgültig, nicht
  rückholbar** – live an einer echten Rechnung verifiziert.
- **"Prüfstatus" (`--verify`/`--unverify`)** markiert einen Beleg nur als
  geprüft (Status `VERIFIED`, bleibt in "Prüfen und Zahlen") – **kein**
  Freigabe-Schritt, keine Übermittlung an den Buchhalter. `--unverify` macht
  das rückgängig.
- **Dedup-Check ist Default** ("Duplikate per MD5 überspringen") – das
  Backend blockiert Duplikate sonst nicht selbst.
- **Zahlungsdaten (SEPA-Überweisungen) werden nicht unterstützt** – diese
  Node kann und soll keine echten Geldüberweisungen anlegen oder auslösen.

Ausführliche Herleitung/Live-Test-Nachweise: siehe
[Haupt-README](../README.md).

## Bekannte Probleme

### Credential-Dialog friert ein bei zwei oder mehr maskierten Feldern

**Live reproduziert am 2026-09-01 gegen n8n 2.30.8 / n8n-workflow 2.30.2.**
Enthält eine Credential-Definition **zwei oder mehr** Felder mit
`typeOptions: { password: true }`, friert der Credential-Dialog beim Öffnen
bzw. bei der ersten Eingabe komplett ein: keine Konsolen-Fehler, keine
hängenden Netzwerk-Requests, Hintergrund-Telemetrie läuft weiter (der
Browser-Tab ist also nicht global blockiert) – nur die Formular-Reaktivität
selbst reagiert nicht mehr, auch Speichern/Abbrechen tut nichts.

Systematisch eingegrenzt (jeweils Node neu bauen/installieren/neu starten
und im Browser testen):

| Getestete Variante | Ergebnis |
|---|---|
| Alle 5 ursprünglichen Felder (inkl. optionaler Python-Pfade, 2× `password`-Typ, `documentationUrl`) | ❌ hängt |
| Nur 3 Felder (ohne Python-Pfade), weiterhin 2× `password`-Typ | ❌ hängt |
| 1 einziges einfaches String-Feld (kein `password`-Typ) | ✅ funktioniert |
| 3 einfache String-Felder, kein `password`-Typ, ohne `documentationUrl` | ✅ funktioniert |
| 3 Felder, **2×** `password`-Typ, ohne `documentationUrl` | ❌ hängt |
| 3 Felder, **1×** `password`-Typ, ohne `documentationUrl` | ✅ funktioniert |

→ Der Auslöser ist **die Kombination von zwei oder mehr `typeOptions:
{ password: true }`-Feldern** in derselben Credential, nicht
`documentationUrl`, nicht die Feldanzahl an sich, nicht Feldnamen wie
`password`. Ein einzelnes maskiertes Feld ist unauffällig.

**Workaround (aktuell umgesetzt):** Nur `password` ist maskiert,
`totpSecret` ist bewusst ein Klartextfeld. Das TOTP-Secret ist zwar auch
sensibel, aber kurzlebiger nutzbar als das Passwort und wird wie alle
Credential-Felder von n8n verschlüsselt gespeichert - die Maskierung
betrifft nur die Bildschirmanzeige, nicht die Speicherung.

**Vermutlich ein n8n-Bug, kein Fehler in dieser Node.** Nicht upstream
gemeldet/verifiziert. Bei einem n8n-Update prüfen, ob sich das Problem noch
reproduzieren lässt - falls nicht, könnte `totpSecret` wieder maskiert
werden.

### Zwischenzeitlich beobachtet, aber nicht die eigentliche Ursache

Während der Diagnose traten zwei weitere, inzwischen aufgeklärte Symptome
auf, die **nicht** mit dem obigen Bug zusammenhängen und hier nur der
Vollständigkeit halber festgehalten sind:

- Ein testweise per SQL direkt angelegter/wieder gelöschter Test-Workflow
  blieb im Browser als "zuletzt geöffnete Ressource" hängen
  (`useRecentResources.ts`, clientseitig in `localStorage`, nicht in der
  n8n-Datenbank). Äußerte sich als wiederholte `404`-Requests auf
  `/rest/workflows/<id>` beim Öffnen der App und - vermutlich, da die App-
  Initialisierung dadurch blockierte - allgemeine UI-Unresponsivität.
  Behoben durch "Clear site data" für die n8n-Domain im Browser. **Lehre:**
  Testdaten nicht per Rohzugriff (SQL) direkt in einer produktiv laufenden
  n8n-Instanz anlegen/löschen, während eine echte Browser-Session offen sein
  könnte - lieber über die reguläre API/CLI (`n8n import:workflow` +
  passendes Cleanup) oder in einer separaten Test-Instanz.
- Die Node wurde anfangs gegen `n8n-workflow@^1.60.0` (löste zu `1.120.28`
  auf) gebaut, während die Ziel-n8n-Instanz intern `n8n-workflow@2.30.2`
  nutzt. Das war zwar eine unabhängig davon sinnvolle Korrektur (jetzt fest
  auf `2.30.2` gepinnt), hat den Freeze-Bug oben aber **nicht** verursacht
  und auch nicht behoben.

## Entwicklung

```
n8n-node/
  credentials/AgendaApi.credentials.ts   Credential-Typ (Zugangsdaten + optionale Pfade)
  nodes/Agenda/Agenda.node.ts            Die eigentliche Node (Operationen, execFile-Aufrufe)
  nodes/Agenda/agenda.svg                Icon
  python/                                Kopie des CLI-Codes aus dem Hauptprojekt (siehe unten)
  scripts/postinstall.js                 Legt beim Installieren automatisch das venv an
  package.json / tsconfig.json
```

**`python/` synchron halten:** Der Ordner ist eine Kopie von
[`../agenda/`](../agenda/) aus dem Hauptprojekt zum Zeitpunkt des letzten
Builds, kein Symlink/Submodul. Ändert sich das CLI, muss `python/agenda/`
hier manuell neu kopiert und die Node neu gebaut/gepackt/installiert
werden:

```bash
rm -rf n8n-node/python/agenda
cp -r agenda n8n-node/python/agenda
find n8n-node/python/agenda -name '__pycache__' -o -name '.DS_Store' | xargs rm -rf
cd n8n-node && npm run build && npm pack
```

### Debugging

- n8n-Logs (Speicherort abhängig vom Setup, z. B.
  `$N8N_USER_FOLDER/logs/n8n.log` oder `journalctl -u n8n`) zeigen
  Node-Ausführungsfehler.
- Das CLI direkt im mitgelieferten venv testen, unabhängig von n8n:
  ```bash
  cd <installierte-node>/python
  AGENDA_USERNAME=... AGENDA_PASSWORD=... AGENDA_TOTP_SECRET=... \
    .venv/bin/python -m agenda list-mandants --json
  ```
- **`N8N_USER_FOLDER` beim manuellen `n8n`-CLI-Aufruf nicht vergessen:**
  Ruft man `n8n <command>` direkt in einer Shell auf (z. B. zum Debuggen),
  ohne die Umgebungsvariablen des laufenden Services zu setzen, legt die
  n8n-CLI eine **neue, separate** Konfiguration unter `~/.n8n` an – nicht
  die echte, produktive unter `$N8N_USER_FOLDER/.n8n`. Vor jedem
  CLI-Aufruf die Environment-Variablen aus der systemd-Unit (oder dem
  jeweiligen Startskript) exportieren.
- Community-Packages-Tabelle in n8n's DB bleibt bei manueller Installation
  (ohne die UI) leer – das ist normal und kein Fehler, die Node wird trotzdem
  beim Start per Verzeichnis-Scan geladen.

## Lizenz

Community License Version 1.0 (siehe [`../LICENSE`](../LICENSE)), wie das
Hauptprojekt – freie interne Nutzung, keine kommerzielle Weitergabe/SaaS ohne
gesonderte Lizenz. Kein Bezug zum Anbieter des Agenda Unternehmensportals
oder einer sonstigen mit "Agenda" verbundenen Firma – siehe Hinweise in der
[Haupt-README](../README.md).
