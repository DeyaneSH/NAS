import ipaddress

# =========================================================
# OUTILS
# =========================================================

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
    """
    Pour RIP: IOS active RIP par 'network' classful.
    - A: 1-126 -> x.0.0.0
    - B: 128-191 -> x.y.0.0
    - C: 192-223 -> x.y.z.0
    """
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
    """
    Cherche dans intent['links'] un lien entre local_router et remote_router
    et renvoie l'IP (sans /mask) du remote_router sur ce lien.
    """
    for link in intent.get("links", []):
        eps = link.get("endpoints", [])
        devs = {ep.get("device") for ep in eps}
        if local_router in devs and remote_router in devs:
            for ep in eps:
                if ep.get("device") == remote_router:
                    return ep["ip"].split("/")[0]
    return None

def get_router_asn(router_name: str, intent: dict):
    """Retourne l'ASN du routeur (via l'AS qui le contient)."""
    as_data = get_router_as(router_name, intent)
    return as_data["asn"] if as_data else None

def infer_reverse_relationship(rel: str) -> str:
    """
    Si A voit B comme 'customer', B voit A comme 'provider' (et inversement).
    'peer' reste 'peer'.
    """
    rel = rel.lower()
    if rel == "customer":
        return "provider"
    if rel == "provider":
        return "customer"
    return "peer"

def validate_intent_minimal(intent: dict):
    """
    Vérifications utiles pour valider parties 2–3 :
    - Tous les routeurs ont au moins 1 interface dans links (sinon IGP/BGP impossibles)
    - Chaque ebgp_peers a bien un lien correspondant dans links
    """
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
        raise ValueError(
            "Topo incomplète: ces routeurs n'ont aucune interface dans 'links' "
            f"(donc IGP/iBGP impossibles) : {', '.join(isolated)}"
        )

    for p in intent.get("bgp", {}).get("ebgp_peers", []):
        lr = p["local_router"]
        rr = p["remote_router"]
        if find_link_peer_ip(lr, rr, intent) is None:
            raise ValueError(
                f"Topo incomplète: ebgp_peers {lr}->{rr} mais aucun lien {lr}<->{rr} dans 'links'."
            )

# =========================================================
# BLOCS DE CONFIGURATION DE BASE
# =========================================================

def creer_entete(hostname):
    return f"""!
version 15.2
service timestamps debug datetime msec
service timestamps log datetime msec
hostname {hostname}
!
"""

def configurer_interfaces(interfaces, protocol_igp: str):
    cfg = ""
    for iface in interfaces:
        cfg += f"""interface {iface['name']}
 ip address {iface['ip']} {iface['mask']}
"""

        # coût OSPF uniquement si le routeur est en OSPF
        metric = iface.get("ospf_metric")
        if protocol_igp.upper() == "OSPF" and metric is not None:
            cfg += f" ip ospf cost {int(metric)}\n"

        cfg += """ no shutdown
!
"""
    return cfg

def configurer_loopback(loopback_ip):
    return f"""interface Loopback0
 ip address {loopback_ip} 255.255.255.255
!
"""



# =========================================================
# IGP
# =========================================================

def configurer_igp(as_data, interfaces, loopback_ip):
    """
    Configuration OSPF avec la possibilité de définir des métriques (coûts) OSPF.
    """
    igp = as_data["igp"]["protocol"].upper()

    if igp == "RIP":
        cfg = """router rip
 version 2
 no auto-summary
"""
        majors = set()
        for iface in interfaces:
            majors.add(classful_major_network(iface["ip"]))
        for net in sorted(majors):
            cfg += f" network {net}\n"

        cfg += " redistribute connected\n"
        return cfg + "!\n"

    if igp == "OSPF":
        process_id = as_data["igp"]["process_id"]
        area = as_data["igp"]["area"]
        cfg = f"""router ospf {process_id}
 router-id {loopback_ip}
"""
        for iface in interfaces:
            mask = iface["mask"]
            prefixlen = ipaddress.IPv4Network(f"0.0.0.0/{mask}").prefixlen
            net = ipaddress.IPv4Interface(f"{iface['ip']}/{prefixlen}").network
            wildcard = wildcard_from_prefixlen(prefixlen)
            cfg += f" network {net.network_address} {wildcard} area {area}\n"
        cfg += f" network {loopback_ip} 0.0.0.0 area {area}\n"
        return cfg + "!\n"

    return ""

