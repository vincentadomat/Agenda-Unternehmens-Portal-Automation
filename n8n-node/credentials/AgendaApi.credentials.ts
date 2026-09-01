import type { ICredentialType, INodeProperties } from 'n8n-workflow';

/**
 * Zugangsdaten für das Agenda Unternehmensportal. Werden NICHT direkt für
 * HTTP-Requests genutzt (kein n8n-HttpRequest-Credential), sondern beim
 * Ausführen als Umgebungsvariablen an das Python-CLI-Tool übergeben
 * (AGENDA_USERNAME/AGENDA_PASSWORD/AGENDA_TOTP_SECRET), das den eigentlichen
 * Login (Keycloak OIDC+PKCE+TOTP) selbst übernimmt.
 */
export class AgendaApi implements ICredentialType {
	name = 'agendaApi';

	displayName = 'Agenda Unternehmensportal';

	documentationUrl =
		'https://github.com/vincentadomat/Agenda-Unternehmens-Portal-Automation';

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
			typeOptions: { password: true },
			default: '',
			description:
				'Wie in Apple Passwords / Authenticator hinterlegt. Leerzeichen werden automatisch entfernt.',
		},
		{
			displayName: 'Python-Interpreter (optional)',
			name: 'pythonPath',
			type: 'string',
			default: '',
			description:
				'Leer lassen, um die mit dieser Node ausgelieferte, automatisch beim Installieren ' +
				'angelegte Python-Umgebung zu nutzen (python/.venv im Node-Paket). Nur setzen, um ' +
				'stattdessen eine eigene/externe Installation des agenda-CLI zu verwenden.',
		},
		{
			displayName: 'Projektverzeichnis (optional)',
			name: 'projectDir',
			type: 'string',
			default: '',
			description:
				'Leer lassen für das mitgelieferte python/-Verzeichnis im Node-Paket. Nur setzen, ' +
				'falls "Python-Interpreter" ebenfalls auf eine externe Installation zeigt.',
		},
	];
}
