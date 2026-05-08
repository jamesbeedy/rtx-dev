# freeipa-client

A Juju **subordinate** charm that enrols its principal unit into a FreeIPA
identity-management domain by running `ipa-client-install` and configuring
SSSD.

## Subordinate model

This charm does not deploy on its own machine. It runs alongside another
*principal* application (any charm with a `juju-info` interface) inside the
same machine/container. When you relate it to a `freeipa-server`
(provider of the `freeipa` interface), the client pulls the realm, domain,
server FQDN, and CA certificate from relation data and joins the domain.

## Build & deploy

```bash
charmcraft pack
juju deploy ./freeipa-client_amd64.charm
juju relate <principal-app>     freeipa-client
juju relate freeipa-client      freeipa-server
```

Once both relations settle, the principal unit's `/etc/ipa/default.conf`
will exist and the host will appear in `ipa host-find`.

## Configuration

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `enroll-as` | string | `admin` | IPA principal used to enrol the host. |
| `enrollment-password` | string | `""` | Password for the principal. Empty → use OTP from relation. |
| `client-fqdn` | string | `""` | Override hostname. Empty → use `socket.getfqdn()`. |
| `automount` | boolean | `false` | Configure automount integration. |
| `mkhomedir` | boolean | `true` | Create home directories on first login. |

## Bundle example

```yaml
applications:
  ubuntu:
    charm: ubuntu
    num_units: 1
  freeipa-client:
    charm: ./freeipa-client_amd64.charm
  freeipa-server:
    charm: ./freeipa-server_amd64.charm
    num_units: 1
relations:
  - [ubuntu, freeipa-client]
  - [freeipa-client, freeipa-server]
```

## Actions

| Action | Description |
|--------|-------------|
| `leave-domain` | Run `ipa-client-install --uninstall -U` and detach this unit from FreeIPA. |

## Troubleshooting

- Enrollment failures land in `/var/log/ipaclient-install.log` on the unit.
- `juju debug-log -i freeipa-client` shows the charm's hook traces.
- If a relation gets stuck after a failed enroll, run the `leave-domain`
  action and re-relate.

## License

Apache-2.0 — see [LICENSE](LICENSE).