# =========================================================
# BGP POLICIES (PARTIE 3.4)
# =========================================================

def configurer_bgp_policies(intent):
    """
    Politique valley-free via COMMUNITIES.

    - IN  : on TAG + on fixe local-pref (aucun filtrage en entrée)
    - OUT : on FILTRE selon propagation_policy (community-list TO_*)
    """
    bgp = intent["bgp"]
    communities = bgp["communities"]              # customer / peer / provider
    local_pref = bgp["local_preference"]          # customer / peer / provider
    policy = bgp.get("propagation_policy", {})    # to_customer / to_peer / to_provider

    cfg = ""

    # ---------------------------------------------------------
    # 1) Community-lists "rôles" (pour debug/lecture éventuelle)
    # ---------------------------------------------------------
    for role, comm in communities.items():
        cfg += f"ip community-list standard {role.upper()} permit {comm}\n"
    cfg += "\n"

    # ---------------------------------------------------------
    # 2) INBOUND route-maps : TAG + LOCAL-PREF (PAS de filtre)
    #    IMPORTANT : pas de "additive" => on remplace le tag
    # ---------------------------------------------------------
    for role, comm in communities.items():
        lp = local_pref.get(role, 100)
        cfg += f"""route-map RM-IN-{role.upper()} permit 10
 set community {comm}
 set local-preference {lp}
!
"""

    # ---------------------------------------------------------
    # 3) Tag des routes locales (origination via "network ... route-map")
    # ---------------------------------------------------------
   # --- Origination ---
    # Routes "exportables" (typiquement loopbacks des border routers)
    cfg += f"""route-map RM-SET-EXPORT permit 10
 set local-preference {local_pref.get('local', local_pref.get('customer', 200))}
 set community {communities['local']}
!
"""

    # Routes internes (loopbacks internes) : on les tag "customer"
    # => elles pourront circuler dans l'AS et aller vers peer/customer,
    #    mais NE partiront PAS vers provider si to_provider=["local"].
    cfg += f"""route-map RM-SET-INTERNAL permit 10
 set local-preference {local_pref.get('customer', 200)}
 set community {communities['customer']}
!
"""

    # ---------------------------------------------------------
    # 4) OUTBOUND : community-lists TO_* + route-maps RM-OUT-TO-*
    #    C'est ICI que vit le filtrage valley-free.
    #    Exemple (ton JSON):
    #      to_customer: customer, peer, provider
    #      to_peer    : customer
    #      to_provider: customer
    # ---------------------------------------------------------
    for to_key, allowed_roles in policy.items():
        target = to_key.replace("to_", "").upper()   # CUSTOMER / PEER / PROVIDER
        listname = f"TO_{target}"

        # On autorise uniquement les communautés listées
        for r in allowed_roles:
            if r not in communities:
                raise KeyError(
                    f"propagation_policy: rôle '{r}' inconnu. "
                    f"Attendus: {list(communities.keys())}"
                )
            cfg += f"ip community-list standard {listname} permit {communities[r]}\n"
        cfg += "\n"

        # Route-map OUT: permit si match community-list, sinon deny
        cfg += f"""route-map RM-OUT-TO-{target} permit 10
 match community {listname}
!
route-map RM-OUT-TO-{target} deny 20
!
"""

    return cfg

# =========================================================
# CONFIGURER BGP
# =========================================================

