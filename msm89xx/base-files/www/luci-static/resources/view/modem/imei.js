'use strict';
'require fs';
'require ui';
'require view';

/*
	IMEI Management Interface for Qualcomm Snapdragon 410 (MSM8916) Modems
	Wraps /usr/bin/change-imei (diag_nv + qmicli + ModemManager refresh)
*/

function parseImeiStatus(stdout) {
	var text = stdout || '';
	var nvramMatch = text.match(/NVRAM IMEI\s*:\s*([^\r\n]+)/);
	var activeMatch = text.match(/Active Modem\s*:\s*([^\r\n]+)/);
	return {
		nvram: nvramMatch ? nvramMatch[1].trim() : '--',
		active: activeMatch ? activeMatch[1].trim() : '--'
	};
}

function renderColoredLog(container, text) {
	while (container.firstChild)
		container.removeChild(container.firstChild);

	var lines = (text || '').split(/\r?\n/);
	if (lines.length === 1 && lines[0] === '') {
		container.appendChild(E('span', { 'style': 'color: #94a3b8;' }, _('(No output)')));
		return;
	}

	lines.forEach(function(line, idx) {
		var style = 'color: #e2e8f0;';
		if (/^\[\+\]/.test(line)) {
			style = 'color: #4ade80; font-weight: 600;';
		} else if (/^\[-\]|ERROR|Error:/i.test(line)) {
			style = 'color: #f87171; font-weight: 600;';
		} else if (/^\[\*\]/.test(line)) {
			style = 'color: #38bdf8;';
		} else if (/^=+$/.test(line.trim())) {
			style = 'color: #64748b;';
		} else if (/IMEI/i.test(line)) {
			style = 'color: #fbbf24; font-weight: 600;';
		}

		container.appendChild(E('span', { 'style': style }, line));
		if (idx < lines.length - 1)
			container.appendChild(document.createTextNode('\n'));
	});
}

