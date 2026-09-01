import { execFile } from 'child_process';
import { promisify } from 'util';
import * as fs from 'fs/promises';
import * as os from 'os';
import * as path from 'path';

import type {
	IExecuteFunctions,
	INodeExecutionData,
	INodeType,
	INodeTypeDescription,
	IDataObject,
	IBinaryKeyData,
} from 'n8n-workflow';
import { NodeConnectionTypes, NodeOperationError } from 'n8n-workflow';

const execFileAsync = promisify(execFile);

type AgendaCredentials = {
	username: string;
	password: string;
	totpSecret: string;
};

// Mitgelieferte Python-Umgebung relativ zu dieser (kompilierten) Datei:
// dist/nodes/Agenda/Agenda.node.js -> drei Ebenen hoch = Paket-Wurzel.
// Kein Override mehr über die Credentials (die entsprechenden Felder haben
// live reproduzierbar das Credential-Formular in n8n eingefroren - siehe
// n8n-node/README.md "Bekannte Probleme"). Immer die gebündelte Umgebung.
const PACKAGE_ROOT = path.join(__dirname, '..', '..', '..');
const BUNDLED_PYTHON_DIR = path.join(PACKAGE_ROOT, 'python');
const BUNDLED_PYTHON_BIN = path.join(BUNDLED_PYTHON_DIR, '.venv', 'bin', 'python');

/** Ergebnis eines CLI-Aufrufs: geparstes JSON + rohes stdout/stderr für Fehlerfälle. */
async function runAgenda(
	creds: AgendaCredentials,
	args: string[],
): Promise<IDataObject> {
	const fullArgs = [...args, '--json'];
	let stdout = '';
	try {
		const result = await execFileAsync(BUNDLED_PYTHON_BIN, ['-m', 'agenda', ...fullArgs], {
			cwd: BUNDLED_PYTHON_DIR,
			env: {
				...process.env,
				AGENDA_USERNAME: creds.username,
				AGENDA_PASSWORD: creds.password,
				AGENDA_TOTP_SECRET: creds.totpSecret,
			},
			maxBuffer: 1024 * 1024 * 50,
		});
		stdout = result.stdout;
	} catch (error) {
		// Das CLI gibt bei Fehlern trotzdem JSON auf stdout aus (Exit-Code != 0
		// löst hier eine execFile-Exception aus, stdout/stderr stecken im Error).
		const execError = error as { stdout?: string; stderr?: string; message: string };
		stdout = execError.stdout ?? '';
		if (!stdout) {
			throw new Error(`agenda-CLI fehlgeschlagen: ${execError.stderr || execError.message}`);
		}
	}

	let parsed: IDataObject;
	try {
		parsed = JSON.parse(stdout) as IDataObject;
	} catch {
		throw new Error(`agenda-CLI lieferte kein gültiges JSON: ${stdout.slice(0, 500)}`);
	}

	if (parsed.ok === false) {
		throw new Error(`${parsed.type ?? 'Error'}: ${parsed.error ?? 'unbekannter Fehler'}`);
	}
	return parsed;
}

function splitIds(value: string): string[] {
	return value
		.split(/[\s,]+/)
		.map((s) => s.trim())
		.filter(Boolean);
}