def configurer_bgp(as_data, asn, router_id, ibgp_neighbors, ebgp_neighbors, intent):
    """
    Configuration complète de BGP avec gestion des route-maps et des politiques de propagation.
    """
    if not ibgp_neighbors and not ebgp_neighbors:
        return ""

    cfg = configurer_bgp_policies(intent)

    cfg += f"""router bgp {asn}
 bgp router-id {router_id}
 bgp log-neighbor-changes
"""

    # Configuration iBGP en full-mesh
    for n in ibgp_neighbors:
        cfg += f""" neighbor {n} remote-as {asn}
 neighbor {n} update-source Loopback0
 neighbor {n} next-hop-self
 neighbor {n} send-community
 neighbor {n} soft-reconfiguration inbound
"""

    # Configuration des voisins eBGP
    for n in ebgp_neighbors:
        role = n["relationship"].lower()
        peer_ip = n["ip"]
        if role == "provider":
            cfg += f""" neighbor {peer_ip} remote-as {n['remote_as']}
 neighbor {peer_ip} send-community
 neighbor {peer_ip} route-map RM-IN-{role.upper()} in
 neighbor {peer_ip} route-map RM-OUT-TO-{role.upper()} out
 neighbor {peer_ip} soft-reconfiguration inbound
 neighbor {peer_ip} next-hop-self
"""  # Applique la route-map d'entrée pour les providers
        else:
            cfg += f""" neighbor {peer_ip} remote-as {n['remote_as']}
 neighbor {peer_ip} send-community
 neighbor {peer_ip} route-map RM-IN-{role.upper()} in
 neighbor {peer_ip} route-map RM-OUT-TO-{role.upper()} out
 neighbor {peer_ip} soft-reconfiguration inbound
 neighbor {peer_ip} next-hop-self
"""  # Applique les route-maps pour autres rôles

    # Annonce de la loopback pour BGP
    if as_data.get("advertise_loopback"):
        is_border_to_provider = any(n["relationship"].lower() == "provider" for n in ebgp_neighbors)
        rm = "RM-SET-EXPORT" if is_border_to_provider else "RM-SET-INTERNAL"
        cfg += f" network {router_id} mask 255.255.255.255 route-map {rm}\n"

    return cfg + "!\n"

# =========================================================
# LOGIQUE INTENT
# =========================================================

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
    interfaces = []
    for link in intent.get("links", []):
        # lecture de la métrique portée par le lien (choisis UNE clé et garde-la)
        metric = link.get("ospf_metric")  # <- on va utiliser cette clé dans TON json

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
                "relationship": p["relationship"]
            })

        if rr == router_name and (rr, lr) not in declared:
            remote_ip = find_link_peer_ip(rr, lr, intent)
            if remote_ip is None:
                raise ValueError(f"Impossible de trouver le lien {rr}<->{lr} dans 'links'.")
            remote_as = get_router_asn(lr, intent)
            if remote_as is None:
                raise ValueError(f"Impossible de déduire l'ASN de {lr} (routeur introuvable).")
            neighbors.append({
                "ip": remote_ip,
                "remote_as": remote_as,
                "relationship": infer_reverse_relationship(p["relationship"])
            })

    return neighbors

# =========================================================
# ASSEMBLER CONFIGURATION COMPLETE
# =========================================================

def assembler_configuration(router_name, intent):
    validate_intent_minimal(intent)

    as_data = get_router_as(router_name, intent)
    if as_data is None:
        raise ValueError(f"Routeur {router_name} introuvable dans autonomous_systems.")

    loopback_ip = get_router_loopback(router_name, intent)
    if loopback_ip is None:
        raise ValueError(f"Loopback non définie pour {router_name}.")

    interfaces = get_router_interfaces(router_name, intent)

    # iBGP neighbors
    ibgp_neighbors = []
    if as_data.get("ibgp", {}).get("type") == "full-mesh":
        for r in as_data.get("routers", []):
            if r["name"] != router_name:
                ibgp_neighbors.append(get_router_loopback(r["name"], intent))

    # eBGP neighbors
    ebgp_neighbors = collect_ebgp_neighbors(router_name, intent)

    cfg = ""
    cfg += creer_entete(router_name)
    cfg += configurer_loopback(loopback_ip)
    protocol_igp = as_data["igp"]["protocol"].upper()
    cfg += configurer_interfaces(interfaces, protocol_igp)
    cfg += configurer_igp(as_data, interfaces, loopback_ip)
    cfg += configurer_bgp(as_data, as_data["asn"], loopback_ip, ibgp_neighbors, ebgp_neighbors, intent)
    return cfg