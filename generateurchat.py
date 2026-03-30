import ipaddress
import json
import os

def mask_to_dotted(mask):
    """Convertit un /XX en masque décimal pointé"""
    mask = int(mask)
    bits = (0xffffffff >> (32 - mask)) << (32 - mask)
    return ".".join(str((bits >> i) & 0xff) for i in [24, 16, 8, 0])

def wildcard_from_prefixlen(prefixlen: int) -> str:
    """Ex: /30 -> 0.0.0.3"""
    host_bits = 32 - int(prefixlen)
    wildcard_int = (1 << host_bits) - 1 if host_bits > 0 else 0
    return ".".join(str((wildcard_int >> i) & 0xff) for i in [24, 16, 8, 0])

def classful_major_network(ip: str) -> str:
    """Pour RIP: IOS active RIP par 'network' classful."""
    o = [int(x) for x in ip.split(".")]
    first = o[0]
    if 1 <= first <= 126:
        return f"{o[0]}.0.0.0"
    elif 128 <= first <= 191:
        return f"{o[0]}.{o[1]}.0.0"
    elif 192 <= first <= 223:
        return f"{o[0]}.{o[1]}.{o[2]}.0"
    return f"{o[0]}.0.0.0"

def find_link_peer_ip(local_router: str, remote_router: str, intent: dict):
    """Cherche dans intent['links'] un lien et renvoie l'IP du remote_router."""
    for link in intent.get("links", []):
        eps = link.get("endpoints", [])
        devs = {ep.get("device") for ep in eps}
        if local_router in devs and remote_router in devs:
            for ep in eps:
                if ep.get("device") == remote_router:
                    return ep["ip"].split("/")[0]
    return None

def get_router_asn(router_name: str, intent: dict):
    """Retourne l'ASN du routeur."""
    as_data = get_router_as(router_name, intent)
    return as_data["asn"] if as_data else None

def infer_reverse_relationship(rel: str) -> str:
    """Si A voit B comme 'customer', B voit A comme 'provider'."""
    rel = rel.lower()
    if rel == "customer":
        return "provider"
    if rel == "provider":
        return "customer"
    return "peer"

def validate_intent_minimal(intent: dict):
    """Vérifications minimales de la topologie."""
    routers = []
    for a in intent.get("autonomous_systems", []):
        routers += [r["name"] for r in a.get("routers", [])]

    seen = {r: 0 for r in routers}
    for link in intent.get("links", []):
        for ep in link.get("endpoints", []):
            dev = ep.get("device")
            if dev in seen:
                seen[dev] += 1

    isolated = [r for r, n in seen.items() if n == 0]
    if isolated:
        raise ValueError(f"Topo incomplète: routeurs isolés : {', '.join(isolated)}")

    for p in intent.get("bgp", {}).get("ebgp_peers", []):
        lr = p["local_router"]
        rr = p["remote_router"]
        if find_link_peer_ip(lr, rr, intent) is None:
            raise ValueError(f"Topo incomplète: ebgp_peers {lr}->{rr} mais aucun lien dans 'links'.")

# BLOCS DE CONFIGURATION DE BASE

def creer_entete(hostname, mpls_enabled=False):
    cfg = f"""!
version 15.2
service timestamps debug datetime msec
service timestamps log datetime msec
hostname {hostname}
!
ip cef
""" 
    if mpls_enabled:
        cfg+= "mpls label protocol ldp\n"
        cfg+= "mpls ldp router-id Loopback0 force\n"
    cfg += "!\n"
    return cfg

def configurer_vrfs_global(intent, router_name):
    """Génère la définition globale des VRF uniquement pour les routeurs PE."""
    as_data = get_router_as(router_name, intent)
    if not as_data: 
        return ""
    
    role = next((r.get("role") for r in as_data.get("routers", []) if r["name"] == router_name), None)
    if role != "PE":
        return "" 
        
    cfg = ""
    vrfs = intent.get("vrfs", [])
    for vrf in vrfs:
        cfg += f"ip vrf {vrf['name']}\n"
        cfg += f" rd {vrf['rd']}\n"
        for rt in vrf.get("rt_export", []):
            cfg += f" route-target export {rt}\n"
        for rt in vrf.get("rt_import", []):
            cfg += f" route-target import {rt}\n"
        cfg += "!\n"
        
    return cfg

def configurer_interfaces(interfaces, protocol_igp: str):
    cfg = ""
    for iface in interfaces:
        cfg += f"interface {iface['name']}\n"
        
        if "vrf" in iface:
            cfg += f" ip vrf forwarding {iface['vrf']}\n"
            
        cfg += f" ip address {iface['ip']} {iface['mask']}\n"

        metric = iface.get("ospf_metric")
        if protocol_igp.upper() == "OSPF" and metric is not None and "vrf" not in iface:
            cfg += f" ip ospf cost {int(metric)}\n"
            
        if iface.get("mpls"):
            cfg +=" mpls ip\n"

        cfg += " no shutdown\n!\n"
    return cfg

