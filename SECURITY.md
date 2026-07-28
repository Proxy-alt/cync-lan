# Security Policy

## Reporting a vulnerability

Report privately through GitHub's
[security advisory form](https://github.com/Proxy-alt/cync-lan/security/advisories/new).
That keeps the report between us until there is something to tell people.

Please **do not** open a public issue for anything that would let someone else
reach devices or credentials they should not.

This is a hobby project maintained by one person. There is no guaranteed
response time, no bounty, and no dedicated security team. What you will get is
an honest answer about whether it is a real problem and what is being done.

## Supported versions

The latest release of each of the three artifacts. There are no long-term
support branches and no backports to older versions.

| Artifact | Branch |
|---|---|
| `cync_lan` custom_component | `feature/ha-custom-component` |
| `cync-lan` (protocol library) | `core` |
| `cync-lan-mqtt` (Docker/MQTT add-on) | `python` |

## What this project handles

Worth stating plainly, because it shapes what counts as a vulnerability here:

- **Your Cync account email and password.** Used to fetch the device list from
  the vendor cloud during setup. The resulting token is cached encrypted
  (Fernet/PBKDF2HMAC).
- **Your BTLE mesh name and password.** The shared secret for your lighting
  mesh. `query_mesh_credentials` deliberately returns these to a dismissible
  notification rather than the log, because the log is a broader and
  longer-lived audience.
- **A TLS listener on your LAN** that Cync devices connect to, using a
  self-signed certificate presented as the vendor's own hosts. Devices do not
  verify it — that is the mechanism the project relies on, not a flaw in it.
- **Debug and MITM logs** containing raw device traffic, which can include
  identifiers you would not want to paste into an issue. Redact before
  attaching.

## In scope

- Credential or token exposure — written to logs, sent anywhere unexpected,
  stored unencrypted, or reachable by another process
- Anything letting a host on your network control devices without going through
  the normal path, or read cached credentials
- Vulnerabilities in the TCP/TLS listener reachable from the LAN
- Dependency vulnerabilities that are actually reachable from this code

## Out of scope

- **That Cync devices accept a self-signed certificate.** That is the vendor's
  design and the reason this project can exist. Report it to them if you like.
- **That DNS redirection is required.** Same.
- Anything requiring physical access to a device you already own
- Vulnerabilities in Home Assistant, Docker or your MQTT broker — report those
  upstream
- Missing hardening that has no path to an actual compromise

## Please do not

Report anything about this project to **GE Lighting or Savant**. They did not
write it, cannot support it, and reports there help nobody.