return view.extend({
	fetchStatus: function() {
		return fs.exec('/usr/bin/change-imei', []).then(function(res) {
			return parseImeiStatus(res.stdout);
		}).catch(function() {
			return { nvram: '--', active: '--' };
		});
	},

	load: function() {
		return this.fetchStatus();
	},

	refreshStatusUI: function() {
		return this.fetchStatus().then(function(status) {
			var elNvram = document.getElementById('imei-nvram-val');
			var elActive = document.getElementById('imei-active-val');
			if (elNvram) elNvram.textContent = status.nvram;
			if (elActive) elActive.textContent = status.active;
		});
	},

	executeImeiChange: function(newImei) {
		var self = this;
		var btn = document.getElementById('btn-apply-imei');
		var outputBox = document.getElementById('imei-output');
		if (btn) btn.disabled = true;

		ui.showIndicator('imei-apply', _('Writing IMEI to NVRAM and restarting ModemManager...'));

		fs.exec('/usr/bin/change-imei', [newImei]).then(function(res) {
			ui.hideIndicator('imei-apply');
			var out = [res.stdout || '', res.stderr || ''].filter(Boolean).join('\n').trim();
			if (outputBox) {
				outputBox.style.display = 'block';
				renderColoredLog(outputBox, out);
			}

			if (res.code === 0) {
				ui.addNotification(_('Success'), E('p', {}, _('IMEI has been successfully updated to %s.').format(newImei)), 'success');
				return self.refreshStatusUI();
			} else {
				ui.addNotification(_('Operation Failed'), E('p', {}, _('Failed to update IMEI (exit code %d). Please inspect the execution log below.').format(res.code)), 'danger');
			}
		}).catch(function(err) {
			ui.hideIndicator('imei-apply');
			if (outputBox) {
				outputBox.style.display = 'block';
				renderColoredLog(outputBox, 'Error: ' + (err.message || err));
			}
			ui.addNotification(_('Execution Error'), E('p', {}, err.message || _('Failed to execute /usr/bin/change-imei.')), 'danger');
		}).finally(function() {
			if (btn) btn.disabled = false;
		});
	},

	handleApply: function() {
		var self = this;
		var input = document.getElementById('imei-new-input');
		var newImei = input ? input.value.trim() : '';

		if (!/^[0-9]{15}$/.test(newImei)) {
			ui.addNotification(_('Invalid Input'), E('p', {}, _('Please enter a valid 15-digit numeric IMEI before proceeding.')), 'error');
			return;
		}

		ui.showModal(_('Confirm IMEI Modification'), [
			E('p', {}, _('You are about to overwrite the modem NVRAM IMEI (NV item 550) with the following value:')),
			E('div', {
				'style': 'margin: 12px 0; padding: 10px 14px; background: rgba(245, 158, 11, 0.1); border: 1px solid rgba(245, 158, 11, 0.35); border-radius: 6px; font-family: monospace; font-size: 1.1rem; font-weight: 700; text-align: center; letter-spacing: 1px;'
			}, newImei),
			E('p', { 'style': 'font-size: 0.9rem; color: var(--proton-muted);' },
				_('The cellular radio will be temporarily cycled and ModemManager will restart (~4 seconds). Ensure you have a backup of your original NVRAM and comply with local regulations.')
			),
			E('div', { 'class': 'right', 'style': 'margin-top: 16px; display: flex; justify-content: flex-end; gap: 8px;' }, [
				E('button', {
					'class': 'cbi-button cbi-button-neutral',
					'click': ui.hideModal
				}, _('Cancel')),
				E('button', {
					'class': 'cbi-button cbi-button-action important',
					'click': function() {
						ui.hideModal();
						self.executeImeiChange(newImei);
					}
				}, _('Confirm & Apply'))
			])
		]);
	},

	render: function(data) {
		var self = this;
		var nvramImei = (data && data.nvram) ? data.nvram : '--';
		var activeImei = (data && data.active) ? data.active : '--';

		return E('div', { 'class': 'cbi-map', 'style': 'padding-bottom: 30px;' }, [
			E('h2', { 'style': 'margin-bottom: 6px; font-weight: 700;' }, _('IMEI Configuration')),
			E('div', { 'class': 'cbi-map-descr', 'style': 'margin-bottom: 24px; color: var(--proton-muted);' },
				_('Inspect and update the Qualcomm baseband NVRAM IMEI (NV item 550) and synchronize the active ModemManager identity.')
			),

			E('div', { 'class': 'cbi-section', 'style': 'background: var(--proton-card-bg, rgba(255,255,255,0.03)); border: 1px solid var(--proton-border); border-radius: var(--proton-radius); padding: 22px; margin-bottom: 20px;' }, [
				E('div', { 'style': 'display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px;' }, [
					E('div', { 'style': 'font-weight: 700; font-size: 1rem;' }, _('Current Device Identity')),
					E('button', {
						'class': 'cbi-button cbi-button-neutral',
						'click': function(ev) {
							ev.preventDefault();
							self.refreshStatusUI().then(function() {
								ui.addNotification(_('Refreshed'), E('p', {}, _('Current IMEI status has been reloaded from NVRAM and ModemManager.')), 'info');
							});
						}
					}, _('Refresh'))
				]),

				E('div', { 'style': 'display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 14px; margin-bottom: 20px;' }, [
					E('div', { 'style': 'padding: 12px 16px; border: 1px solid var(--proton-border); border-radius: var(--proton-radius-sm);' }, [
						E('div', { 'style': 'font-size: 0.8rem; color: var(--proton-muted); margin-bottom: 4px;' }, _('NVRAM IMEI (NV 550)')),
						E('div', { 'id': 'imei-nvram-val', 'style': 'font-family: monospace; font-size: 1.05rem; font-weight: 700;' }, nvramImei)
					]),
					E('div', { 'style': 'padding: 12px 16px; border: 1px solid var(--proton-border); border-radius: var(--proton-radius-sm);' }, [
						E('div', { 'style': 'font-size: 0.8rem; color: var(--proton-muted); margin-bottom: 4px;' }, _('Active Modem IMEI')),
						E('div', { 'id': 'imei-active-val', 'style': 'font-family: monospace; font-size: 1.05rem; font-weight: 700;' }, activeImei)
					])
				]),

				E('div', {
					'style': 'margin-bottom: 20px; padding: 14px 16px; background: rgba(245, 158, 11, 0.08); border-left: 4px solid #f59e0b; border-radius: var(--proton-radius-sm); font-size: 0.86rem; line-height: 1.5;'
				}, [
					E('strong', { 'style': 'color: #fbbf24; display: block; margin-bottom: 4px;' }, _('Warning: NVRAM Modification')),
					_('Modifying the IMEI directly writes to Qualcomm NVRAM (NV item 550), temporarily drops the cellular connection while cycling radio power, and restarts ModemManager (~4 seconds). Ensure you have backed up your original NVRAM partitions and that any modification complies with applicable local telecommunications regulations.')
				]),

				E('div', { 'class': 'cbi-value', 'style': 'border-top: 1px solid var(--proton-border); padding-top: 18px;' }, [
					E('label', { 'class': 'cbi-value-title', 'style': 'font-weight: 600;' }, _('New 15-Digit IMEI:')),
					E('div', { 'class': 'cbi-value-field' }, [
						E('div', { 'style': 'display: flex; gap: 10px; flex-wrap: wrap; align-items: center;' }, [
							E('input', {
								'type': 'text',
								'id': 'imei-new-input',
								'class': 'cbi-input-text',
								'maxlength': '15',
								'placeholder': 'xxxxxxxxxxxxxxx',
								'style': 'width: 240px; font-family: monospace; font-size: 15px; padding: 8px 12px; letter-spacing: 1px;',
								'keydown': function(ev) {
									if (ev.keyCode === 13) {
										ev.preventDefault();
										self.handleApply();
									}
								}
							}),
							E('button', {
								'id': 'btn-apply-imei',
								'class': 'cbi-button cbi-button-action important',
								'style': 'padding: 8px 20px; font-weight: 600;',
								'click': function(ev) {
									ev.preventDefault();
									self.handleApply();
								}
							}, _('Change IMEI'))
						]),
						E('div', { 'class': 'cbi-value-description', 'style': 'margin-top: 8px; font-size: 0.82rem; color: var(--proton-muted);' },
							_('Must be exactly 15 numeric digits (0-9).')
						)
					])
				]),

				E('div', { 'style': 'margin-top: 18px;' }, [
					E('pre', {
						'id': 'imei-output',
						'style': 'display: none; padding: 14px; background: #0f172a; color: #e2e8f0; font-family: monospace; font-size: 13px; line-height: 1.55; border-radius: 6px; border: 1px solid var(--border-color-medium, #334155); max-height: 280px; overflow-y: auto; white-space: pre-wrap;'
					})
				])
			])
		]);
	},

	handleSaveApply: null,
	handleSave: null,
	handleReset: null
});
