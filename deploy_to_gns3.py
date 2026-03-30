#!/usr/bin/env python3
import argparse
import json
import os
import shutil
import telnetlib
import time
from datetime import datetime
from typing import Optional, List, Tuple


def find_gns3_file(project_dir: str) -> str:
    """Trouve le fichier .gns3 (JSON) dans le dossier du projet."""
    candidates = []
    for name in os.listdir(project_dir):
        if name.lower().endswith(".gns3"):
            candidates.append(os.path.join(project_dir, name))
    if not candidates:
        raise FileNotFoundError(
            f"Aucun fichier .gns3 trouvé dans: {project_dir}\n"
            "➡️ Donne le chemin du dossier projet GNS3 (celui qui contient le .gns3)."
        )
  
    return sorted(candidates)[0]


def load_project_nodes(gns3_path: str) -> List[dict]:
    with open(gns3_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    nodes = data.get("topology", {}).get("nodes", [])
    return nodes


def find_node_dir(project_dir: str, node_id: str) -> Optional[str]:
    """
    Dans un projet GNS3, les nodes sont souvent dans:
      <project_dir>/project-files/dynamips/<node_id>/
      <project_dir>/project-files/qemu/<node_id>/
      <project_dir>/project-files/iou/<node_id>/
      <project_dir>/project-files/vpcs/<node_id>/
    On cherche dans project-files/*/<node_id>.
    """
    project_files = os.path.join(project_dir, "project-files")
    if not os.path.isdir(project_files):
        return None

    for family in os.listdir(project_files):
        family_dir = os.path.join(project_files, family)
        if not os.path.isdir(family_dir):
            continue
        cand = os.path.join(family_dir, node_id)
        if os.path.isdir(cand):
            return cand
    return None


def find_startup_config(node_dir: str) -> Optional[str]:
    """
    Selon la plateforme, le fichier peut s'appeler:
      - configs/i1_startup-config.cfg
      - startup-config.cfg
      - .../something_startup-config.cfg
    On fait une recherche simple et robuste.
    """
    hits: List[str] = []
    for root, _, files in os.walk(node_dir):
        for fn in files:
            low = fn.lower()
            if "startup-config" in low and low.endswith(".cfg"):
                hits.append(os.path.join(root, fn))

    if not hits:
        return None

    
    hits.sort(key=lambda p: ("/configs/" not in p.replace("\\", "/"), len(p)))
    return hits[0]


def backup_file(path: str) -> str:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    bak = f"{path}.bak-{ts}"
    shutil.copy2(path, bak)
    return bak


def deploy_one(router_name: str, src_cfg: str, dst_cfg: str, do_backup: bool, dry_run: bool) -> None:
    if not os.path.exists(src_cfg):
        raise FileNotFoundError(f"Config générée introuvable: {src_cfg}")

    if not os.path.exists(dst_cfg):
     
        os.makedirs(os.path.dirname(dst_cfg), exist_ok=True)

    if dry_run:
        print(f"[DRY] COPY {src_cfg}  ->  {dst_cfg}")
        return

    if do_backup and os.path.exists(dst_cfg):
        bak = backup_file(dst_cfg)
        print(f"🧷 Backup: {bak}")

    shutil.copy2(src_cfg, dst_cfg)
    print(f"✅ Deployed: {router_name} -> {dst_cfg}")

def send_command(tn, command, sleep_time=0.5):
    """
    Envoie une commande avec le vrai 'Entrée' Cisco (\r\n) 
    et affiche TOUT ce que le routeur répond pour débugger.
    """
    
    tn.write(command.encode('ascii') + b"\r\n")
    time.sleep(sleep_time)
    
    
    output = tn.read_very_eager().decode('ascii', errors='ignore')
    
    
    reponse_propre = output.replace('\r\n', ' | ').strip()
    if reponse_propre:
        print(f"      [Routeur] {reponse_propre}")
    else:
        print("      [Routeur] (Silence absolu...)")
        
    if "% Invalid" in output or "% Incomplete" in output or "% Unknown" in output:
        print(f"      ❌ ERREUR : {output.strip()}")
        
    return output

def deploy_vrf_via_telnet(host, port, vrf_list):
    print(f"[*] Connexion Telnet à {host}:{port}...")
    try:
        tn = telnetlib.Telnet(host, port, timeout=5)
        
       
        tn.write(b"\r\n\r\n")
        time.sleep(1) 
        tn.read_very_eager() 

        print("[+] Connecté ! Passage en mode configuration...")
       
        send_command(tn, "enable", sleep_time=0.5)
        
       
        send_command(tn, "configure terminal", sleep_time=1)

        for vrf in vrf_list:
            print(f"    -> Injection de la VRF : {vrf['name']}")
            
            send_command(tn, f"ip vrf {vrf['name']}")
            send_command(tn, f"rd {vrf['rd']}")
            
            for rt_exp in vrf.get('rt_export', []): 
                send_command(tn, f"route-target export {rt_exp}")
                
            for rt_imp in vrf.get('rt_import', []):
                send_command(tn, f"route-target import {rt_imp}")
            
            send_command(tn, "exit") 
        
        send_command(tn, "end")
        print("[+] Sauvegarde de la configuration (write memory)...")
        
        send_command(tn, "write memory", sleep_time=2)
        
        tn.write(b"\r\n") 
        time.sleep(1)
        
        tn.close()
        print(f"[✅] Déploiement terminé sur {host}:{port}\n")

    except Exception as e:
        print(f"[-] Erreur de connexion à {host}:{port} : {e}")

def main():
    
    ap = argparse.ArgumentParser(
        description="Déploie les configs générées (output/*.cfg) dans le bon dossier du projet GNS3."
    )
    
    
    ap.add_argument("--telnet-vrf", action="store_true", help="Déploie uniquement les VRFs à chaud via Telnet")
    ap.add_argument("--project", required=True, help="Chemin du dossier projet GNS3 (celui qui contient le .gns3)")
    ap.add_argument("--generated", default="output", help="Dossier contenant R1.cfg, R2.cfg, ... (par défaut: output)")
    ap.add_argument("--ext", default=".cfg", help="Extension des configs générées (par défaut: .cfg)")
    ap.add_argument("--backup", action="store_true", help="Fait un backup du startup-config actuel avant d'écraser")
    ap.add_argument("--dry-run", action="store_true", help="N'écrit rien, affiche juste ce qui serait copié")
    
    
    args = ap.parse_args()

    
    
    if args.telnet_vrf:
        
        print("[-] Mode Telnet activé : Déploiement des VRFs à chaud...")
        
        
        try:
            with open("intent_file.json", "r") as f:
                network_data = json.load(f)
        except FileNotFoundError:
            print("❌ Erreur : Fichier intent_file.json introuvable.")
            return

        vrfs_to_deploy = network_data.get("vrfs", []) 
        if not vrfs_to_deploy:
            print("⚠️ Aucune VRF (vrfs) trouvée dans le fichier JSON.")
            return

        
        gns3_routers = {}

        for n in nodes:
            name = n.get("name")
            node_id = n.get("node_id")
            if not name or not node_id:
                continue

            print("Cherche le port telnet")

            # Cherche le port telnet dans le .gns3
            if "PE" in name:
                print("PE found: " + name)
                telnet_port = n.get("console")
                print("telnet_port = " + str(telnet_port))

                gns3_routers[name] = {"host": "127.0.0.1", "port": telnet_port}

        
        for router_name, connection_info in gns3_routers.items():
            print(f"\n=== Cible : {router_name} ===")
            deploy_vrf_via_telnet(
                connection_info["host"], 
                connection_info["port"], 
                vrfs_to_deploy
            )

    else:
        
        print("[-] Mode Fichiers activé : Écrasement des startup-configs...")
        
        project_dir = os.path.abspath(args.project)
        gen_dir = os.path.abspath(args.generated)

        gns3_path = find_gns3_file(project_dir)
        print(f"📄 Using project file: {gns3_path}")

        nodes = load_project_nodes(gns3_path)
        if not nodes:
            raise RuntimeError("Aucun node trouvé dans le fichier .gns3 (topology.nodes vide).")

        missing_generated: List[str] = []
        missing_node_dir: List[str] = []
        missing_startup: List[str] = []
        deployed: List[Tuple[str, str]] = []

        for n in nodes:
            name = n.get("name")
            node_id = n.get("node_id")
            if not name or not node_id:
                continue

            src_cfg = os.path.join(gen_dir, f"{name}{args.ext}")
            if not os.path.exists(src_cfg):
                missing_generated.append(name)
                continue

            node_dir = find_node_dir(project_dir, node_id)
            if node_dir is None:
                missing_node_dir.append(name)
                continue

            dst_cfg = find_startup_config(node_dir)
            if dst_cfg is None:
                missing_startup.append(name)
                continue

            deploy_one(name, src_cfg, dst_cfg, do_backup=args.backup, dry_run=args.dry_run)
            deployed.append((name, dst_cfg))

        print("\n=== SUMMARY ===")
        print(f"Deployed: {len(deployed)}")
        if missing_generated:
            print(f"⚠️ No generated cfg for: {', '.join(sorted(set(missing_generated)))}")
        if missing_node_dir:
            print(f"⚠️ Node dir not found for: {', '.join(sorted(set(missing_node_dir)))}")
        if missing_startup:
            print(f"⚠️ No startup-config found for: {', '.join(sorted(set(missing_startup)))}")

        print("\n✅ Done.")
        print("ℹ️ Pense à 'Reload' / 'Restart' les nodes dans GNS3 si nécessaire.")

if __name__ == "__main__":
    main()
