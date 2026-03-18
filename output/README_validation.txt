VALIDATION GUIDE (Parts 2–3)
Generated on: 2026-01-26T23:54:14

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
