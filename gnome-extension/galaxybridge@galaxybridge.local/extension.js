import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import * as QuickSettings from 'resource:///org/gnome/shell/ui/quickSettings.js';


const SERVICE = 'galaxybridge-buds.service';
const STATUS_MAX_AGE = 25;
const NOISE_REFRESH_AGE = 12;

const MODE_LABELS = {
    off: 'Désactivée',
    anc: 'Réduction de bruit',
    ambient: 'Son ambiant',
};

const AUDIO_LABELS = {
    music: 'Musique (A2DP)',
    call: 'Appel sur PC (10 min)',
};

const STATE_LABELS = {
    starting: 'Démarrage…',
    disconnected: 'En attente des écouteurs',
    connecting: 'Connexion…',
    connected: 'Audio connecté · multipoint non vérifié',
    patching: 'Activation multipoint…',
    ready: 'Prêts',
    error: 'Multipoint indisponible',
    stopped: 'Service arrêté',
};

const DETAIL_LABELS = {
    patch_cooldown: 'Activation différée pour éviter les interruptions répétées',
    regular_session_released: 'Préparation sécurisée du multipoint…',
    restore_pending: 'Reconnexion audio en cours…',
    patch_failed: 'Nouvelle tentative programmée…',
    unexpected_patch_failure: 'Le service doit être relancé',
    bluez_monitor_unavailable: 'Bluetooth indisponible',
    phone_priority: 'Téléphone prioritaire · reconnexion PC suspendue',
    session_restored: 'Session multipoint vérifiée réutilisée',
    multipoint_restore_required: 'Connexion audio prête · restaurez le multipoint seulement si nécessaire',
    audio_mode_changing: 'Changement du profil audio…',
    audio_mode_failed: 'Le profil audio n’a pas pu être changé',
    phone_off_then_activate: 'Coupez le Bluetooth du téléphone, fermez le boîtier 15 s, puis activez',
    multipoint_activating: 'Connexion PC et vérification asVer=2…',
    reconnect_phone_now: 'Réactivez maintenant le Bluetooth du téléphone',
    multipoint_activate_failed: 'Activation échouée · laissez le téléphone désactivé et réessayez',
    pc_audio_restore_required: 'asVer vérifié · réessayez seulement la connexion audio PC',
};


function readJson(path) {
    try {
        const [ok, contents] = GLib.file_get_contents(path);
        if (!ok)
            return null;
        return JSON.parse(new TextDecoder().decode(contents));
    } catch (_error) {
        return null;
    }
}


function runCommand(argv, callback) {
    let process;
    try {
        process = Gio.Subprocess.new(
            argv,
            Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE
        );
    } catch (error) {
        callback(false, '', error.message);
        return null;
    }
    process.communicate_utf8_async(null, null, (source, result) => {
        try {
            const [, stdout, stderr] = source.communicate_utf8_finish(result);
            callback(source.get_successful(), stdout ?? '', stderr ?? '');
        } catch (error) {
            callback(false, '', error.message);
        }
    });
    return process;
}


