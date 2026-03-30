# 🚀 NAS Project - GNS3 Network Automation

Ce projet permet d'automatiser de bout en bout la configuration d'un réseau opérateur (OSPF, MPLS LDP, BGP VPNv4) et d'y raccorder des clients isolés via des VRF (L3VPN). Il déploie directement les configurations générées dans un projet GNS3 local, avec un support pour l'injection à chaud via Telnet.

## 📋 Prérequis

- **Python 3.x** installé sur votre machine.
- **GNS3** installé et fonctionnel.
- Un projet GNS3 créé avec une topologie correspondant aux routeurs définis dans le fichier d'intention (ex: `PE1`, `P1`, `P2`, `PE2`, `CE1`, `CE2`...).

## 📁 Structure du projet

```text
├── intent_file.json      # Le fichier d'intention (votre architecture réseau cible, VRF, BGP)
├── generateurchat.py     # Le moteur de génération des configurations Cisco IOS
├── main.py               # Script principal : lit le JSON et génère les fichiers .cfg
├── deploy_to_gns3.py     # Script de déploiement : injecte les .cfg et/ou configure via Telnet
└── output/               # Dossier généré contenant les configurations (.cfg) prêtes

🛠️ Guide d'utilisation
Étape 1 : Préparer GNS3

    Ouvrez GNS3 et chargez votre projet.

    Assurez-vous que les noms d'hôtes de vos routeurs dans GNS3 correspondent exactement aux noms définis dans intent_file.json (attention à la casse).

    Connectez les interfaces selon la topologie prévue.

    Notez le chemin absolu de votre dossier de projet GNS3 (celui qui contient le fichier .gns3).

Étape 2 : Générer les configurations

Dans votre terminal, à la racine du dépôt, lancez le générateur :
Bash

python3 main.py

    Résultat : Le script lit intent_file.json et crée un fichier .cfg par routeur dans le dossier output/.

Étape 3 : Déploiement à froid (Fichiers de démarrage)

Cette étape injecte les fichiers .cfg générés directement dans les dossiers de votre projet GNS3 (pour écraser les startup-config).

Lancez le script en remplaçant <CHEMIN_PROJET> par le chemin vers votre projet GNS3 :
Bash

python3 deploy_to_gns3.py --project "Chemin/Vers/Votre/Projet" --backup

Note : Utilisez des guillemets si votre chemin contient des espaces.

    Retournez dans l'interface de GNS3.

    Sélectionnez tous vos routeurs, faites un clic droit et choisissez Reload (ou démarrez-les s'ils étaient éteints).

    Les routeurs vont booter en chargeant la nouvelle configuration de base.

Étape 4 : Déploiement à chaud des VRF (Telnet)

Une fois les routeurs démarrés et stabilisés, utilisez le mode Telnet de notre script pour créer et configurer les VRF (Route Distinguisher, Route Targets) en direct sur les routeurs PE.
Bash

python3 deploy_to_gns3.py --project "Chemin/Vers/Votre/Projet" --telnet-vrf

    ⚠️ Important : Assurez-vous d'avoir renseigné les bons ports de console GNS3 (ex: 5000, 5004) dans le dictionnaire gns3_routers du script deploy_to_gns3.py avant de lancer cette commande.
