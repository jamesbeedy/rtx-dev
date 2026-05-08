#!/usr/bin/env python3
"""
FreeIPA Server Charm
"""

import logging
import os
import subprocess
import tempfile
import secrets
import base64
import socket
from typing import Optional

import ops
from ops import (
    CharmBase,
    InstallEvent,
    StartEvent,
    ConfigChangedEvent,
    RelationJoinedEvent,
    ActionEvent,
)

logger = logging.getLogger(__name__)

class FreeIPAServerCharm(CharmBase):
    """Main charm class for FreeIPA server."""

    def __init__(self, *args):
        super().__init__(*args)
        self.framework.observe(self.on.install, self._on_install)
        self.framework.observe(self.on.start, self._on_start)
        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(
            self.on.freeipa_relation_joined, self._on_freeipa_relation_joined
        )
        self.framework.observe(
            self.on.get_admin_password_action, self._on_get_admin_password_action
        )
        self.framework.observe(
            self.on.add_host_action, self._on_add_host_action
        )
        self.framework.observe(
            self.on.refresh_certs_action, self._on_refresh_certs_action
        )

    def _on_install(self, event: InstallEvent):
        """Handle install event."""
        try:
            subprocess.run(
                ["apt-get", "update"],
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                [
                    "apt-get",
                    "install",
                    "-y",
                    "freeipa-server",
                    "freeipa-server-dns",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to install packages: {e}")
            self.unit.status = ops.BlockedStatus("Failed to install packages")

    def _on_start(self, event: StartEvent):
        """Handle start event."""
        # Set ports for the unit
        self.unit.set_ports(
            53,  # DNS TCP
            80,  # HTTP
            88,  # Kerberos
            389,  # LDAP
            443,  # HTTPS
            464,  # Kerberos
            636,  # LDAPS
            749,  # Kerberos
        )
        # Set UDP ports
        self.unit.set_ports(
            53,  # DNS UDP
            88,  # Kerb UDP
            464,  # Kerb UDP
        )

    def _on_config_changed(self, event: ConfigChangedEvent):
        """Handle config changed event."""
        # Validate domain and realm
        domain = self.config.get("domain")
        realm = self.config.get("realm")
        admin_password = self.config.get("admin-password")
        setup_dns = self.config.get("setup-dns")
        server_fqdn = self.config.get("server-fqdn") or socket.getfqdn()

        if not domain or not realm:
            self.unit.status = ops.BlockedStatus("Domain and realm must be configured")
            return

        # Generate admin password if not provided
        if not admin_password:
            admin_password = secrets.token_urlsafe(20)
            # Write to file
            with open("/var/lib/freeipa/admin.password", "w") as f:
                f.write(admin_password)
            os.chmod("/var/lib/freeipa/admin.password", 0o600)

        # Check if IPA server is already installed
        if not os.path.exists("/etc/ipa/default.conf"):
            # Run ipa-server-install
            cmd = [
                "ipa-server-install",
                "-U",
                f"--domain={domain}",
                f"--realm={realm}",
                f"--admin-password={admin_password}",
                f"--hostname={server_fqdn}",
            ]
            if setup_dns:
                cmd.extend(["--setup-dns", "--auto-forwarders"])

            try:
                subprocess.run(cmd, check=True, capture_output=True, text=True)
                self.unit.status = ops.ActiveStatus(f"FreeIPA serving {domain}")
            except subprocess.CalledProcessError as e:
                logger.error(f"Failed to install IPA server: {e}")
                self.unit.status = ops.BlockedStatus("Failed to install IPA server")
                return
        else:
            self.unit.status = ops.ActiveStatus(f"FreeIPA serving {domain}")

    def _on_freeipa_relation_joined(self, event: RelationJoinedEvent):
        """Handle freeipa relation joined event."""
        # Get the relation
        relation = self.model.get_relation("freeipa")
        if not relation:
            logger.warning("No freeipa relation found")
            return

        # Read CA certificate
        ca_cert_path = "/etc/ipa/ca.crt"
        if os.path.exists(ca_cert_path):
            with open(ca_cert_path, "rb") as f:
                ca_cert_data = f.read()
            ca_cert_b64 = base64.b64encode(ca_cert_data).decode("utf-8")
        else:
            ca_cert_b64 = ""

        # Write to relation data
        relation.data[self.app]["realm"] = self.config.get("realm")
        relation.data[self.app]["domain"] = self.config.get("domain")
        relation.data[self.app]["server"] = socket.getfqdn()
        relation.data[self.app]["ca_cert"] = ca_cert_b64

    def _on_get_admin_password_action(self, event: ActionEvent):
        """Handle get-admin-password action."""
        try:
            with open("/var/lib/freeipa/admin.password", "r") as f:
                pwd = f.read().strip()
            event.set_results({"password": pwd})
        except Exception as e:
            logger.error(f"Failed to read admin password: {e}")
            event.fail("Failed to retrieve admin password")

    def _on_add_host_action(self, event: ActionEvent):
        """Handle add-host action."""
        fqdn = event.params.get("fqdn")
        if not fqdn:
            event.fail("FQDN is required")
            return

        try:
            # Run ipa host-add command
            result = subprocess.run(
                ["ipa", "host-add", fqdn, "--random"],
                check=True,
                capture_output=True,
                text=True,
            )
            # Parse OTP from stdout
            otp = None
            for line in result.stdout.splitlines():
                if "OTP" in line:
                    otp = line.split()[-1]
                    break

            if otp:
                event.set_results({"otp": otp})
            else:
                event.set_results({"otp": "OTP not found in output"})
        except Exception as e:
            logger.error(f"Failed to add host: {e}")
            event.fail("Failed to add host")

    def _on_refresh_certs_action(self, event: ActionEvent):
        """Handle refresh-certs action."""
        try:
            subprocess.run(
                ["ipa-getcert", "resubmit", "-f", "/etc/pki/tls/certs/ipa.crt"],
                check=True,
                capture_output=True,
                text=True,
            )
            event.set_results({"result": "Certificates refreshed successfully"})
        except Exception as e:
            logger.error(f"Failed to refresh certificates: {e}")
            event.fail("Failed to refresh certificates")


if __name__ == "__main__":
    ops.main(FreeIPAServerCharm)