const GalaxyBridgeToggle = GObject.registerClass(
class GalaxyBridgeToggle extends QuickSettings.QuickMenuToggle {
    constructor(extensionObject) {
        super({
            title: 'Galaxy Buds',
            subtitle: 'Lecture de l’état…',
            iconName: 'audio-headphones-symbolic',
            toggleMode: true,
        });

        this._extension = extensionObject;
        this._destroyed = false;
        this._reading = false;
        this._changing = false;
        this._reconnecting = false;
        this._serviceActive = false;
        this._controlAvailable = false;
        this._mode = null;
        this._audioMode = 'music';
        this._multipointStage = 'idle';
        this._noiseUpdatedAt = 0;
        this._pendingMode = null;
        this._readToken = 0;
        this._menuOpen = false;

        const cacheRoot = GLib.get_user_cache_dir();
        this._cacheDirectory = GLib.build_filenamev([cacheRoot, 'galaxybridge']);
        this._statusPath = GLib.build_filenamev([this._cacheDirectory, 'buds-status.json']);
        this._noisePath = GLib.build_filenamev([this._cacheDirectory, 'noise-control.json']);
        this._controlPath = GLib.build_filenamev([GLib.get_home_dir(), '.local', 'bin', 'galaxybridgectl']);

        this.menu.setHeader(
            'audio-headphones-symbolic',
            'Galaxy Buds',
            'Contrôle du bruit'
        );

        this._statusItem = new PopupMenu.PopupMenuItem('Lecture de l’état…', {
            reactive: false,
            can_focus: false,
        });
        this.menu.addMenuItem(this._statusItem);
        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

        this._modeItems = new Map();
        for (const mode of ['anc', 'ambient', 'off']) {
            const item = new PopupMenu.PopupMenuItem(MODE_LABELS[mode]);
            item.connect('activate', () => this._setNoiseMode(mode));
            this._modeItems.set(mode, item);
            this.menu.addMenuItem(item);
        }

        this._cycleItem = new PopupMenu.PopupMenuItem('Basculer ANC ↔ Son ambiant');
        this._cycleItem.connect('activate', () => {
            const target = this._mode === 'anc' ? 'ambient' : 'anc';
            this._setNoiseMode(target);
        });
        this.menu.addMenuItem(this._cycleItem);

        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        this._refreshItem = new PopupMenu.PopupMenuItem('Vérifier le mode');
        this._refreshItem.connect('activate', () => this._readNoiseMode(true));
        this.menu.addMenuItem(this._refreshItem);

        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        this._audioItems = new Map();
        for (const mode of ['music', 'call']) {
            const item = new PopupMenu.PopupMenuItem(AUDIO_LABELS[mode]);
            item.connect('activate', () => this._setAudioMode(mode));
            this._audioItems.set(mode, item);
            this.menu.addMenuItem(item);
        }

        this._reconnectItem = new PopupMenu.PopupMenuItem('Reconnecter les écouteurs');
        this._reconnectItem.connect('activate', () => this._reconnect());
        this.menu.addMenuItem(this._reconnectItem);

        this._multipointItem = new PopupMenu.PopupMenuItem('Restaurer le multipoint…');
        this._multipointItem.connect('activate', () => this._multipointAction());
        this.menu.addMenuItem(this._multipointItem);

        this.connect('clicked', () => this._toggleService());
        this.menu.connect('open-state-changed', (_menu, isOpen) => {
            this._menuOpen = isOpen;
            if (isOpen) {
                this._refresh();
            }
        });

        this._installFileMonitor();
        this._refresh();
        // File monitoring is immediate. This slower timer is only a stale
        // heartbeat fallback for session resume and monitor backend failures.
        this._timerId = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 15, () => {
            this._refresh();
            return GLib.SOURCE_CONTINUE;
        });
    }

    _installFileMonitor() {
        this._cacheMonitor = null;
        this._cacheMonitorId = 0;
        try {
            GLib.mkdir_with_parents(this._cacheDirectory, 0o700);
            const directory = Gio.File.new_for_path(this._cacheDirectory);
            this._cacheMonitor = directory.monitor_directory(Gio.FileMonitorFlags.WATCH_MOVES, null);
            this._cacheMonitorId = this._cacheMonitor.connect('changed', (_monitor, file, otherFile) => {
                const names = [file, otherFile]
                    .filter(candidate => candidate !== null)
                    .map(candidate => candidate.get_basename());
                if (names.some(name => name === 'buds-status.json' || name === 'noise-control.json'))
                    this._scheduleRefresh();
            });
        } catch (_error) {
            this._cacheMonitor = null;
        }
    }

    _scheduleRefresh() {
        if (this._destroyed || this._refreshId)
            return;
        this._refreshId = GLib.timeout_add_once(GLib.PRIORITY_DEFAULT, 40, () => {
            this._refreshId = 0;
            this._refresh();
        });
    }

    _fresh(payload, maxAge = STATUS_MAX_AGE) {
        if (!payload || typeof payload.updated_at !== 'number')
            return false;
        return Date.now() / 1000 - payload.updated_at <= maxAge;
    }

    _refresh() {
        if (this._destroyed)
            return;

        const status = readJson(this._statusPath);
        const fresh = this._fresh(status);
        const state = fresh ? status.state : 'stopped';
        this._serviceActive = fresh && state !== 'stopped';
        this._controlAvailable = this._serviceActive &&
            (status?.control_available === true || state === 'ready');
        if (AUDIO_LABELS[status?.audio_mode])
            this._audioMode = status.audio_mode;
        if (['idle', 'phone_off', 'activating', 'pc_restore', 'phone_on'].includes(status?.multipoint_stage))
            this._multipointStage = status.multipoint_stage;
        this.checked = this._serviceActive;

        const noise = readJson(this._noisePath);
        if (noise?.state === 'ready' && MODE_LABELS[noise.mode]) {
            this._mode = noise.mode;
            this._noiseUpdatedAt = Number(noise.updated_at) || 0;
        }

        const battery = Number.isInteger(status?.battery) ? ` · ${status.battery}%` : '';
        let base = STATE_LABELS[state] ?? 'État inconnu';
        if (this._reconnecting)
            base = 'Reconnexion…';
        else if (this._changing)
            base = 'Changement du mode…';
        else if (this._controlAvailable && this._mode)
            base = `${base} · ${MODE_LABELS[this._mode]}`;
        this.subtitle = `${base}${battery}`;

        let detail = DETAIL_LABELS[status?.detail] ?? this.subtitle;
        if (state === 'error' && this._controlAvailable)
            detail = 'Contrôle du bruit disponible · multipoint en reprise';
        else if (this._reading && !this._changing)
            detail = 'Vérification du mode en arrière-plan…';
        this._statusItem.label.text = detail;

        for (const [mode, item] of this._modeItems) {
            // Background reads never make the controls unavailable. Only an
            // actual write is serialized and temporarily disables them.
            item.sensitive = this._controlAvailable && !this._changing;
            item.setOrnament(mode === this._mode
                ? PopupMenu.Ornament.CHECK
                : PopupMenu.Ornament.NONE);
        }
        this._cycleItem.sensitive = this._controlAvailable && !this._changing;
        this._refreshItem.sensitive = this._controlAvailable && !this._reading && !this._changing;
        for (const [mode, item] of this._audioItems) {
            item.sensitive = this._serviceActive && !this._changing && !this._reconnecting;
            item.setOrnament(mode === this._audioMode
                ? PopupMenu.Ornament.CHECK
                : PopupMenu.Ornament.NONE);
        }
        this._reconnectItem.sensitive = this._serviceActive && !this._reconnecting && !this._changing;
        const stageLabels = {
            idle: 'Restaurer le multipoint…',
            phone_off: 'Téléphone coupé : activer multipoint',
            activating: 'Activation multipoint…',
            pc_restore: 'asVer vérifié : reconnecter l’audio PC',
            phone_on: 'Téléphone reconnecté : terminer',
        };
        this._multipointItem.label.text = stageLabels[this._multipointStage] ?? stageLabels.idle;
        this._multipointItem.sensitive = this._serviceActive && !this._changing &&
            !this._reconnecting && this._multipointStage !== 'activating';
        this._extension.setIndicatorVisible(this._serviceActive);
    }

    _toggleService() {
        if (this._changing || this._reconnecting)
            return;
        const action = this._serviceActive ? 'stop' : 'start';
        runCommand(['systemctl', '--user', action, SERVICE], (success, _stdout, stderr) => {
            if (this._destroyed)
                return;
            if (!success)
                Main.notify('GalaxyBridge', stderr.trim() || 'Impossible de modifier le service Galaxy Buds.');
            GLib.timeout_add_once(GLib.PRIORITY_DEFAULT, 500, () => this._refresh());
        });
    }

    _scheduleNoiseRead() {
        if (!this._controlAvailable || this._reading || this._changing)
            return;
        const age = Date.now() / 1000 - this._noiseUpdatedAt;
        if (this._mode && age <= NOISE_REFRESH_AGE)
            return;
        if (this._noiseReadId)
            GLib.source_remove(this._noiseReadId);
        // Give an immediate user click priority over a background refresh.
        this._noiseReadId = GLib.timeout_add_once(GLib.PRIORITY_DEFAULT, 250, () => {
            this._noiseReadId = 0;
            if (this._menuOpen)
                this._readNoiseMode(false);
        });
    }

    _readNoiseMode(showErrors) {
        if (!this._controlAvailable || this._reading || this._changing)
            return;
        this._reading = true;
        const token = ++this._readToken;
        this._refresh();
        runCommand(
            [this._controlPath, 'buds', 'noise-control', 'status'],
            (success, stdout, stderr) => {
                if (this._destroyed || token !== this._readToken)
                    return;
                this._reading = false;
                if (success) {
                    try {
                        const result = JSON.parse(stdout);
                        if (MODE_LABELS[result.reported_mode]) {
                            this._mode = result.reported_mode;
                            this._noiseUpdatedAt = Date.now() / 1000;
                        }
                    } catch (_error) {
                        // The atomically updated cache remains the fallback.
                    }
                } else if (showErrors) {
                    Main.notify('GalaxyBridge', stderr.trim() || 'Le mode actuel n’a pas pu être lu.');
                }
                if (this._pendingMode) {
                    const pending = this._pendingMode;
                    this._pendingMode = null;
                    this._executeSet(pending);
                } else {
                    this._refresh();
                }
            }
        );
    }

    _setNoiseMode(mode) {
        if (!this._controlAvailable || this._changing || !MODE_LABELS[mode])
            return;
        if (this._noiseReadId) {
            GLib.source_remove(this._noiseReadId);
            this._noiseReadId = 0;
        }
        this._changing = true;
        this._refresh();
        if (this._reading) {
            this._pendingMode = mode;
            return;
        }
        this._executeSet(mode);
    }

    _executeSet(mode) {
        runCommand(
            [this._controlPath, 'buds', 'noise-control', 'set', mode],
            (success, stdout, stderr) => {
                if (this._destroyed)
                    return;
                this._changing = false;
                if (success) {
                    try {
                        const result = JSON.parse(stdout);
                        if (result.reported_mode === mode) {
                            this._mode = mode;
                            this._noiseUpdatedAt = Date.now() / 1000;
                        }
                    } catch (_error) {
                        // The verified cache file is read during refresh.
                    }
                } else {
                    Main.notify(
                        'GalaxyBridge',
                        stderr.trim() || 'Les écouteurs n’ont pas confirmé le changement de mode.'
                    );
                }
                this._refresh();
            }
        );
    }

    _reconnect() {
        if (!this._serviceActive || this._reconnecting || this._changing)
            return;
        this._reconnecting = true;
        this._refresh();
        runCommand([this._controlPath, 'buds', 'connect'], (success, _stdout, stderr) => {
            if (this._destroyed)
                return;
            this._reconnecting = false;
            if (!success)
                Main.notify('GalaxyBridge', stderr.trim() || 'Les écouteurs ne sont pas encore disponibles.');
            this._refresh();
        });
    }

    _setAudioMode(mode) {
        if (!this._serviceActive || this._changing || !AUDIO_LABELS[mode])
            return;
        this._changing = true;
        this._refresh();
        runCommand([this._controlPath, 'buds', 'audio-mode', 'set', mode], (success, stdout, stderr) => {
            if (this._destroyed)
                return;
            this._changing = false;
            if (success) {
                this._audioMode = mode;
                if (mode === 'call')
                    Main.notify('GalaxyBridge', 'Mode appel PC actif pendant 10 minutes. Le téléphone peut céder temporairement sa place.');
            } else {
                Main.notify('GalaxyBridge', stderr.trim() || 'Le profil audio n’a pas pu être changé.');
            }
            this._refresh();
        });
    }

    _multipointAction() {
        if (!this._serviceActive || this._changing || this._reconnecting)
            return;
        const commands = {
            idle: 'prepare',
            phone_off: 'activate',
            pc_restore: 'activate',
            phone_on: 'done',
        };
        const command = commands[this._multipointStage];
        if (!command)
            return;
        this._changing = true;
        this._refresh();
        runCommand([this._controlPath, 'buds', 'multipoint', command], (success, stdout, stderr) => {
            if (this._destroyed)
                return;
            this._changing = false;
            if (success) {
                try {
                    const result = JSON.parse(stdout);
                    if (result.stage)
                        this._multipointStage = result.stage;
                } catch (_error) {
                    // The status file is authoritative and refreshes below.
                }
                if (command === 'prepare')
                    Main.notify('GalaxyBridge', 'Désactivez le Bluetooth du téléphone, fermez le boîtier 15 secondes, puis choisissez « activer multipoint ».');
                else if (command === 'activate')
                    Main.notify('GalaxyBridge', 'PC vérifié. Réactivez le téléphone, connectez les Buds, puis choisissez « terminer ».');
                else
                    Main.notify('GalaxyBridge', 'Session multipoint terminée. Le téléphone reste prioritaire.');
            } else {
                let message = stderr.trim();
                if (!message) {
                    try {
                        message = JSON.parse(stdout).error ?? '';
                    } catch (_error) {
                        // Keep the concise fallback below.
                    }
                }
                Main.notify('GalaxyBridge', message || 'L’étape multipoint a échoué.');
            }
            this._refresh();
        });
    }

    destroy() {
        this._destroyed = true;
        this._readToken++;
        for (const id of [this._timerId, this._refreshId, this._noiseReadId]) {
            if (id)
                GLib.source_remove(id);
        }
        this._timerId = 0;
        this._refreshId = 0;
        this._noiseReadId = 0;
        if (this._cacheMonitor) {
            if (this._cacheMonitorId)
                this._cacheMonitor.disconnect(this._cacheMonitorId);
            this._cacheMonitor.cancel();
            this._cacheMonitor = null;
        }
        super.destroy();
    }
});


const GalaxyBridgeIndicator = GObject.registerClass(
class GalaxyBridgeIndicator extends QuickSettings.SystemIndicator {
    constructor(extensionObject) {
        super();
        this._indicator = this._addIndicator();
        this._indicator.icon_name = 'audio-headphones-symbolic';
        this._indicator.visible = false;
        this.quickSettingsItems.push(new GalaxyBridgeToggle(extensionObject));
    }

    setVisible(visible) {
        this._indicator.visible = visible;
    }

    destroy() {
        this.quickSettingsItems.forEach(item => item.destroy());
        super.destroy();
    }
});


export default class GalaxyBridgeExtension extends Extension {
    enable() {
        this._indicator = new GalaxyBridgeIndicator(this);
        Main.panel.statusArea.quickSettings.addExternalIndicator(this._indicator);
    }

    setIndicatorVisible(visible) {
        this._indicator?.setVisible(visible);
    }

    disable() {
        this._indicator?.destroy();
        this._indicator = null;
    }
}
