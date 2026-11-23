'use strict';
'require dom';
'require rpc';
'require ui';
'require view';

/*
	Speedtest page.
	- Speedtest by Ookla: runs on the router (modem-speedtest ookla) with the
	  official CLI, installed on request by ookla-speedtest-install.
	- Browser test: ping, download and upload from this browser against the
	  server it names (client -> router -> internet).
	Both engines share the gauges, metric cards, throughput chart and history.
*/

var callGetSignal = rpc.declare({
	object: 'luci.proton-system',
	method: 'getSignalDetails',
	expect: { '': {} }
});

var callGetSpeedtestHistory = rpc.declare({
	object: 'luci.proton-system',
	method: 'getSpeedtestHistory',
	expect: { '': [] }
});

var callClearHistory = rpc.declare({
	object: 'luci.proton-system',
	method: 'clearSpeedtestHistory',
	expect: { '': {} }
});

var callOoklaStatus = rpc.declare({
	object: 'luci.proton-system',
	method: 'getOoklaStatus',
	expect: { '': {} }
});

var callInstallOokla = rpc.declare({
	object: 'luci.proton-system',
	method: 'installOokla',
	params: [ 'accept' ],
	expect: { '': {} }
});

var callListOoklaServers = rpc.declare({
	object: 'luci.proton-system',
	method: 'listOoklaServers',
	expect: { '': {} }
});

var callStartRouterSpeedtest = rpc.declare({
	object: 'luci.proton-system',
	method: 'startSpeedtest',
	params: [ 'gateway', 'size', 'custom_host' ],
	expect: { '': {} }
});

var callGetSpeedtestStatus = rpc.declare({
	object: 'luci.proton-system',
	method: 'getSpeedtestStatus',
	expect: { '': {} }
});

var callCancelSpeedtest = rpc.declare({
	object: 'luci.proton-system',
	method: 'cancelSpeedtest',
	expect: { '': {} }
});

// Browser test targets. Each entry pings, downloads and uploads against the
// same service, so the result really describes the server named here.
var GATEWAYS = [
	{
		id: 'cloudflare',
		name: 'Cloudflare (speed.cloudflare.com)',
		location: 'Nearest Cloudflare edge',
		pingUrl: 'https://speed.cloudflare.com/__down?bytes=0',
		downUrl: 'https://speed.cloudflare.com/__down?bytes=',
		upUrl: 'https://speed.cloudflare.com/__up'
	},
	{
		id: 'custom',
		name: 'Custom server (your own URLs)',
		location: 'Custom target',
		pingUrl: '',
		downUrl: '',
		upUrl: ''
	}
];

function createMetricCard(title, valId, subId, badgeId, defaultVal, defaultSub, accentColor) {
	return E('div', {
		'class': 'cbi-section rf-cockpit-card',
		'style': 'margin: 0; padding: 14px 16px; display: flex; flex-direction: column; justify-content: space-between; position: relative; overflow: hidden; border-radius: 8px;'
	}, [
		E('div', { 'style': 'display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;' }, [
			E('span', { 'style': 'font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; color: var(--text-color-medium); font-weight: 600;' }, title),
			E('span', {
				'id': badgeId,
				'class': 'badge',
				'style': 'font-size: 10px; padding: 1px 6px; border-radius: 4px; background: rgba(255,255,255,0.06);'
			}, '--')
		]),
		E('div', {
			'id': valId,
			'style': 'font-size: 22px; font-weight: 800; font-family: monospace; color: ' + (accentColor || 'var(--text-color-high)') + '; line-height: 1.2; margin: 4px 0;'
		}, defaultVal || '--'),
		E('div', {
			'id': subId,
			'style': 'font-size: 11px; color: var(--text-color-medium); font-family: monospace; opacity: 0.85;'
		}, defaultSub || '--')
	]);
}

function createDialGaugeSvg(type, dialId, valueId, peakId, stateBadgeId, maxVal, unit, color) {
	var strokeColor = color || '#38bdf8';
	return E('div', {
		'style': 'display: flex; flex-direction: column; align-items: center; justify-content: center; position: relative; width: 100%; min-width: 200px;'
	}, [
		E('div', { 'style': 'position: relative; width: 180px; height: 180px;' }, [
			dom.create('svg', {
				'viewBox': '0 0 100 100',
				'style': 'width: 100%; height: 100%; transform: rotate(160deg); overflow: visible;'
			}, [
				// Track arc
				dom.create('circle', {
					'cx': '50', 'cy': '50', 'r': '40',
					'fill': 'none',
					'stroke': 'rgba(255, 255, 255, 0.08)',
					'stroke-width': '7',
					'stroke-dasharray': '170 300',
					'stroke-linecap': 'round'
				}),
				// Active value arc
				dom.create('circle', {
					'id': dialId,
					'cx': '50', 'cy': '50', 'r': '40',
					'fill': 'none',
					'stroke': strokeColor,
					'stroke-width': '7.5',
					'stroke-dasharray': '0 300',
					'stroke-linecap': 'round',
					'style': 'transition: stroke-dasharray 0.15s ease-out, stroke 0.3s ease; filter: drop-shadow(0 0 6px ' + strokeColor + 'aa);'
				})
			]),
			// Inner Text Overlay
			E('div', {
				'style': 'position: absolute; top: 0; left: 0; right: 0; bottom: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; pointer-events: none;'
			}, [
				E('div', { 'style': 'font-size: 11px; font-weight: bold; text-transform: uppercase; letter-spacing: 1px; color: var(--text-color-medium); margin-bottom: 2px;' }, type),
				E('div', {
					'id': valueId,
					'style': 'font-size: 28px; font-weight: 800; font-family: monospace; color: #fff; line-height: 1;'
				}, '0.00'),
				E('div', { 'style': 'font-size: 11px; font-weight: 600; color: ' + strokeColor + '; margin-top: 4px;' }, unit)
			])
		]),
		E('div', {
			'style': 'display: flex; flex-direction: column; align-items: center; gap: 4px; font-size: 11px; color: var(--text-color-medium); font-family: monospace; margin-top: -6px;'
		}, [
			E('div', { 'style': 'display: flex; gap: 12px;' }, [
				E('span', {}, _('Peak') + ': <strong id="' + peakId + '" style="color:#fff;">0.00</strong> ' + unit),
				E('span', {}, 'Max: ' + maxVal + ' ' + unit)
			]),
			E('span', {
				'id': stateBadgeId,
				'class': 'badge',
				'style': 'font-size: 10px; padding: 2px 8px; border-radius: 4px; background: rgba(255,255,255,0.06); margin-top: 2px;'
			}, 'Idle')
		])
	]);
}

