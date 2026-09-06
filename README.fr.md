# GalaxyBridge

Contrôles Linux pour les Samsung Galaxy Buds : panneau graphique indépendant du
bureau, CLI et extension GNOME facultative.

**Projet expérimental. La reconnexion automatique aux deux appareils après un
passage dans le boîtier n'est pas résolue.** Les tests logiciels réussis ne
constituent pas une validation matérielle de tous les modèles.

## Installation

Sur Debian/Ubuntu :

```sh
sudo apt install python3-venv python3-pip python3-dbus python3-gi python3-tk bluez
bash scripts/install.sh
# Ou, pour ajouter l'extension GNOME Shell 50 :
bash scripts/install.sh --gnome
export PATH="$HOME/.local/bin:$PATH"
bluetoothctl devices
```

Renseigne l'adresse dans `~/.config/galaxybridge/environment`, puis :

```sh
systemctl --user enable --now galaxybridge-buds.service
galaxybridge-ui
```

Le panneau fonctionne indépendamment de GNOME, notamment sous KDE, Xfce,
Cinnamon et MATE si Python Tk est installé. Ces bureaux restent à valider
matériellement. Sans systemd, lance `galaxybridge-buds-daemon` dans un terminal
avec la variable `GALAXYBRIDGE_BUDS_ADDRESS` définie.

L'installateur préserve la configuration et ne connecte pas les écouteurs.
Pour GNOME 50, ferme puis rouvre la session et active
`galaxybridge@galaxybridge.local`. Désactive l'ancienne extension GalaxyBridge
si elle est déjà installée.

## Utilisation

Le panneau permet la connexion PC, la lecture du mode de bruit, les modes ANC,
ambiant et désactivé, le profil musique et l'assistant multipoint. La CLI offre
les mêmes commandes. `galaxybridgectl buds capabilities` examine les services
annoncés sans ouvrir de connexion de contrôle.

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
