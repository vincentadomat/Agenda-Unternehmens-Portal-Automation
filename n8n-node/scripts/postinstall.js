#!/usr/bin/env node
'use strict';

/**
 * Legt beim Installieren dieses npm-Pakets automatisch ein Python-venv für
 * das mitgelieferte agenda-CLI an (unter python/.venv relativ zum Paket) und
 * installiert dessen Abhängigkeiten (requests, pyotp). Ein System-Python
 * (python3 oder python) muss vorhanden sein - das Bundling ersetzt keinen
 * Python-Interpreter, nur den manuellen Deployment-Schritt (git clone + venv
 * von Hand anlegen).
 */

const { execFileSync } = require('child_process');
const fs = require('fs');
const path = require('path');

const packageRoot = path.join(__dirname, '..');
const pythonDir = path.join(packageRoot, 'python');
const venvDir = path.join(pythonDir, '.venv');
const venvPython = path.join(venvDir, 'bin', 'python');

function findSystemPython() {
	for (const candidate of ['python3', 'python']) {
		try {
			execFileSync(candidate, ['--version'], { stdio: 'ignore' });
			return candidate;
		} catch {
			// nächsten Kandidaten versuchen
		}
	}
	return null;
}

function main() {
	if (fs.existsSync(venvPython)) {
		console.log('[agenda-node] venv existiert bereits (' + venvDir + '), überspringe Setup.');
		return;
	}

	const python = findSystemPython();
	if (!python) {
		console.warn(
			'[agenda-node] WARNUNG: Kein "python3"/"python" im PATH gefunden.\n' +
				'  Bitte manuell ein venv unter ' +
				venvDir +
				' anlegen und\n' +
				'  "pip install -r ' +
				path.join(pythonDir, 'requirements.txt') +
				'" ausführen,\n' +
				'  oder in den Node-Credentials "Python-Interpreter"/"Projektverzeichnis" ' +
				'explizit auf eine\n  bestehende Installation zeigen lassen.',
		);
		return;
	}

	console.log(`[agenda-node] Lege venv an unter ${venvDir} (mit "${python}") ...`);
	execFileSync(python, ['-m', 'venv', venvDir], { stdio: 'inherit' });

	console.log('[agenda-node] Installiere Python-Abhängigkeiten (requests, pyotp) ...');
	execFileSync(path.join(venvDir, 'bin', 'pip'), [
		'install',
		'-q',
		'-r',
		path.join(pythonDir, 'requirements.txt'),
	], { stdio: 'inherit' });

	console.log('[agenda-node] Fertig - Python-Umgebung unter ' + venvDir);
}

main();