return view.extend({
	isRunning: false,
	activeXhr: null,
	activeAbortController: null,
	peakDownload: 0,
	peakUpload: 0,
	downloadPoints: [],
	uploadPoints: [],
	maxPointsPerPhase: 30,
	localHistory: [],

	load: function() {
		return Promise.all([
			callGetSignal(),
			callGetSpeedtestHistory(),
			callOoklaStatus().catch(function() { return {}; })
		]);
	},

	// ---- Shared result helpers (used by both engines) ----------------------
	engine: 'ookla',
	ooklaState: {},
	ooklaTimer: null,
	ooklaRunning: false,

	setBadge: function(id, text, color) {
		var el = document.getElementById(id);
		if (!el) return;
		el.textContent = text;
		el.style.color = color || '';
	},

	setLatencyCards: function(avg, min, max, jitter, loss) {
		var set = function(id, t) { var el = document.getElementById(id); if (el) el.textContent = t; };
		var f = function(v) { return (v != null && v !== '' && !isNaN(v)) ? Number(v).toFixed(1) : null; };

		set('st-ping-val', f(avg) ? f(avg) + ' ms' : '--');
		set('st-ping-sub', (f(min) && f(max)) ? _('Low %s · High %s ms').format(f(min), f(max)) : '--');
		if (f(avg)) this.setBadge('st-ping-badge', avg < 50 ? _('Low') : (avg < 100 ? _('Normal') : _('High')), avg < 50 ? '#10b981' : (avg < 100 ? '#38bdf8' : '#f59e0b'));

		set('st-jitter-val', f(jitter) ? f(jitter) + ' ms' : '--');
		if (f(jitter)) this.setBadge('st-jitter-badge', jitter < 10 ? _('Stable') : (jitter < 30 ? _('Moderate') : _('High')), jitter < 10 ? '#10b981' : (jitter < 30 ? '#38bdf8' : '#ef4444'));

		if (loss != null && loss !== '' && !isNaN(loss)) {
			var l = Number(loss);
			set('st-loss-val', (l % 1 ? l.toFixed(1) : l) + '%');
			set('st-loss-sub', l === 0 ? _('All packets received') : _('Packets lost during the test'));
			this.setBadge('st-loss-badge', l === 0 ? _('None') : _('Loss'), l === 0 ? '#10b981' : '#ef4444');
		}
	},

	// Network card from getSignalDetails: real operator/band, or "No service".
	setNetworkCard: function(sig) {
		var set = function(id, t) { var el = document.getElementById(id); if (el) el.textContent = t; };
		var op = (sig && sig.operator) || {}, sv = (sig && sig.serving) || {}, sg = (sig && sig.signal) || {};
		if (!sig || !op.registered) {
			set('st-radio-val', _('No service'));
			set('st-radio-sub', '--');
			this.setBadge('st-radio-badge', '--');
			return { label: '' };
		}
		var label = sv.band || '';
		set('st-radio-val', (op.name || '--') + (label ? ' · ' + label : ''));
		set('st-radio-sub', (sg.rsrp && sg.rsrp !== '--' ? 'RSRP ' + sg.rsrp + ' dBm' : 'RSRP --') + (sg.snr && sg.snr !== '--' ? ' · SNR ' + sg.snr + ' dB' : ''));
		this.setBadge('st-radio-badge', op.reg_state === 'roaming' ? _('Roaming') : _('Home'), '#38bdf8');
		return { label: label };
	},

	resetResults: function() {
		this.peakDownload = 0;
		this.peakUpload = 0;
		this.downloadPoints = [];
		this.uploadPoints = [];
		this.drawWaveform();
		['st-dl-val', 'st-ul-val'].forEach(function(id) { var el = document.getElementById(id); if (el) el.textContent = '0.00'; });
		['st-dl-peak', 'st-ul-peak'].forEach(function(id) { var el = document.getElementById(id); if (el) el.textContent = '0.00'; });
		this.updateDial('st-dl-dial', 0, 100);
		this.updateDial('st-ul-dial', 0, 50);
		var meta = document.getElementById('st-result-meta');
		if (meta) dom.content(meta, []);
	},

	setRunning: function(running) {
		var btnStart = document.getElementById('btn-start-speedtest');
		var btnStop = document.getElementById('btn-stop-speedtest');
		if (btnStart) { btnStart.disabled = running; btnStart.style.opacity = running ? '0.5' : '1'; }
		if (btnStop) btnStop.style.display = running ? 'inline-flex' : 'none';
		document.querySelectorAll('.st-engine-tab').forEach(function(t) { t.disabled = running; });
	},

	selectEngine: function(engine) {
		this.engine = engine;
		document.querySelectorAll('.st-engine-tab').forEach(function(t) {
			var on = (t.getAttribute('data-engine') === engine);
			t.classList.toggle('st-engine-active', on);
			t.setAttribute('aria-pressed', on ? 'true' : 'false');
		});
		var po = document.getElementById('st-panel-ookla'), pb = document.getElementById('st-panel-browser');
		if (po) po.style.display = (engine === 'ookla') ? '' : 'none';
		if (pb) pb.style.display = (engine === 'browser') ? '' : 'none';
		this.updateStartButton();
		try { localStorage.setItem('modem_speedtest_engine', engine); } catch(e) {}
	},

	updateStartButton: function() {
		var btn = document.getElementById('btn-start-speedtest');
		if (!btn) return;
		var blocked = (this.engine === 'ookla' && !this.ooklaState.installed);
		btn.disabled = blocked || this.isRunning || this.ooklaRunning;
		btn.style.opacity = btn.disabled ? '0.5' : '1';
		btn.title = blocked ? _('Install the Speedtest CLI first') : '';
	},

	// ---- Speedtest by Ookla (runs on the router) ---------------------------
	renderOoklaPanel: function(st) {
		var self = this;
		var body = document.getElementById('st-panel-ookla');
		if (!body) return;
		st = st || {};
		this.ooklaState = st;

		if (!st.installed) {
			var accept = E('input', { 'type': 'checkbox', 'id': 'ookla-accept' });
			var btn = E('button', {
				'class': 'cbi-button cbi-button-action',
				'disabled': st.installing ? 'disabled' : null,
				'click': function(ev) {
					ev.preventDefault();
					if (!accept.checked) {
						ui.addNotification(null, E('p', _('Please accept the Speedtest CLI license first.')), 'warning');
						return;
					}
					callInstallOokla(true).then(function() { self.pollOoklaInstall(); });
				}
			}, st.installing ? _('Installing…') : _('Install Speedtest CLI'));

			dom.content(body, [
				E('div', { 'class': 'st-note' },
					_('Runs on the router with multiple connections against a nearby Ookla server, so results match the Speedtest app. The CLI is free for personal, non-commercial use and is downloaded from Ookla (about 1 MB) when you install it. It is then also available as "speedtest" in an SSH shell.')),
				E('label', { 'class': 'st-accept' }, [
					accept,
					E('span', {}, [
						_('I accept the Speedtest by Ookla '),
						E('a', { 'href': st.eula, 'target': '_blank', 'rel': 'noopener' }, _('EULA')), ', ',
						E('a', { 'href': st.terms, 'target': '_blank', 'rel': 'noopener' }, _('Terms of Use')), _(' and '),
						E('a', { 'href': st.privacy, 'target': '_blank', 'rel': 'noopener' }, _('Privacy Policy')), '.'
					])
				]),
				E('div', { 'style': 'display: flex; align-items: center; gap: 10px; flex-wrap: wrap;' }, [
					btn,
					st.error ? E('span', { 'style': 'color: #f87171; font-size: 12px;' }, _('Install failed: %s').format(st.error)) : ''
				])
			]);
			this.updateStartButton();
			return;
		}

		var serverSelect = E('select', { 'class': 'cbi-input-select st-input', 'id': 'ookla-server' }, [
			E('option', { 'value': '' }, _('Nearest server (automatic)'))
		]);
		var loadServers = E('button', {
			'class': 'cbi-button cbi-button-neutral',
			'click': function(ev) {
				ev.preventDefault();
				loadServers.disabled = true;
				loadServers.textContent = _('Loading…');
				callListOoklaServers().then(function(r) {
					(r.servers || []).forEach(function(sv) {
						serverSelect.appendChild(E('option', { 'value': String(sv.id) },
							sv.name + ' — ' + sv.location + (sv.country ? ', ' + sv.country : '')));
					});
					loadServers.textContent = (r.servers && r.servers.length) ? _('%d servers').format(r.servers.length) : _('Server list unavailable');
				}).catch(function() { loadServers.disabled = false; loadServers.textContent = _('Choose server'); });
			}
		}, _('Choose server'));

		dom.content(body, [
			E('label', { 'class': 'st-label' }, _('Server')),
			E('div', { 'style': 'display: flex; gap: 8px; flex-wrap: wrap;' }, [ serverSelect, loadServers ]),
			E('div', { 'class': 'st-hint' }, _('Speedtest CLI %s on the router · multi-connection').format(st.version || ''))
		]);
		this.updateStartButton();
	},

	pollOoklaInstall: function() {
		var self = this;
		callOoklaStatus().then(function(st) {
			self.renderOoklaPanel(st);
			if (st.installing) setTimeout(function() { self.pollOoklaInstall(); }, 2000);
		});
	},

	finishOokla: function() {
		if (this.ooklaTimer) clearInterval(this.ooklaTimer);
		this.ooklaTimer = null;
		this.ooklaRunning = false;
		this.setRunning(false);
		this.updateStartButton();
	},

	runOokla: function(serverId) {
		var self = this;
		this.ooklaRunning = true;
		this.setRunning(true);
		this.resetResults();
		this.setGaugeState('download', _('Waiting'), '#94a3b8');
		this.setGaugeState('upload', _('Waiting'), '#94a3b8');
		this.updateProgress(2, _('Starting Speedtest by Ookla…'), '#94a3b8');
		callGetSignal().then(function(sig) { self.setNetworkCard(sig); }).catch(function() {});

		callStartRouterSpeedtest('ookla', 'standard', serverId || '').then(function() {
			if (self.ooklaTimer) clearInterval(self.ooklaTimer);
			self.ooklaTimer = setInterval(function() {
				callGetSpeedtestStatus().then(function(d) {
					if (!d || d.engine !== 'ookla' || !self.ooklaRunning) return;
					self.showOoklaProgress(d);
				});
			}, 1000);
		});
	},

	showOoklaProgress: function(d) {
		var dl = Number(d.download_mbps) || 0, ul = Number(d.upload_mbps) || 0;
		var set = function(id, t) { var el = document.getElementById(id); if (el) el.textContent = t; };

		if (d.step === 'download' && dl) {
			this.peakDownload = Math.max(this.peakDownload || 0, dl);
			this.downloadPoints.push(dl);
			if (this.downloadPoints.length > this.maxPointsPerPhase) this.downloadPoints.shift();
			this.setGaugeState('download', _('Testing'), '#38bdf8', 'rgba(56,189,248,0.15)');
		}
		if (d.step === 'upload' && ul) {
			this.peakUpload = Math.max(this.peakUpload || 0, ul);
			this.uploadPoints.push(ul);
			if (this.uploadPoints.length > this.maxPointsPerPhase) this.uploadPoints.shift();
			this.setGaugeState('download', _('Done'), '#10b981', 'rgba(16,185,129,0.15)');
			this.setGaugeState('upload', _('Testing'), '#34d399', 'rgba(52,211,153,0.15)');
		}
		if (dl) { set('st-dl-val', dl.toFixed(2)); set('st-dl-peak', (this.peakDownload || dl).toFixed(2)); this.updateDial('st-dl-dial', dl, 100); }
		if (ul) { set('st-ul-val', ul.toFixed(2)); set('st-ul-peak', (this.peakUpload || ul).toFixed(2)); this.updateDial('st-ul-dial', ul, 50); }
		if (d.ping_avg) this.setLatencyCards(d.ping_avg, d.ping_min, d.ping_max, d.jitter, d.state === 'completed' ? d.packet_loss : null);
		this.drawWaveform();

		var color = d.step === 'upload' ? '#34d399' : (d.step === 'download' ? '#38bdf8' : '#94a3b8');
		if (d.state === 'failed') {
			this.updateProgress(100, _('Failed: %s').format(d.error || d.step_label || ''), '#ef4444');
			this.finishOokla();
			return;
		}
		if (d.state !== 'completed') {
			this.updateProgress(d.progress || 0, d.step_label || '', color);
			return;
		}

		this.updateProgress(100, _('Done · Download %s Mbps · Upload %s Mbps').format(dl.toFixed(1), ul.toFixed(1)), 'linear-gradient(90deg, #38bdf8, #34d399)');
		this.setGaugeState('download', _('Done'), '#10b981', 'rgba(16,185,129,0.15)');
		this.setGaugeState('upload', _('Done'), '#10b981', 'rgba(16,185,129,0.15)');
		var meta = document.getElementById('st-result-meta');
		if (meta) dom.content(meta, [
			d.server ? E('span', {}, _('Server: %s').format(d.server + (d.location ? ', ' + d.location : ''))) : '',
			d.isp ? E('span', {}, _('ISP: %s').format(d.isp)) : '',
			d.result_url ? E('a', { 'href': d.result_url, 'target': '_blank', 'rel': 'noopener' }, _('View result on speedtest.net')) : ''
		]);
		this.localHistory = [d].concat(this.localHistory || []).slice(0, 15);
		try { localStorage.setItem('modem_speedtest_history', JSON.stringify(this.localHistory)); } catch(e) {}
		this.renderHistoryTable(this.localHistory);
		this.finishOokla();
	},

	updateDial: function(dialId, val, maxVal) {
		var el = document.getElementById(dialId);
		if (!el) return;
		var num = parseFloat(val) || 0;
		var pct = Math.max(0, Math.min(100, (num / maxVal) * 100));
		var maxDash = 170; // 220 deg arc
		var currentDash = (pct / 100) * maxDash;
		el.setAttribute('stroke-dasharray', currentDash.toFixed(1) + ' 300');
	},

	drawWaveform: function() {
		var cvs = document.getElementById('st-waveform-canvas');
		if (!cvs || !cvs.getContext) return;
		var ctx = cvs.getContext('2d');
		var w = cvs.width = cvs.offsetWidth;
		var h = cvs.height = cvs.offsetHeight;
		ctx.clearRect(0, 0, w, h);

		var midX = w / 2;

		// 1. Grid Lines & Mbps Scale
		var allValues = this.downloadPoints.concat(this.uploadPoints);
		var maxVal = Math.max(10, Math.max.apply(null, allValues.length ? allValues : [10]));
		// Round up maxVal to multiple of 10
		var gridMax = Math.ceil(maxVal / 10) * 10;

		ctx.strokeStyle = 'rgba(255, 255, 255, 0.05)';
		ctx.lineWidth = 1;
		ctx.fillStyle = 'rgba(255, 255, 255, 0.3)';
		ctx.font = '9px monospace';

		var steps = 4;
		for (var s = 0; s <= steps; s++) {
			var y = h - 20 - (s / steps) * (h - 35);
			ctx.beginPath();
			ctx.moveTo(0, y);
			ctx.lineTo(w, y);
			ctx.stroke();

			var labelMbps = Math.round((s / steps) * gridMax);
			ctx.fillText(labelMbps + ' Mbps', 6, y - 3);
		}

		// 2. Middle Phase Divider: [ DOWNLOAD (Left) | UPLOAD (Right) ]
		ctx.setLineDash([4, 4]);
		ctx.strokeStyle = 'rgba(255, 255, 255, 0.15)';
		ctx.beginPath();
		ctx.moveTo(midX, 10);
		ctx.lineTo(midX, h - 15);
		ctx.stroke();
		ctx.setLineDash([]);

		ctx.fillStyle = '#38bdf8';
		ctx.fillText(_('DOWNLOAD'), 60, h - 4);
		ctx.fillStyle = '#34d399';
		ctx.fillText(_('UPLOAD'), midX + 20, h - 4);

		// 3. Draw Download Curve (Left Half: 0 to midX)
		if (this.downloadPoints.length >= 2) {
			var dlStep = (midX - 10) / (this.maxPointsPerPhase - 1);

			// Fill Path
			ctx.beginPath();
			ctx.moveTo(0, h - 20);
			for (var i = 0; i < this.downloadPoints.length; i++) {
				var dv = this.downloadPoints[i];
				var dNorm = dv / gridMax;
				var dpy = h - 20 - (dNorm * (h - 35));
				var dpx = i * dlStep;
				ctx.lineTo(dpx, dpy);
			}
			ctx.lineTo((this.downloadPoints.length - 1) * dlStep, h - 20);
			ctx.closePath();
			ctx.fillStyle = 'rgba(56, 189, 248, 0.12)';
			ctx.fill();

			// Line Path
			ctx.beginPath();
			for (var di = 0; di < this.downloadPoints.length; di++) {
				var dval = this.downloadPoints[di];
				var dn = dval / gridMax;
				var dy = h - 20 - (dn * (h - 35));
				var dx = di * dlStep;
				if (di === 0) ctx.moveTo(dx, dy);
				else ctx.lineTo(dx, dy);
			}
			ctx.strokeStyle = '#38bdf8';
			ctx.lineWidth = 2.5;
			ctx.stroke();
		}

		// 4. Draw Upload Curve (Right Half: midX to w)
		if (this.uploadPoints.length >= 2) {
			var ulStep = (w - midX - 10) / (this.maxPointsPerPhase - 1);

			// Fill Path
			ctx.beginPath();
			ctx.moveTo(midX, h - 20);
			for (var j = 0; j < this.uploadPoints.length; j++) {
				var uv = this.uploadPoints[j];
				var uNorm = uv / gridMax;
				var upy = h - 20 - (uNorm * (h - 35));
				var upx = midX + j * ulStep;
				ctx.lineTo(upx, upy);
			}
			ctx.lineTo(midX + (this.uploadPoints.length - 1) * ulStep, h - 20);
			ctx.closePath();
			ctx.fillStyle = 'rgba(52, 211, 153, 0.12)';
			ctx.fill();

			// Line Path
			ctx.beginPath();
			for (var uj = 0; uj < this.uploadPoints.length; uj++) {
				var uval = this.uploadPoints[uj];
				var un = uval / gridMax;
				var uy = h - 20 - (un * (h - 35));
				var ux = midX + uj * ulStep;
				if (uj === 0) ctx.moveTo(ux, uy);
				else ctx.lineTo(ux, uy);
			}
			ctx.strokeStyle = '#34d399';
			ctx.lineWidth = 2.5;
			ctx.stroke();
		}
	},

	renderHistoryTable: function(historyList) {
		var tbody = document.getElementById('speedtest-history-body');
		if (!tbody) return;
		dom.content(tbody, []);

		var list = historyList || this.localHistory;
		if (!list || list.length === 0) {
			tbody.appendChild(E('tr', {}, [
				E('td', { 'colspan': 8, 'style': 'text-align: center; color: var(--text-color-medium); padding: 14px; font-style: italic;' }, 
					_('No results yet.')
				)
			]));
			return;
		}

		list.forEach(function(row) {
			tbody.appendChild(E('tr', {}, [
				E('td', { 'style': 'font-family: monospace; font-size: 11px;' }, row.timestamp || '--'),
				E('td', { 'style': 'font-weight: 600;' }, [
					E('div', {}, row.server || '--'),
					E('div', { 'style': 'font-size: 10px; color: var(--text-color-medium);' },
						(row.engine === 'ookla' ? 'Ookla' : (row.engine === 'browser' ? _('Browser') : '')) + (row.location ? ' · ' + row.location : ''))
				]),
				E('td', { 'style': 'font-family: monospace; color: #38bdf8;' }, row.band || '--'),
				E('td', { 'style': 'font-family: monospace; font-weight: 600;' }, (row.ping_avg ? row.ping_avg + ' ms' : '--')),
				E('td', { 'style': 'font-family: monospace;' }, (row.jitter ? row.jitter + ' ms' : '--')),
				E('td', { 'style': 'font-family: monospace; color: ' + (row.packet_loss > 0 ? '#ef4444' : '#10b981') + ';' }, (row.packet_loss != null ? row.packet_loss + '%' : '0%')),
				E('td', { 'style': 'font-family: monospace; font-weight: 700; color: #38bdf8; font-size: 12.5px;' }, (row.download_mbps ? row.download_mbps + ' Mbps' : '--')),
				E('td', { 'style': 'font-family: monospace; font-weight: 700; color: #34d399; font-size: 12.5px;' }, (row.upload_mbps ? row.upload_mbps + ' Mbps' : '--'))
			]));
		});
	},

	updateProgress: function(pct, label, stageColor) {
		var elLabel = document.getElementById('st-step-label');
		var elPct = document.getElementById('st-progress-pct');
		var elBar = document.getElementById('st-progress-bar');
		if (elLabel) elLabel.textContent = label;
		if (elPct) elPct.textContent = Math.round(pct) + '%';
		if (elBar) {
			elBar.style.width = Math.round(pct) + '%';
			if (stageColor) elBar.style.background = stageColor;
		}
	},

	setGaugeState: function(type, text, color, bg) {
		var el = document.getElementById(type === 'download' ? 'st-dl-state-badge' : 'st-ul-state-badge');
		if (!el) return;
		el.textContent = text;
		el.style.color = color || 'var(--text-color-high)';
		el.style.background = bg || 'rgba(255,255,255,0.06)';
		el.style.border = color ? '1px solid ' + color + '44' : 'none';
	},

	runSpeedtest: async function(gwConfig, sizeConfig) {
		var self = this;
		this.isRunning = true;
		this.activeAbortController = new AbortController();
		var signal = this.activeAbortController.signal;

		this.setRunning(true);

		this.resetResults();
		this.setGaugeState('download', _('Waiting'), '#94a3b8');
		this.setGaugeState('upload', _('Waiting'), '#94a3b8');

		var rfInfo = { band: '' };
		try { rfInfo.band = this.setNetworkCard(await callGetSignal()).label; } catch(e) {}

		// ==========================================
		// PHASE 1/3: PING LATENCY & RFC 3550 JITTER
		// ==========================================
		this.updateProgress(10, _('Measuring latency…'), '#94a3b8');
		var pingTimes = [];
		var drops = 0;
		var pingUrl = gwConfig.pingUrl || 'https://speed.cloudflare.com/__down?bytes=0';

		for (var p = 0; p < 10; p++) {
			if (signal.aborted) return;
			var t0 = performance.now();
			try {
				var probeUrl = pingUrl + (pingUrl.indexOf('?') >= 0 ? '&' : '?') + 't=' + Date.now() + '_' + p;
				await fetch(probeUrl, { method: 'HEAD', cache: 'no-store', signal: signal, mode: 'cors' });
				var dt = performance.now() - t0;
				pingTimes.push(dt);
			} catch(err) {
				if (err.name === 'AbortError') return;
				drops++;
			}
			self.updateProgress(10 + p * 1.5, _('Measuring latency… %d/10').format(p + 1), '#94a3b8');
		}

		var minPing = 0, avgPing = 0, maxPing = 0, jitter = 0, lossPct = 0;
		if (pingTimes.length > 0) {
			minPing = Math.min.apply(null, pingTimes);
			maxPing = Math.max.apply(null, pingTimes);
			avgPing = pingTimes.reduce(function(a, b) { return a + b; }, 0) / pingTimes.length;

			var diffs = [];
			for (var d = 1; d < pingTimes.length; d++) {
				diffs.push(Math.abs(pingTimes[d] - pingTimes[d - 1]));
			}
			if (diffs.length > 0) {
				jitter = diffs.reduce(function(a, b) { return a + b; }, 0) / diffs.length;
			}
		}
		lossPct = Math.round((drops / 10) * 100);

		this.setLatencyCards(avgPing, minPing, maxPing, jitter, lossPct);

		// ==========================================
		// PHASE 2/3: 4G LTE DOWNLOAD SPEEDTEST (FIRST)
		// ==========================================
		var payloadMb = (sizeConfig.bytes / 1000000).toFixed(0);
		this.setGaugeState('download', _('Testing'), '#38bdf8', 'rgba(56,189,248,0.15)');

		var downBytesTarget = sizeConfig.bytes;
		var downUrl = gwConfig.downUrl + downBytesTarget + '&t=' + Date.now();
		var finalDlMbps = 0;

		try {
			var dlStart = performance.now();
			var dlResp = await fetch(downUrl, { signal: signal, cache: 'no-store', mode: 'cors' });
			var reader = dlResp.body ? dlResp.body.getReader() : null;
			var receivedBytes = 0;

			if (reader) {
				while (true) {
					if (signal.aborted) return;
					var resChunk = await reader.read();
					if (resChunk.done) break;
					receivedBytes += resChunk.value.byteLength;

					var now = performance.now();
					var elapsedSec = (now - dlStart) / 1000.0;
					if (elapsedSec > 0.08) {
						var instantMbps = (receivedBytes * 8) / (elapsedSec * 1000000.0);
						if (instantMbps > self.peakDownload) self.peakDownload = instantMbps;

						var elDl = document.getElementById('st-dl-val');
						if (elDl) elDl.textContent = instantMbps.toFixed(2);
						var elDlPeak = document.getElementById('st-dl-peak');
						if (elDlPeak) elDlPeak.textContent = self.peakDownload.toFixed(2);

						self.updateDial('st-dl-dial', instantMbps, 100);

						self.downloadPoints.push(instantMbps);
						if (self.downloadPoints.length > self.maxPointsPerPhase) self.downloadPoints.shift();
						self.drawWaveform();

						var dlPct = 25 + Math.min(35, (receivedBytes / downBytesTarget) * 35);
						self.updateProgress(dlPct, _('Download · %s Mbps').format(instantMbps.toFixed(2)), '#38bdf8');
					}
				}
				var totalDlSec = (performance.now() - dlStart) / 1000.0;
				finalDlMbps = (receivedBytes * 8) / (totalDlSec * 1000000.0);
			}
		} catch(err) {
			if (err.name === 'AbortError') return;
		}

		var elDlFinal = document.getElementById('st-dl-val');
		if (elDlFinal && finalDlMbps > 0) elDlFinal.textContent = finalDlMbps.toFixed(2);
		this.setGaugeState('download', _('Done'), '#10b981', 'rgba(16,185,129,0.15)');

		// ==========================================
		// PHASE 3/3: 4G LTE UPLOAD SPEEDTEST (1:1 EQUAL PAYLOAD)
		// ==========================================
		this.setGaugeState('upload', _('Testing'), '#34d399', 'rgba(52,211,153,0.15)');
		this.updateProgress(62, _('Preparing upload…'), '#34d399');

		var upBytesTarget = sizeConfig.bytes; // EXACT 1:1 RATIO WITH DOWNLOAD!
		var upPayload = new Uint8Array(upBytesTarget);
		for (var k = 0; k < upBytesTarget; k += 8) {
			upPayload[k] = (k & 0xff);
		}

		var upUrl = gwConfig.upUrl || 'https://speed.cloudflare.com/__up';
		var finalUlMbps = 0;

		await new Promise(function(resolveUp) {
			var xhr = new XMLHttpRequest();
			self.activeXhr = xhr;
			var ulStart = performance.now();

			xhr.upload.onprogress = function(e) {
				if (signal.aborted) {
					xhr.abort();
					return resolveUp();
				}
				var now = performance.now();
				var elapsedSec = (now - ulStart) / 1000.0;
				if (elapsedSec > 0.08 && e.loaded > 0) {
					var instantMbps = (e.loaded * 8) / (elapsedSec * 1000000.0);
					if (instantMbps > self.peakUpload) self.peakUpload = instantMbps;

					var elUl = document.getElementById('st-ul-val');
					if (elUl) elUl.textContent = instantMbps.toFixed(2);
					var elUlPeak = document.getElementById('st-ul-peak');
					if (elUlPeak) elUlPeak.textContent = self.peakUpload.toFixed(2);

					self.updateDial('st-ul-dial', instantMbps, 50);

					self.uploadPoints.push(instantMbps);
					if (self.uploadPoints.length > self.maxPointsPerPhase) self.uploadPoints.shift();
					self.drawWaveform();

					var ulPct = 62 + Math.min(36, (e.loaded / upBytesTarget) * 36);
					self.updateProgress(ulPct, _('Upload · %s Mbps').format(instantMbps.toFixed(2)), '#34d399');
				}
			};

			xhr.onload = function() {
				var totalUlSec = (performance.now() - ulStart) / 1000.0;
				if (totalUlSec > 0.05) {
					finalUlMbps = (upBytesTarget * 8) / (totalUlSec * 1000000.0);
				}
				resolveUp();
			};

			xhr.onerror = function() {
				resolveUp();
			};

			xhr.open('POST', upUrl, true);
			xhr.send(upPayload);
		});

		var elUlFinal = document.getElementById('st-ul-val');
		if (elUlFinal && finalUlMbps > 0) elUlFinal.textContent = finalUlMbps.toFixed(2);
		this.setGaugeState('upload', _('Done'), '#10b981', 'rgba(16,185,129,0.15)');

		// Redraw final full waveform with both curves intact!
		this.drawWaveform();

		// ==========================================
		// PHASE 4: FINAL COMPLETION & LOGGING
		// ==========================================
		this.updateProgress(100, _('Done · Download %s Mbps · Upload %s Mbps').format(finalDlMbps.toFixed(1), finalUlMbps.toFixed(1)), 'linear-gradient(90deg, #38bdf8, #34d399)');

		var nowD = new Date();
		var ts = nowD.getFullYear() + '-' + 
			String(nowD.getMonth() + 1).padStart(2, '0') + '-' + 
			String(nowD.getDate()).padStart(2, '0') + ' ' + 
			String(nowD.getHours()).padStart(2, '0') + ':' + 
			String(nowD.getMinutes()).padStart(2, '0') + ':' + 
			String(nowD.getSeconds()).padStart(2, '0');

		var record = {
			timestamp: ts,
			server: gwConfig.name,
			location: gwConfig.location,
			engine: 'browser',
			band: rfInfo.band,
			ping_avg: avgPing > 0 ? avgPing.toFixed(1) : '--',
			jitter: jitter > 0 ? jitter.toFixed(1) : '--',
			packet_loss: lossPct,
			download_mbps: finalDlMbps > 0 ? finalDlMbps.toFixed(2) : '--',
			upload_mbps: finalUlMbps > 0 ? finalUlMbps.toFixed(2) : '--'
		};

		this.localHistory.unshift(record);
		if (this.localHistory.length > 15) this.localHistory.pop();
		try {
			localStorage.setItem('modem_speedtest_history', JSON.stringify(this.localHistory));
		} catch(e) {}
		this.renderHistoryTable(this.localHistory);

		this.stopSpeedtest();
	},

	stopSpeedtest: function() {
		if (this.ooklaRunning) {
			var self = this;
			callCancelSpeedtest().then(function() {
				self.updateProgress(0, _('Cancelled'), '#94a3b8');
				self.finishOokla();
			});
			return;
		}
		if (this.activeAbortController) {
			this.activeAbortController.abort();
			this.activeAbortController = null;
		}
		if (this.activeXhr) {
			this.activeXhr.abort();
			this.activeXhr = null;
		}
		this.isRunning = false;
		this.setRunning(false);
		this.updateStartButton();
	},

	render: function(loadData) {
		var self = this;
		var initialSig = (loadData && loadData[0]) ? loadData[0] : null;
		var initialHist = (loadData && loadData[1]) ? loadData[1] : [];
		var ooklaInitial = (loadData && loadData[2]) ? loadData[2] : {};
		this.localHistory = initialHist || [];
		this.ooklaState = ooklaInitial;

		try {
			var cachedLocal = JSON.parse(localStorage.getItem('modem_speedtest_history') || '[]');
			if (cachedLocal && cachedLocal.length > 0) this.localHistory = cachedLocal;
		} catch(e) {}

		var startEngine = 'ookla';
		try { startEngine = localStorage.getItem('modem_speedtest_engine') || 'ookla'; } catch(e) {}
		if (startEngine !== 'browser') startEngine = 'ookla';

		// --- Browser test controls ---
		var customGwBox = E('div', { 'id': 'st-custom-gw-box', 'class': 'st-custom-grid', 'style': 'display: none;' }, [
			E('input', { 'id': 'st-custom-ping', 'type': 'text', 'class': 'cbi-input-text', 'placeholder': _('Ping URL') }),
			E('input', { 'id': 'st-custom-down', 'type': 'text', 'class': 'cbi-input-text', 'placeholder': _('Download URL (size is appended)') }),
			E('input', { 'id': 'st-custom-up', 'type': 'text', 'class': 'cbi-input-text', 'placeholder': _('Upload URL') })
		]);

		var gwSelect = E('select', {
			'id': 'st-gateway-select', 'class': 'cbi-input-select st-input',
			'change': function(ev) { customGwBox.style.display = (ev.target.value === 'custom') ? 'grid' : 'none'; }
		}, GATEWAYS.map(function(g) { return E('option', { 'value': g.id }, g.name); }));

		var sizeSelect = E('select', { 'id': 'st-size-select', 'class': 'cbi-input-select st-input' }, [
			E('option', { 'value': 'fast' }, _('Quick (5 MB each way)')),
			E('option', { 'value': 'standard', 'selected': 'selected' }, _('Standard (10 MB each way)')),
			E('option', { 'value': 'deep' }, _('Extended (25 MB each way)'))
		]);

		// --- Engine switch ---
		var engineTab = function(id, title, sub) {
			return E('button', {
				'class': 'st-engine-tab', 'data-engine': id, 'aria-pressed': 'false',
				'click': function(ev) { ev.preventDefault(); self.selectEngine(id); }
			}, [ E('span', { 'class': 'st-engine-title' }, title), E('span', { 'class': 'st-engine-sub' }, sub) ]);
		};

		var btnStart = E('button', {
			'id': 'btn-start-speedtest', 'class': 'cbi-button cbi-button-action st-start',
			'click': function(ev) {
				ev.preventDefault();
				if (self.engine === 'ookla') {
					self.runOokla((document.getElementById('ookla-server') || {}).value || '');
					return;
				}
				var gwId = gwSelect.value || 'cloudflare';
				var gw = GATEWAYS.find(function(g) { return g.id === gwId; }) || GATEWAYS[0];
				if (gwId === 'custom') {
					gw = {
						id: 'custom', name: _('Custom server'), location: _('Custom target'),
						pingUrl: document.getElementById('st-custom-ping').value || '',
						downUrl: document.getElementById('st-custom-down').value || '',
						upUrl: document.getElementById('st-custom-up').value || ''
					};
				}
				var bytes = { fast: 5000000, standard: 10000000, deep: 25000000 }[sizeSelect.value] || 10000000;
				self.runSpeedtest(gw, { bytes: bytes });
			}
		}, _('Start'));

		var btnStop = E('button', {
			'id': 'btn-stop-speedtest', 'class': 'cbi-button cbi-button-reset st-stop', 'style': 'display: none;',
			'click': function(ev) {
				ev.preventDefault();
				var wasOokla = self.ooklaRunning;
				self.stopSpeedtest();
				if (!wasOokla) self.updateProgress(0, _('Cancelled'), '#94a3b8');
				self.setGaugeState('download', _('Stopped'), '#94a3b8');
				self.setGaugeState('upload', _('Stopped'), '#94a3b8');
			}
		}, _('Stop'));

		var btnClearHist = E('button', {
			'class': 'cbi-button cbi-button-neutral', 'style': 'font-size: 11px; padding: 3px 10px;',
			'click': function(ev) {
				ev.preventDefault();
				if (!confirm(_('Clear the speedtest history?'))) return;
				self.localHistory = [];
				try { localStorage.removeItem('modem_speedtest_history'); } catch(e) {}
				self.renderHistoryTable([]);
				callClearHistory();
			}
		}, _('Clear'));

		var css = [
			'.st-card { margin: 0 0 14px; padding: 16px 18px; border: 1px solid var(--border-color-medium); border-radius: 8px; }',
			'.st-card h3 { margin: 0 0 12px; font-size: 13px; font-weight: 700; letter-spacing: .3px; }',
			'.st-engines { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 8px; margin-bottom: 14px; }',
			'.st-engine-tab { text-align: left; padding: 10px 14px; border-radius: 6px; cursor: pointer; background: rgba(255,255,255,0.03); border: 1px solid var(--border-color-medium); color: inherit; display: flex; flex-direction: column; gap: 2px; }',
			'.st-engine-tab:disabled { cursor: default; opacity: .6; }',
			'.st-engine-active { border-color: #3b82f6; background: rgba(59,130,246,0.10); box-shadow: inset 0 0 0 1px #3b82f6; }',
			'.st-engine-title { font-size: 13px; font-weight: 700; }',
			'.st-engine-sub { font-size: 11px; color: var(--text-color-medium); }',
			'.st-controls { display: flex; flex-wrap: wrap; gap: 16px; align-items: flex-end; justify-content: space-between; }',
			'.st-panel { flex: 1; min-width: 260px; }',
			'.st-label { display: block; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: .5px; color: var(--text-color-medium); margin: 0 0 5px; }',
			'.st-input { min-width: 240px; max-width: 100%; }',
			'.st-hint { font-size: 11px; color: var(--text-color-medium); margin-top: 6px; }',
			'.st-note { font-size: 12px; line-height: 1.5; color: var(--text-color-medium); margin-bottom: 10px; }',
			'.st-accept { display: flex; gap: 8px; align-items: flex-start; font-size: 12px; margin-bottom: 10px; }',
			'.st-custom-grid { grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 8px; margin-top: 8px; }',
			'.st-actions { display: flex; gap: 8px; }',
			'.st-start { min-width: 140px; padding: 9px 28px !important; font-size: 14px !important; font-weight: 700 !important; }',
			'.st-stop { padding: 9px 18px !important; }',
			'.st-progress-row { display: flex; justify-content: space-between; font-size: 12px; margin: 14px 0 6px; }',
			'.st-meta { display: flex; flex-wrap: wrap; gap: 6px 16px; font-size: 12px; color: var(--text-color-medium); margin: -4px 0 14px; }',
			'.st-meta:empty { display: none; }',
			'.st-grid-2 { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 14px; margin-bottom: 14px; }',
			'.st-grid-4 { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 12px; margin-bottom: 14px; }'
		].join('\n');

		var viewRoot = E('div', { 'class': 'cbi-map', 'style': 'padding-bottom: 30px;' }, [
			E('style', {}, css),

			// Header
			E('div', { 'style': 'margin-bottom: 16px; padding-bottom: 12px; border-bottom: 1px solid var(--border-color-medium);' }, [
				E('h2', { 'style': 'margin: 0 0 4px; font-size: 18px; font-weight: 700;' }, _('Speedtest')),
				E('div', { 'style': 'font-size: 12px; color: var(--text-color-medium);' },
					_('Measure the cellular connection from the router with Speedtest by Ookla, or from this browser.'))
			]),

			// Controls
			E('div', { 'class': 'cbi-section rf-cockpit-card st-card' }, [
				E('div', { 'class': 'st-engines' }, [
					engineTab('ookla', _('Speedtest by Ookla'), _('Runs on the router · nearest Ookla server')),
					engineTab('browser', _('Browser test'), _('This device → router → internet'))
				]),
				E('div', { 'class': 'st-controls' }, [
					E('div', { 'class': 'st-panel', 'id': 'st-panel-ookla' }, '--'),
					E('div', { 'class': 'st-panel', 'id': 'st-panel-browser', 'style': 'display: none;' }, [
						E('div', { 'style': 'display: flex; flex-wrap: wrap; gap: 12px;' }, [
							E('div', {}, [ E('label', { 'class': 'st-label' }, _('Server')), gwSelect ]),
							E('div', {}, [ E('label', { 'class': 'st-label' }, _('Data size')), sizeSelect ])
						]),
						customGwBox
					]),
					E('div', { 'class': 'st-actions' }, [ btnStart, btnStop ])
				]),
				E('div', { 'class': 'st-progress-row' }, [
					E('span', { 'id': 'st-step-label', 'style': 'font-weight: 600;' }, _('Ready')),
					E('span', { 'id': 'st-progress-pct', 'style': 'font-family: monospace;' }, '0%')
				]),
				E('div', { 'style': 'width: 100%; height: 5px; background: rgba(255,255,255,0.08); border-radius: 3px; overflow: hidden;' }, [
					E('div', { 'id': 'st-progress-bar', 'style': 'width: 0%; height: 100%; background: #38bdf8; border-radius: 3px; transition: width 0.25s ease;' })
				])
			]),

			// Gauges
			E('div', { 'class': 'st-grid-2' }, [
				E('div', { 'class': 'cbi-section rf-cockpit-card st-card', 'style': 'margin: 0; display: flex; justify-content: center;' }, [
					createDialGaugeSvg(_('Download'), 'st-dl-dial', 'st-dl-val', 'st-dl-peak', 'st-dl-state-badge', 100, 'Mbps', '#38bdf8')
				]),
				E('div', { 'class': 'cbi-section rf-cockpit-card st-card', 'style': 'margin: 0; display: flex; justify-content: center;' }, [
					createDialGaugeSvg(_('Upload'), 'st-ul-dial', 'st-ul-val', 'st-ul-peak', 'st-ul-state-badge', 50, 'Mbps', '#34d399')
				])
			]),
			E('div', { 'class': 'st-meta', 'id': 'st-result-meta' }),

			// Metrics
			E('div', { 'class': 'st-grid-4' }, [
				createMetricCard(_('Latency'), 'st-ping-val', 'st-ping-sub', 'st-ping-badge', '--', '--', '#38bdf8'),
				createMetricCard(_('Jitter'), 'st-jitter-val', 'st-jitter-sub', 'st-jitter-badge', '--', _('Variation between pings'), '#a78bfa'),
				createMetricCard(_('Packet loss'), 'st-loss-val', 'st-loss-sub', 'st-loss-badge', '--', '--', '#10b981'),
				createMetricCard(_('Network'), 'st-radio-val', 'st-radio-sub', 'st-radio-badge', '--', '--', '#f59e0b')
			]),

			// Throughput chart
			E('div', { 'class': 'cbi-section rf-cockpit-card st-card' }, [
				E('div', { 'style': 'display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;' }, [
					E('h3', { 'style': 'margin: 0;' }, _('Throughput')),
					E('div', { 'style': 'display: flex; gap: 14px; font-size: 11px;' }, [
						E('span', { 'style': 'color: #38bdf8;' }, '● ' + _('Download')),
						E('span', { 'style': 'color: #34d399;' }, '● ' + _('Upload'))
					])
				]),
				E('div', { 'style': 'width: 100%; height: 150px; background: rgba(0,0,0,0.22); border: 1px solid var(--border-color-medium); border-radius: 6px; overflow: hidden;' }, [
					E('canvas', { 'id': 'st-waveform-canvas', 'style': 'width: 100%; height: 100%; display: block;' })
				])
			]),

			// History
			E('div', { 'class': 'cbi-section rf-cockpit-card st-card', 'style': 'margin: 0;' }, [
				E('div', { 'style': 'display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;' }, [
					E('h3', { 'style': 'margin: 0;' }, _('History')),
					btnClearHist
				]),
				E('div', { 'style': 'overflow-x: auto;' }, [
					E('table', { 'class': 'table', 'style': 'margin: 0; font-size: 12px;' }, [
						E('thead', {}, [
							E('tr', {}, [
								E('th', {}, _('Time')),
								E('th', {}, _('Server')),
								E('th', {}, _('Network')),
								E('th', {}, _('Ping')),
								E('th', {}, _('Jitter')),
								E('th', {}, _('Loss')),
								E('th', {}, _('Download')),
								E('th', {}, _('Upload'))
							])
						]),
						E('tbody', { 'id': 'speedtest-history-body' })
					])
				])
			])
		]);

		setTimeout(function() {
			self.renderHistoryTable(self.localHistory);
			self.renderOoklaPanel(ooklaInitial);
			self.selectEngine(startEngine);
			self.setNetworkCard(initialSig);
			self.setGaugeState('download', _('Idle'), '#94a3b8');
			self.setGaugeState('upload', _('Idle'), '#94a3b8');
			if (ooklaInitial.installing) self.pollOoklaInstall();
		}, 60);

		return viewRoot;
	},

	handleSaveApply: null,
	handleSave: null,
	handleReset: null
});