def configurer_loopback(loopback_ip):
    return f"""interface Loopback0
 ip address {loopback_ip} 255.255.255.255
!
"""
# IGP
def configurer_igp(as_data, interfaces, loopback_ip):
    igp = as_data["igp"]["protocol"].upper()

    if igp == "RIP":
        cfg = """router rip\n version 2\n no auto-summary\n"""
        majors = set()
        for iface in interfaces:
            majors.add(classful_major_network(iface["ip"]))
        for net in sorted(majors):
            cfg += f" network {net}\n"
        cfg += " redistribute connected\n!\n"
        return cfg

    if igp == "OSPF":
        process_id = as_data["igp"]["process_id"]
        area = as_data["igp"]["area"]
        cfg = f"""router ospf {process_id}\n router-id {loopback_ip}\n"""
        for iface in interfaces:
            if "vrf" in iface:
                continue 
            mask = iface["mask"]
            prefixlen = ipaddress.IPv4Network(f"0.0.0.0/{mask}").prefixlen
            net = ipaddress.IPv4Interface(f"{iface['ip']}/{prefixlen}").network
            wildcard = wildcard_from_prefixlen(prefixlen)
            cfg += f" network {net.network_address} {wildcard} area {area}\n"
        cfg += f" network {loopback_ip} 0.0.0.0 area {area}\n!\n"
        return cfg

    return ""

# BGP POLICIES (PARTIE 3.4)

def configurer_bgp_policies(intent):
    """Politique valley-free via COMMUNITIES."""
    bgp = intent["bgp"]
    communities = bgp["communities"]
    local_pref = bgp["local_preference"]
    policy = bgp.get("propagation_policy", {})

    cfg = ""
    for role, comm in communities.items():
        cfg += f"ip community-list standard {role.upper()} permit {comm}\n"
    cfg += "\n"

    for role, comm in communities.items():
        lp = local_pref.get(role, 100)
        cfg += f"route-map RM-IN-{role.upper()} permit 10\n set community {comm}\n set local-preference {lp}\n!\n"

    cfg += f"route-map RM-SET-EXPORT permit 10\n set local-preference {local_pref.get('local', local_pref.get('customer', 200))}\n set community {communities['local']}\n!\n"
    cfg += f"route-map RM-SET-INTERNAL permit 10\n set local-preference {local_pref.get('customer', 200)}\n set community {communities['customer']}\n!\n"

    for to_key, allowed_roles in policy.items():
        target = to_key.replace("to_", "").upper()
        listname = f"TO_{target}"
        for r in allowed_roles:
            if r not in communities:
                raise KeyError(f"Rôle inconnu : {r}")
            cfg += f"ip community-list standard {listname} permit {communities[r]}\n"
        cfg += "\n"
        cfg += f"route-map RM-OUT-TO-{target} permit 10\n match community {listname}\n!\nroute-map RM-OUT-TO-{target} deny 20\n!\n"

    return cfg

# =========================================================
# CONFIGURER BGP
# =========================================================

def configurer_bgp(as_data, asn, router_id, ibgp_neighbors, ebgp_neighbors, intent):
    if not ibgp_neighbors and not ebgp_neighbors:
        return ""

    try:
        cfg = configurer_bgp_policies(intent)
    except KeyError:
        cfg = ""

    cfg += f"router bgp {asn}\n"
    cfg += f" bgp router-id {router_id}\n"
    cfg += " bgp log-neighbor-changes\n"

   
    cfg += f" address-family ipv4\n"
    cfg += f"  network {router_id} mask 255.255.255.255\n"
    cfg += f" exit-address-family\n"

    for n_info in ibgp_neighbors:
        n = n_info["ip"]
        cfg += f" neighbor {n} remote-as {asn}\n"
        cfg += f" neighbor {n} update-source Loopback0\n"

    if ibgp_neighbors:
        cfg += " address-family ipv4\n"
        for n_info in ibgp_neighbors:
            n = n_info["ip"]
            cfg += f"  no neighbor {n} activate\n"
        cfg += " exit-address-family\n"

        cfg += " address-family vpnv4\n"
        for n_info in ibgp_neighbors:
            n = n_info["ip"]
            cfg += f"  neighbor {n} activate\n"
            cfg += f"  neighbor {n} send-community extended\n"
            if n_info.get("is_client"):
                cfg += f"  neighbor {n} route-reflector-client\n"
        cfg += " exit-address-family\n"

    for n in ebgp_neighbors:
        role = n["relationship"].lower()
        peer_ip = n["ip"]
        vrf_name = n.get("vrf") 

        if vrf_name:
            cfg += f" address-family ipv4 vrf {vrf_name}\n"
            cfg += f"  neighbor {peer_ip} remote-as {n['remote_as']}\n"
            cfg += f"  neighbor {peer_ip} activate\n"
            cfg += f" exit-address-family\n"
        else:
            
            cfg += f" neighbor {peer_ip} remote-as {n['remote_as']}\n"
            cfg += f" neighbor {peer_ip} soft-reconfiguration inbound\n"
            cfg += f" neighbor {peer_ip} allowas-in\n" 

    return cfg + "!\n"

