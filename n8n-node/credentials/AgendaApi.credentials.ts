import type { ICredentialType, INodeProperties } from 'n8n-workflow';

/**
 * Zugangsdaten für das Agenda Unternehmensportal. Werden NICHT direkt für
 * HTTP-Requests genutzt (kein n8n-HttpRequest-Credential), sondern beim
 * Ausführen als Umgebungsvariablen an das Python-CLI-Tool übergeben
 * (AGENDA_USERNAME/AGENDA_PASSWORD/AGENDA_TOTP_SECRET), das den eigentlichen
 * Login (Keycloak OIDC+PKCE+TOTP) selbst übernimmt.
 *
 * WICHTIG - live reproduzierter n8n-Bug (n8n 2.30.8 / n8n-workflow 2.30.2,
 * 2026-09-01): Zwei oder mehr Felder mit `typeOptions: { password: true }`
 * in derselben Credential lassen den Formular-Dialog beim Öffnen/Tippen
 * einfrieren (keine Konsolen-/Netzwerkfehler, UI reagiert einfach nicht
 * mehr). Ein einzelnes maskiertes Feld funktioniert problemlos. Deshalb ist
 * hier nur `password` maskiert, `totpSecret` bewusst als Klartextfeld -
 * siehe n8n-node/README.md "Bekannte Probleme" für die Diagnose-Historie.
 * Kein Bug in diesem Code, sondern in n8n selbst; bei einem n8n-Update
 * prüfen, ob sich das noch reproduzieren lässt.
 */
export class AgendaApi implements ICredentialType {
	name = 'agendaApi';

	displayName = 'Agenda Unternehmensportal';

	properties: INodeProperties[] = [
		{
			displayName: 'Benutzername (E-Mail)',
			name: 'username',
			type: 'string',
			default: '',
		},
		{
			displayName: 'Passwort',
			name: 'password',
			type: 'string',
			typeOptions: { password: true },
			default: '',
		},
		{
			displayName: 'TOTP-Secret (Base32)',
			name: 'totpSecret',
			type: 'string',
			default: '',
		},
	];
}
