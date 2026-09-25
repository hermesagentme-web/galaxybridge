"""Lightweight French/English labels for the panel and terminal menu.

The language follows the session locale, with an optional GALAXYBRIDGE_LANG
override (values: fr, en). The GNOME extension ships French labels only.
"""
from __future__ import annotations

import locale
import os

STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "app.title": "GalaxyBridge",
        "app.subtitle": "Galaxy Buds · Linux controls",
        "state.starting": "Starting…",
        "state.disconnected": "Waiting for the earbuds",
        "state.connecting": "Connecting…",
        "state.connected": "Audio connected · multipoint unverified",
        "state.patching": "Enabling multipoint…",
        "state.ready": "Ready",
        "state.error": "Multipoint unavailable",
        "state.stopped": "Service stopped",
        "state.unknown": "Unknown state",
        "device.title": "Earbuds",
        "device.battery": "Battery {value}%",
        "device.connect": "Connect PC audio",
        "device.refresh": "Refresh",
        "device.service_start": "Start service",
        "device.service_stop": "Stop service",
        "noise.title": "Noise control",
        "noise.anc": "Noise cancelling",
        "noise.ambient": "Ambient sound",
        "noise.off": "Off",
        "noise.read": "Read current mode",
        "noise.unknown": "Mode unknown · read it first",
        "audio.title": "Audio profile",
        "audio.music": "Music (A2DP)",
        "audio.call": "PC call (10 min)",
        "audio.call_warning": "Call mode can interrupt phone use and needs a live daemon.",
        "multipoint.title": "Experimental multipoint",
        "multipoint.hint": "Guided phone-safe flow. A working session is never re-patched blindly.",
        "multipoint.prepare": "1. Prepare",
        "multipoint.activate": "2. Activate",
        "multipoint.done": "3. Finish",
        "diagnostics.title": "Diagnostics",
        "diagnostics.capabilities": "Inspect capabilities",
        "diagnostics.doctor": "Run doctor",
        "diagnostics.output": "Output",
        "busy": "Working…",
        "done": "Done",
        "menu.heading": "GalaxyBridge · Terminal menu",
        "menu.status": "Earbuds status",
        "menu.anc": "Noise control · Noise cancelling",
        "menu.ambient": "Noise control · Ambient sound",
        "menu.off": "Noise control · Off",
        "menu.music": "Audio profile · Music (A2DP)",
        "menu.call": "Audio profile · PC call (10 min)",
        "menu.connect": "Connect PC audio",
        "menu.multipoint": "Multipoint wizard (experimental)",
        "menu.doctor": "Diagnostics · doctor",
        "menu.capabilities": "Inspect device capabilities",
        "menu.service": "Service · start / stop",
        "menu.quit": "Quit",
        "menu.prompt": "Choice",
        "menu.invalid": "Invalid choice, try again.",
        "menu.next_step": "Next multipoint step",
        "menu.multipoint.1": "Prepare (disconnects the PC)",
        "menu.multipoint.2": "Activate (after the case cycle)",
        "menu.multipoint.3": "Finish (phone reconnected)",
        "menu.multipoint.back": "Back",
        "menu.no_tty": "galaxybridgectl menu needs an interactive terminal.",
    },
    "fr": {
        "app.title": "GalaxyBridge",
        "app.subtitle": "Galaxy Buds · commandes Linux",
        "state.starting": "Démarrage…",
        "state.disconnected": "En attente des écouteurs",
        "state.connecting": "Connexion…",
        "state.connected": "Audio connecté · multipoint non vérifié",
        "state.patching": "Activation multipoint…",
        "state.ready": "Prêts",
        "state.error": "Multipoint indisponible",
        "state.stopped": "Service arrêté",
        "state.unknown": "État inconnu",
        "device.title": "Écouteurs",
        "device.battery": "Batterie {value} %",
        "device.connect": "Connecter l’audio du PC",
        "device.refresh": "Actualiser",
        "device.service_start": "Démarrer le service",
        "device.service_stop": "Arrêter le service",
        "noise.title": "Réduction de bruit",
        "noise.anc": "Réduction de bruit",
        "noise.ambient": "Son ambiant",
        "noise.off": "Désactivée",
        "noise.read": "Lire le mode actuel",
        "noise.unknown": "Mode inconnu · lisez-le d’abord",
        "audio.title": "Profil audio",
        "audio.music": "Musique (A2DP)",
        "audio.call": "Appel sur PC (10 min)",
        "audio.call_warning": "Le mode appel peut interrompre l’usage du téléphone et exige un daemon actif.",
        "multipoint.title": "Multipoint expérimental",
        "multipoint.hint": "Parcours guidé sans risque pour le téléphone. Une session déjà fonctionnelle n’est jamais réécrite à l’aveugle.",
        "multipoint.prepare": "1. Préparer",
        "multipoint.activate": "2. Activer",
        "multipoint.done": "3. Terminer",
        "diagnostics.title": "Diagnostic",
        "diagnostics.capabilities": "Inspecter les capacités",
        "diagnostics.doctor": "Lancer doctor",
        "diagnostics.output": "Sortie",
        "busy": "En cours…",
        "done": "Terminé",
        "menu.heading": "GalaxyBridge · menu terminal",
        "menu.status": "État des écouteurs",
        "menu.anc": "Réduction de bruit · ANC",
        "menu.ambient": "Réduction de bruit · Son ambiant",
        "menu.off": "Réduction de bruit · Désactivée",
        "menu.music": "Profil audio · Musique (A2DP)",
        "menu.call": "Profil audio · Appel PC (10 min)",
        "menu.connect": "Connecter l’audio du PC",
        "menu.multipoint": "Assistant multipoint (expérimental)",
        "menu.doctor": "Diagnostic · doctor",
        "menu.capabilities": "Inspecter les capacités de l’appareil",
        "menu.service": "Service · démarrer / arrêter",
        "menu.quit": "Quitter",
        "menu.prompt": "Choix",
        "menu.invalid": "Choix invalide, réessayez.",
        "menu.next_step": "Étape multipoint suivante",
        "menu.multipoint.1": "Préparer (déconnecte le PC)",
        "menu.multipoint.2": "Activer (après le cycle du boîtier)",
        "menu.multipoint.3": "Terminer (téléphone reconnecté)",
        "menu.multipoint.back": "Retour",
        "menu.no_tty": "galaxybridgectl menu exige un terminal interactif.",
    },
}


def current_language() -> str:
    override = (os.environ.get("GALAXYBRIDGE_LANG") or "").strip().lower()
    if override in STRINGS:
        return override
    try:
        preferred = locale.getlocale()[0] or ""
    except ValueError:
        preferred = ""
    language = (preferred or os.environ.get("LANG") or "en").lower()
    return "fr" if language.startswith("fr") else "en"


def t(key: str, **kwargs: object) -> str:
    table = STRINGS.get(current_language(), STRINGS["en"])
    template = table.get(key) or STRINGS["en"].get(key) or key
    return template.format(**kwargs) if kwargs else template
