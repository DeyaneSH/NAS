import json
import os
import sys
from datetime import datetime

import generateurchat as generateur


def load_intent(path: str) -> dict:
    if not os.path.exists(path):
        raise FileNotFoundError(f"Intent introuvable : {path}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def compute_stats(intent: dict) -> dict:
    as_list = intent.get("autonomous_systems", [])
    routers = []
    for a in as_list:
        routers.extend([r["name"] for r in a.get("routers", [])])

    links = intent.get("links", [])
    ebgp = intent.get("bgp", {}).get("ebgp_peers", [])

    return {
        "as_count": len(as_list),
        "router_count": len(routers),
        "routers": routers,
        "link_count": len(links),
        "ebgp_count": len(ebgp),
    }


def write_validation_guide(output_dir: str) -> None:
    """
    Petit guide de validation demandé par le sujet (approach to validate).
    """
    guide = f"""VALIDATION GUIDE (Parts 2–3)
Generated on: {datetime.now().isoformat(timespec="seconds")}

A) IGP validation
- RIP (AS_X):
  - show ip protocols
  - show ip route rip
  - ping <loopback> between routers inside AS_X

- OSPF (AS_Y):
  - show ip ospf neighbor
  - show ip route ospf
  - ping <loopback> between routers inside AS_Y                     PARTIE A VALIDÉE !!

B) Loopback reachability
- From each router, ping:
  - all loopbacks in the same AS
  - edge loopbacks across AS (after BGP is up)

C) BGP validation
- show ip bgp summary
  Expect:
  - iBGP sessions Established inside each AS (full-mesh)
  - eBGP sessions Established on inter-AS links

- show ip bgp
  Expect:
  - routes learned from neighbors appear in BGP table

D) Policies (Part 3.4)
- Verify LOCAL_PREF according to relationship:
  - customer > peer > provider (values from intent)
- Verify propagation filtering:
  - to_peer / to_provider should advertise only customer-tagged routes
Commands you can use:
  - show route-map
  - show ip community-list
  - show ip bgp neighbors <x.x.x.x> routes (platform-dependent)
"""
    path = os.path.join(output_dir, "README_validation.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(guide)


def ensure_output_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def main() -> int:
    intent_path = "Intent_file.json"

    try:
        intent = load_intent(intent_path)
        stats = compute_stats(intent)

        output_dir = intent.get("project_settings", {}).get("output_folder", "output")
        ensure_output_dir(output_dir)

        print("=== Intent loaded ===")
        print(f"- AS:      {stats['as_count']}")
        print(f"- Routers: {stats['router_count']} -> {', '.join(stats['routers'])}")
        print(f"- Links:   {stats['link_count']}")
        print(f"- eBGP:    {stats['ebgp_count']} declared peers")
        print()

        print("--- Génération des configurations ---")
        generated = 0
        for as_data in intent.get("autonomous_systems", []):
            for router in as_data.get("routers", []):
                name = router["name"]
                cfg = generateur.assembler_configuration(name, intent)

                out_path = os.path.join(output_dir, f"{name}.cfg")
                with open(out_path, "w", encoding="utf-8") as f_out:
                    f_out.write(cfg)

                print(f"✅ {name} -> {out_path}")
                generated += 1

        write_validation_guide(output_dir)

        print()
        print("--- Terminé avec succès ---")
        print(f"- Fichiers générés : {generated}")
        print(f"- Dossier : {output_dir}/")
        print(f"- Guide de validation : {output_dir}/README_validation.txt")
        return 0

    except Exception as e:
        # En mode "projet", mieux vaut une erreur claire et un exit code non nul
        print("❌ ERREUR:", str(e))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())