# LOGIQUE INTENT

def get_router_as(router_name, intent):
    for as_data in intent.get("autonomous_systems", []):
        if router_name in [r["name"] for r in as_data.get("routers", [])]:
            return as_data
    return None

def get_router_loopback(router_name, intent):
    for as_data in intent.get("autonomous_systems", []):
        for r in as_data.get("routers", []):
            if r["name"] == router_name:
                return r["loopback"].split("/")[0]
    return None

def get_router_interfaces(router_name, intent):
    router_role = None
    for as_data in intent.get("autonomous_systems", []):
        for r in as_data.get("routers", []):
            if r["name"] == router_name:
                router_role = r.get("role")

    interfaces = []
    for link in intent.get("links", []):
        metric = link.get("ospf_metric")
        mpls_enabled = link.get("mpls", False)
        vrf_name = link.get("vrf")  

        for ep in link.get("endpoints", []):
            if ep.get("device") == router_name:
                ip, mask = ep["ip"].split("/")
                iface_data = {
                    "name": ep["interface"],
                    "ip": ip,
                    "mask": mask_to_dotted(mask)
                }
                if metric is not None:
                    iface_data["ospf_metric"] = metric
                if mpls_enabled:
                    iface_data["mpls"] = True
                
                if vrf_name and router_role == "PE":
                    iface_data["vrf"] = vrf_name

                interfaces.append(iface_data)
    return interfaces

def collect_ebgp_neighbors(router_name: str, intent: dict):
    neighbors = []
    peers = intent.get("bgp", {}).get("ebgp_peers", [])
    declared = {(p["local_router"], p["remote_router"]) for p in peers}

    for p in peers:
        lr = p["local_router"]
        rr = p["remote_router"]

        if lr == router_name:
            remote_ip = find_link_peer_ip(lr, rr, intent)
            if remote_ip is None:
                raise ValueError(f"Impossible de trouver le lien {lr}<->{rr} dans 'links'.")
            neighbors.append({
                "ip": remote_ip,
                "remote_as": p["remote_as"],
                "relationship": p["relationship"],
                "vrf": p.get("vrf") 
            })

        if rr == router_name and (rr, lr) not in declared:
            remote_ip = find_link_peer_ip(rr, lr, intent)
            if remote_ip is None:
                raise ValueError(f"Impossible de trouver le lien {rr}<->{lr} dans 'links'.")
            remote_as = get_router_asn(lr, intent)
            if remote_as is None:
                raise ValueError(f"Impossible de déduire l'ASN de {lr}.")
            neighbors.append({
                "ip": remote_ip,
                "remote_as": remote_as,
                "relationship": infer_reverse_relationship(p["relationship"]),
                "vrf": None 
            })

    return neighbors

#ASSEMBLER CONFIGURATION COMPLETE

def assembler_configuration(router_name, intent):
    try:
        validate_intent_minimal(intent)
    except ValueError:
        pass

    as_data = get_router_as(router_name, intent)
    if as_data is None:
        raise ValueError(f"Routeur {router_name} introuvable dans autonomous_systems.")

    loopback_ip = get_router_loopback(router_name, intent)
    if loopback_ip is None:
        raise ValueError(f"Loopback non définie pour {router_name}.")

    interfaces = get_router_interfaces(router_name, intent)
    router_needs_mpls = any(iface.get("mpls") for iface in interfaces)
    
    current_router_role = next((r.get("role") for r in as_data.get("routers", []) if r["name"] == router_name), None)

 
    ibgp_neighbors = []
    if current_router_role == "PE":
        for r in as_data.get("routers", []):
            if r["name"] != router_name and r.get("role") == "PE":
                ibgp_neighbors.append({"ip": get_router_loopback(r["name"], intent), "is_client": False})
            elif r.get("role") == "RR":
                ibgp_neighbors.append({"ip": get_router_loopback(r["name"], intent), "is_client": False})
                
    elif current_router_role == "RR":
        for r in as_data.get("routers", []):
            if r["name"] != router_name:
                if r.get("role") == "PE":
                    ibgp_neighbors.append({"ip": get_router_loopback(r["name"], intent), "is_client": True})
                elif r.get("role") == "RR":
                    ibgp_neighbors.append({"ip": get_router_loopback(r["name"], intent), "is_client": False})

    ebgp_neighbors = []
    try:
        ebgp_neighbors = collect_ebgp_neighbors(router_name, intent)
    except KeyError:
        pass

    cfg = ""
    cfg += creer_entete(router_name, mpls_enabled=router_needs_mpls)
    cfg += configurer_vrfs_global(intent, router_name)
    cfg += configurer_loopback(loopback_ip)
    
    protocol_igp = as_data["igp"]["protocol"].upper()
    cfg += configurer_interfaces(interfaces, protocol_igp)
    cfg += configurer_igp(as_data, interfaces, loopback_ip)
    
    cfg += configurer_bgp(as_data, as_data["asn"], loopback_ip, ibgp_neighbors, ebgp_neighbors, intent)
    
    return cfg