export class Agenda implements INodeType {
	description: INodeTypeDescription = {
		displayName: 'Agenda Unternehmensportal',
		name: 'agenda',
		icon: 'file:agenda.svg',
		group: ['transform'],
		version: 1,
		subtitle: '={{$parameter["operation"]}}',
		description:
			'Belege im Agenda Unternehmensportal verwalten (inoffiziell, Reverse-Engineering - siehe Projekt-README)',
		defaults: { name: 'Agenda Unternehmensportal' },
		inputs: [NodeConnectionTypes.Main],
		outputs: [NodeConnectionTypes.Main],
		credentials: [{ name: 'agendaApi', required: true }],
		properties: [
			{
				displayName: 'Operation',
				name: 'operation',
				type: 'options',
				noDataExpression: true,
				default: 'listDocuments',
				options: [
					{ name: 'Mandanten auflisten', value: 'listMandants', action: 'Mandanten auflisten' },
					{ name: 'Ordner auflisten', value: 'listFolders', action: 'Ordner auflisten' },
					{ name: 'Belege auflisten', value: 'listDocuments', action: 'Belege auflisten' },
					{ name: 'Beleg anzeigen', value: 'showDocument', action: 'Beleg anzeigen' },
					{ name: 'Beleg hochladen', value: 'uploadDocument', action: 'Beleg hochladen' },
					{ name: 'Beleg herunterladen', value: 'downloadDocument', action: 'Beleg herunterladen' },
					{
						name: 'Beleg bearbeiten (Kommentar/Buchung)',
						value: 'editDocument',
						action: 'Beleg bearbeiten',
					},
					{
						name: 'Beleg freigeben (an Buchhalter)',
						value: 'provideDocument',
						action: 'Beleg freigeben',
					},
				],
			},

			// -- Mandant (fast alle Operationen) --------------------------------
			{
				displayName: 'Mandant',
				name: 'mandant',
				type: 'string',
				default: '',
				required: true,
				description: 'Mandantennummer, Name oder UUID',
				displayOptions: { hide: { operation: ['listMandants'] } },
			},

			// -- Ordner ----------------------------------------------------------
			{
				displayName: 'Ordner',
				name: 'folder',
				type: 'string',
				default: '',
				required: true,
				description: 'Ordnername (Teilstring) oder UUID',
				displayOptions: {
					show: { operation: ['listFolders', 'listDocuments', 'uploadDocument', 'provideDocument'] },
				},
			},

			// -- Status-Filter (listDocuments) -----------------------------------
			{
				displayName: 'Status',
				name: 'state',
				type: 'options',
				default: 'all',
				options: [
					{ name: 'Alle', value: 'all' },
					{ name: 'Belegseiten ordnen (draft)', value: 'draft' },
					{ name: 'Prüfen und Zahlen (edit)', value: 'edit' },
					{ name: 'Belegarchiv (archive)', value: 'archive' },
				],
				displayOptions: { show: { operation: ['listDocuments'] } },
			},

			// -- Document-Ident(s) -----------------------------------------------
			{
				displayName: 'Beleg-ID(s) (documentIdent)',
				name: 'documentIds',
				type: 'string',
				default: '',
				required: true,
				description:
					'Ein oder mehrere documentIdent, getrennt durch Komma/Leerzeichen (aus "Belege auflisten")',
				displayOptions: {
					show: { operation: ['showDocument', 'downloadDocument', 'editDocument', 'provideDocument'] },
				},
			},

			// -- Upload: Dateien ---------------------------------------------------
			{
				displayName: 'Binary-Property(s)',
				name: 'binaryPropertyName',
				type: 'string',
				default: 'data',
				required: true,
				description:
					'Name(n) der Binary-Property(s) mit der/den hochzuladenden Datei(en), getrennt durch Komma',
				displayOptions: { show: { operation: ['uploadDocument'] } },
			},
			{
				displayName: 'Folgeschritt',
				name: 'nextStep',
				type: 'options',
				default: 'SORT_DOCUMENT',
				options: [
					{ name: 'Belegseiten ordnen (sicher, rückholbar)', value: 'SORT_DOCUMENT' },
					{ name: 'Prüfen und Zahlen (rückholbar)', value: 'VERIFY_AND_PAY' },
					{
						name: 'Direkt an Buchhalter übermitteln (NICHT rückholbar!)',
						value: 'PROVIDE_DOCUMENT',
					},
				],
				displayOptions: { show: { operation: ['uploadDocument'] } },
			},
			{
				displayName: 'Benachrichtigung senden (--notify)',
				name: 'notify',
				type: 'boolean',
				default: true,
				description:
					'Wirkt nur bei Folgeschritt "Direkt an Buchhalter übermitteln"; unbestätigt, was der zusätzliche Call bewirkt',
				displayOptions: { show: { operation: ['uploadDocument'] } },
			},
			{
				displayName: 'Duplikate per MD5 überspringen',
				name: 'skipDuplicates',
				type: 'boolean',
				default: true,
				displayOptions: { show: { operation: ['uploadDocument'] } },
			},

			// -- Download-Ziel -----------------------------------------------------
			{
				displayName: 'Zielverzeichnis (Host)',
				name: 'outDir',
				type: 'string',
				default: '',
				description:
					'Leer lassen, um ein temporäres Verzeichnis zu nutzen und die Datei(en) als Binary-Output zurückzugeben',
				displayOptions: { show: { operation: ['downloadDocument'] } },
			},

			// -- edit-document Felder ----------------------------------------------
			{
				displayName: 'Kommentar',
				name: 'comment',
				type: 'string',
				default: '',
				displayOptions: { show: { operation: ['editDocument'] } },
			},
			{
				displayName: 'Konto',
				name: 'account',
				type: 'string',
				default: '',
				displayOptions: { show: { operation: ['editDocument'] } },
			},
			{
				displayName: 'Gegenkonto',
				name: 'contraAccount',
				type: 'string',
				default: '',
				displayOptions: { show: { operation: ['editDocument'] } },
			},
			{
				displayName: 'Buchungstext',
				name: 'postingText',
				type: 'string',
				default: '',
				displayOptions: { show: { operation: ['editDocument'] } },
			},
			{
				displayName: 'Betrag',
				name: 'amount',
				type: 'number',
				default: 0,
				typeOptions: { numberPrecision: 2 },
				displayOptions: { show: { operation: ['editDocument'] } },
			},
			{
				displayName: 'Buchungsschlüssel',
				name: 'postingKey',
				type: 'string',
				default: '',
				displayOptions: { show: { operation: ['editDocument'] } },
			},
			{
				displayName: 'Kostenstelle 1',
				name: 'cost1',
				type: 'string',
				default: '',
				displayOptions: { show: { operation: ['editDocument'] } },
			},
			{
				displayName: 'Kostenstelle 2',
				name: 'cost2',
				type: 'string',
				default: '',
				displayOptions: { show: { operation: ['editDocument'] } },
			},
			{
				displayName: 'Rechnungsnummer',
				name: 'invoiceNumber',
				type: 'string',
				default: '',
				displayOptions: { show: { operation: ['editDocument'] } },
			},
			{
				displayName: 'Rechnungsdatum',
				name: 'invoiceDate',
				type: 'string',
				default: '',
				placeholder: 'YYYY-MM-DD',
				displayOptions: { show: { operation: ['editDocument'] } },
			},
			{
				displayName: 'Prüfstatus',
				name: 'verifyAction',
				type: 'options',
				default: 'none',
				options: [
					{ name: 'Unverändert lassen', value: 'none' },
					{
						name: 'Als geprüft markieren (--verify, KEIN Freigabe-Schritt)',
						value: 'verify',
					},
					{ name: 'Prüfung zurücknehmen (--unverify)', value: 'unverify' },
				],
				displayOptions: { show: { operation: ['editDocument'] } },
			},
		],
	};

