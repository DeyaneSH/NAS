# NAS
NAS Project on automation of Cisco router configuration

# 🚀 NAS Project - GNS3 Network Automation

Ce projet permet d'automatiser de bout en bout la configuration d'un réseau opérateur (OSPF, MPLS LDP, BGP VPNv4) et de déployer directement les configurations générées dans un projet GNS3 local.

## 📋 Prérequis

- **Python 3.x** installé sur votre machine.
- **GNS3** installé et fonctionnel.
- Un projet GNS3 créé avec une topologie correspondant aux routeurs définis dans le fichier d'intention (ex: `PE1`, `P1`, `P2`, `PE2`).

## 📁 Structure du projet

\`\`\`text
├── Intent_file.json      # Le fichier d'intention (votre architecture réseau cible)
├── generateurchat.py     # Le moteur de génération des configurations Cisco IOS
├── main.py               # Script principal : lit le JSON et génère les fichiers .cfg
├── deploy_to_gns3.py     # Script de déploiement : injecte les .cfg dans GNS3
└── output/               # Dossier généré contenant les configurations (.cfg) prêtes
\`\`\`

## 🛠️ Guide d'utilisation

### Étape 1 : Préparer GNS3
1. Ouvrez GNS3 et chargez votre projet.
2. Assurez-vous que les noms d'hôtes de vos routeurs dans GNS3 correspondent **exactement** aux noms définis dans `Intent_file.json` (attention à la casse).
3. Connectez les interfaces selon la topologie prévue.
4. Notez le chemin absolu de votre dossier de projet GNS3 (celui qui contient le fichier `.gns3`).

### Étape 2 : Générer les configurations
Dans votre terminal, à la racine du dépôt, lancez le générateur :

\`\`\`bash
python main.py
\`\`\`

> **Résultat :** Le script lit `Intent_file.json` et crée un fichier `.cfg` par routeur dans le dossier `output/`, ainsi qu'un guide de validation.

### Étape 3 : Déployer dans GNS3
Toujours dans le terminal, lancez le script de déploiement en remplaçant `<CHEMIN_PROJET>` par le chemin absolu vers votre projet GNS3.

> ⚠️ **Attention au format du chemin selon votre système d'exploitation :**
> - **Sous Windows :** Utilisez des antislashs `\` et gardez les guillemets (surtout s'il y a des espaces dans vos noms de dossiers).
>   *Exemple :* `python deploy_to_gns3.py --project "C:\Users\VotreNom\GNS3\projects\Projet_NAS" --backup`
> - **Sous Mac / Linux :** Utilisez des slashs `/`.
>   *Exemple :* `python deploy_to_gns3.py --project "/Users/VotreNom/GNS3/projects/Projet_NAS" --backup`

*Options utiles :*
- `--backup` : Crée une copie de sauvegarde de l'ancien fichier `startup-config` du routeur avant de l'écraser (fortement recommandé !).
- `--dry-run` : Simule le déploiement pour afficher dans le terminal quels fichiers seraient modifiés, sans rien écrire sur le disque.

### Étape 4 : Appliquer sur le réseau
1. Retournez dans l'interface de GNS3.
2. Sélectionnez tous vos routeurs, faites un clic droit et choisissez **Reload** (ou démarrez-les s'ils étaient éteints).
3. Les routeurs vont booter en chargeant la nouvelle configuration injectée !

## ✅ Validation

Une fois les routeurs démarrés, ouvrez leurs consoles pour vérifier que les différentes couches du réseau sont opérationnelles :

**1. Routage IGP (Phase 0)**
\`\`\`text
PE1# show ip route ospf
P1# show ip ospf neighbor
\`\`\`

**2. Transport MPLS (Phase 1)**
\`\`\`text
P1# show mpls ldp neighbor
P1# show mpls forwarding-table
\`\`\`

**3. Routage BGP VPNv4 (Phase 2)**
\`\`\`text
PE1# show ip bgp vpnv4 all summary
\`\`\`

---
*Projet réalisé dans le cadre du cours NAS (Network Automation and Services).*