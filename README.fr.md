# GalaxyBridge

Contrôles Linux pour les Samsung Galaxy Buds : panneau graphique indépendant du
bureau, menu terminal, CLI et extension GNOME facultative.

**Projet expérimental. La reconnexion automatique aux deux appareils après un
passage dans le boîtier n'est pas résolue.** Les tests logiciels réussis ne
constituent pas une validation matérielle de tous les modèles.

## Installation

Une seule commande sur n'importe quelle distribution Linux systemd.
L'installateur détecte la distribution, installe les bons paquets, puis
configure les écouteurs :

```sh
curl -fsSL https://raw.githubusercontent.com/hermesagentme-web/galaxybridge/main/install.sh | bash
```

Depuis un clone local :

```sh
bash scripts/install.sh --system-packages --configure
bash scripts/install.sh --help    # toutes les options (--yes, --no-gnome, ...)
```

| Famille de distribution | Paquets installés automatiquement |
| --- | --- |
| Debian/Ubuntu/Mint/Pop!_OS | `python3 python3-venv python3-pip python3-dbus python3-gi gir1.2-gtk-3.0 bluez` |
| Fedora/RHEL/CentOS | `python3 python3-pip python3-dbus python3-gobject gtk3 bluez` |
| Arch/Manjaro | `python python-pip python-dbus python-gobject gtk3 bluez bluez-utils` |
| openSUSE/SLES | `python3 python3-pip python3-dbus-python python3-gobject-Gdk typelib-1_0-Gtk-3_0 bluez` |

L'extension GNOME Shell 50 est copiée automatiquement sous GNOME 50 ; tous les
autres bureaux (KDE, Xfce, Cinnamon, MATE…) utilisent le panneau GTK
(`galaxybridge-ui`) ou le menu terminal (`galaxybridgectl menu`). Le panneau suit
le thème du bureau, clair ou sombre. PipeWire et WirePlumber sont nécessaires
pour les commandes de profil audio.

L'installateur préserve la configuration existante et ne connecte pas les
écouteurs. Pour GNOME 50, ferme puis rouvre la session, puis active
`galaxybridge@galaxybridge.local` :

```sh
gnome-extensions enable galaxybridge@galaxybridge.local
```

N'active pas l'ancienne extension `galaxybridge@kotazi.local` si elle est encore
présente : les menus apparaîtraient en double.

## Utilisation

```sh
galaxybridge-ui        # panneau graphique (tous les bureaux)
galaxybridgectl menu   # menu interactif en terminal (serveurs, TTYs)
```

Le panneau et le menu permettent la connexion PC, la lecture du mode de bruit,
les modes ANC, ambiant et désactivé, le profil musique et l'assistant multipoint.
La CLI offre les mêmes commandes. `galaxybridgectl buds capabilities` examine
les services annoncés sans ouvrir de connexion de contrôle.

Le multipoint nécessite actuellement une activation explicite par session :
préparer, couper le Bluetooth du téléphone, remettre brièvement les Buds dans
le boîtier, les sortir, activer, puis reconnecter le téléphone et confirmer.
Ne relance pas cette procédure sur une session qui fonctionne.

Après validation sur ton modèle, `GALAXYBRIDGE_BUDS_AUTO_PATCH=true` permet
de renouveler l'activation lors d'une nouvelle connexion. Redémarre le service
après modification. Garde `GALAXYBRIDGE_BUDS_AUTO_CONNECT=false` : le renouvellement
n'implique pas de boucle de reconnexion PC. Les écritures sont espacées de
60 secondes minimum. La reconnexion spontanée du téléphone reste à valider.

Le projet prend en charge des fonctions selon les services du modèle, mais
ne garantit pas toutes les fonctions sur tous les Buds. L'ANC est testé localement
sur Buds4 Pro ; les autres modèles nécessitent une validation du protocole.
Aucun flash du firmware ni réinitialisation n'est fourni.

Consulte le [README complet](README.md), la
[matrice de compatibilité](docs/compatibility.md) et le
[protocole de test](docs/test-plan.md). Les diagnostics peuvent contenir des noms
d'appareils ou de médias : relis-les avant de les publier.

Licence MIT. Projet non officiel, sans affiliation à Samsung.