	async execute(this: IExecuteFunctions): Promise<INodeExecutionData[][]> {
		const items = this.getInputData();
		const returnData: INodeExecutionData[] = [];
		const credentials = (await this.getCredentials('agendaApi')) as unknown as AgendaCredentials;
		const operation = this.getNodeParameter('operation', 0) as string;

		for (let i = 0; i < items.length; i++) {
			try {
				let result: IDataObject;
				let binary: IBinaryKeyData | undefined;

				switch (operation) {
					case 'listMandants': {
						result = await runAgenda(credentials, ['list-mandants']);
						break;
					}
					case 'listFolders': {
						const mandant = this.getNodeParameter('mandant', i) as string;
						result = await runAgenda(credentials, ['list-folders', '--mandant', mandant]);
						break;
					}
					case 'listDocuments': {
						const mandant = this.getNodeParameter('mandant', i) as string;
						const folder = this.getNodeParameter('folder', i) as string;
						const state = this.getNodeParameter('state', i) as string;
						result = await runAgenda(credentials, [
							'list-documents',
							'--mandant',
							mandant,
							'--folder',
							folder,
							'--state',
							state,
						]);
						break;
					}
					case 'showDocument': {
						const mandant = this.getNodeParameter('mandant', i) as string;
						const documentIds = splitIds(this.getNodeParameter('documentIds', i) as string);
						result = await runAgenda(credentials, [
							'show-document',
							'--mandant',
							mandant,
							'--document',
							documentIds[0],
						]);
						break;
					}
					case 'uploadDocument': {
						const mandant = this.getNodeParameter('mandant', i) as string;
						const folder = this.getNodeParameter('folder', i) as string;
						const nextStep = this.getNodeParameter('nextStep', i) as string;
						const notify = this.getNodeParameter('notify', i) as boolean;
						const skipDuplicates = this.getNodeParameter('skipDuplicates', i) as boolean;
						const binaryPropertyNames = (
							this.getNodeParameter('binaryPropertyName', i) as string
						)
							.split(',')
							.map((s) => s.trim())
							.filter(Boolean);

						const tmpDir = await fs.mkdtemp(path.join(os.tmpdir(), 'agenda-upload-'));
						const filePaths: string[] = [];
						try {
							for (const propName of binaryPropertyNames) {
								const binaryData = this.helpers.assertBinaryData(i, propName);
								const buffer = await this.helpers.getBinaryDataBuffer(i, propName);
								const fileName = binaryData.fileName || `${propName}.bin`;
								const filePath = path.join(tmpDir, fileName);
								await fs.writeFile(filePath, buffer);
								filePaths.push(filePath);
							}

							const args = [
								'belegupload',
								'--mandant',
								mandant,
								'--folder',
								folder,
								'--next-step',
								nextStep,
							];
							if (!notify) args.push('--no-notify');
							if (!skipDuplicates) args.push('--allow-duplicates');
							args.push(...filePaths);

							result = await runAgenda(credentials, args);
						} finally {
							await fs.rm(tmpDir, { recursive: true, force: true });
						}
						break;
					}
					case 'downloadDocument': {
						const mandant = this.getNodeParameter('mandant', i) as string;
						const documentIds = splitIds(this.getNodeParameter('documentIds', i) as string);
						const outDirParam = this.getNodeParameter('outDir', i) as string;

						const useTempDir = !outDirParam;
						const targetDir = useTempDir
							? await fs.mkdtemp(path.join(os.tmpdir(), 'agenda-download-'))
							: outDirParam;

						try {
							result = await runAgenda(credentials, [
								'download-document',
								'--mandant',
								mandant,
								'--document',
								...documentIds,
								'--out',
								targetDir,
							]);

							if (useTempDir) {
								binary = {};
								const downloaded = (result.downloaded as IDataObject[]) || [];
								for (const d of downloaded) {
									const filePath = d.file as string;
									const buffer = await fs.readFile(filePath);
									const key = path.basename(filePath).replace(/[^a-zA-Z0-9_.-]/g, '_');
									binary[key] = await this.helpers.prepareBinaryData(
										buffer,
										path.basename(filePath),
									);
								}
							}
						} finally {
							if (useTempDir) await fs.rm(targetDir, { recursive: true, force: true });
						}
						break;
					}
					case 'editDocument': {
						const mandant = this.getNodeParameter('mandant', i) as string;
						const documentIds = splitIds(this.getNodeParameter('documentIds', i) as string);
						const comment = this.getNodeParameter('comment', i) as string;
						const account = this.getNodeParameter('account', i) as string;
						const contraAccount = this.getNodeParameter('contraAccount', i) as string;
						const postingText = this.getNodeParameter('postingText', i) as string;
						const amount = this.getNodeParameter('amount', i) as number;
						const postingKey = this.getNodeParameter('postingKey', i) as string;
						const cost1 = this.getNodeParameter('cost1', i) as string;
						const cost2 = this.getNodeParameter('cost2', i) as string;
						const invoiceNumber = this.getNodeParameter('invoiceNumber', i) as string;
						const invoiceDate = this.getNodeParameter('invoiceDate', i) as string;
						const verifyAction = this.getNodeParameter('verifyAction', i) as string;

						const args = ['edit-document', '--mandant', mandant, '--document', documentIds[0]];
						if (comment) args.push('--comment', comment);
						if (account) args.push('--account', account);
						if (contraAccount) args.push('--contra-account', contraAccount);
						if (postingText) args.push('--posting-text', postingText);
						if (amount) args.push('--amount', String(amount));
						if (postingKey) args.push('--posting-key', postingKey);
						if (cost1) args.push('--cost1', cost1);
						if (cost2) args.push('--cost2', cost2);
						if (invoiceNumber) args.push('--invoice-number', invoiceNumber);
						if (invoiceDate) args.push('--invoice-date', invoiceDate);
						if (verifyAction === 'verify') args.push('--verify');
						if (verifyAction === 'unverify') args.push('--unverify');

						result = await runAgenda(credentials, args);
						break;
					}
					case 'provideDocument': {
						const mandant = this.getNodeParameter('mandant', i) as string;
						const folder = this.getNodeParameter('folder', i) as string;
						const documentIds = splitIds(this.getNodeParameter('documentIds', i) as string);
						result = await runAgenda(credentials, [
							'provide-document',
							'--mandant',
							mandant,
							'--folder',
							folder,
							'--document',
							...documentIds,
						]);
						break;
					}
					default:
						throw new NodeOperationError(this.getNode(), `Unbekannte Operation: ${operation}`);
				}

				returnData.push({
					json: result,
					binary,
					pairedItem: { item: i },
				});
			} catch (error) {
				if (this.continueOnFail()) {
					returnData.push({
						json: { error: (error as Error).message },
						pairedItem: { item: i },
					});
					continue;
				}
				throw new NodeOperationError(this.getNode(), error as Error, { itemIndex: i });
			}
		}

		return [returnData];
	}
}
