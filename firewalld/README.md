# firewalld configuration

Declarative firewall configuration, one service definition per product plus a
reference zone. Local files under `/etc/firewalld` override the distribution
definitions of the same name and survive package upgrades, which makes them
the right place for a versioned standard.

| File | Product exposure |
| --- | --- |
| `services/nginx.xml` | TCP 80 (redirect and ACME), TCP 443 (HTTP/1.1 and HTTP/2), UDP 443 (HTTP/3 QUIC). |
| `services/ssh-hardened.xml` | TCP 2356, matching `ssh/sshd_config.d/40-baseline-port.conf`. Change the two together. |
| `zones/public.xml` | Reference zone allowing exactly the product services plus `dhcpv6-client`. |

Products that listen only on loopback or Unix sockets — the status endpoint,
PHP-FPM pools — deliberately have no service definition; nothing about them
belongs in the host firewall. fail2ban needs none either: its bans are rich
rules inserted at runtime, independent of zone services.

## Installation and additive setup

The standard scripts install firewalld separately from product exposure. The
installer starts the daemon but changes no zone allowances; setup installs
only the selected service definitions and adds them to an existing zone:

```sh
firewalld/install --plan
sudo firewalld/install

firewalld/setup --plan --service nginx --service ssh-hardened --zone public
sudo firewalld/setup --service nginx --service ssh-hardened --zone public
```

`setup` rejects unknown services and zones, validates the composed firewalld
configuration, and rolls back its own service files and additions on failure.
It never removes an existing allowance and never installs the reference zone.
That additive behavior makes it appropriate for a host that may have
deployment-specific monitoring or management services.

The equivalent manual service installation is:

```sh
install -m 0644 firewalld/services/*.xml /etc/firewalld/services/
firewall-cmd --reload
firewall-cmd --permanent --add-service=nginx
firewall-cmd --permanent --add-service=ssh-hardened
firewall-cmd --reload
```

Then follow the cutover order in `ssh/README.md`: `ssh/setup` removes the
built-in `ssh` service only after key login on 2356 is proven. For a manual
cutover, the stock `http`,
`https`, and any raw port entries the host accumulated should be removed once
the `nginx` service covers them:

```sh
firewall-cmd --permanent --remove-service=http --remove-service=https
firewall-cmd --reload
firewall-cmd --list-all
```

## The reference zone

`zones/public.xml` is the end state, not the starting point: a zone file
**replaces** the host's zone completely, so installing it removes every
allowance not listed — including built-in `ssh`. Install it only on a host
already living on the hardened port, and review it against services the host
legitimately runs beyond this repository (monitoring agents, cockpit). A
host needing extras keeps its own zone and uses the service definitions with
`--add-service`; the zone file stays a reference for what a clean baseline
host allows. `dhcpv6-client` is retained so IPv6 auto-configuration keeps
working on DHCPv6 networks; drop it only on statically addressed hosts.
No repository setup script installs this file.

```sh
install -m 0644 firewalld/zones/public.xml /etc/firewalld/zones/public.xml
firewall-cmd --reload
firewall-cmd --zone=public --list-all
```

## Validation

```sh
firewall-cmd --check-config
firewall-cmd --info-service=nginx
firewall-cmd --info-service=ssh-hardened
firewall-cmd --zone=public --list-services
```

On a host without a running firewalld — provisioning images, containers —
`firewall-offline-cmd` accepts the same checks. CI installs these files onto
Rocky Linux 9 and CentOS Stream 10 and validates them with
`firewall-offline-cmd --check-config`, resolves both services, and asserts
the zone's service list.
