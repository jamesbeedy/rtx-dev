#!/usr/bin/env python3
import os
import subprocess
import base64
from pathlib import Path

import ops
import ops.framework

# Define the charm
class FreeIPAClientCharm(ops.CharmBase):
    def __init__(self, *args):
        super().__init__(*args)
        self.framework.observe(self.on.install, self._on_install)
        self.framework.observe(self.on.start, self._on_start)
        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(self.on.freeipa_relation_changed, self._on_freeipa_relation_changed)
        self.framework.observe(self.on.freeipa_relation_broken, self._on_freeipa_relation_broken)
        self.framework.observe(self.on.leave_domain_action, self._on_leave_domain_action)

    def _on_install(self, _):
        """Install required packages."""
        subprocess.run(["apt-get", "update"], check=True)
        subprocess.run(["apt-get", "install", "-y", "freeipa-client", "sssd"], check=True)

    def _on_start(self, _):
        """Start the charm."""
        # Nothing to do here for now
        pass

    def _on_config_changed(self, _):
        """Handle configuration changes."""
        # Nothing to do here for now
        pass

    def _on_freeipa_relation_changed(self, event):
        """Handle FreeIPA relation changes."""
        # Check if already enrolled
        if os.path.exists("/etc/ipa/default.conf"):
            self.unit.status = ops.ActiveStatus(f"Enrolled in {event.relation.data[event.app].get('domain', 'unknown')}")
            return

        # Get data from relation
        realm = event.relation.data[event.app].get("realm")
        domain = event.relation.data[event.app].get("domain")
        server = event.relation.data[event.app].get("server")
        ca_cert = event.relation.data[event.app].get("ca_cert")

        if not all([realm, domain, server, ca_cert]):
            self.unit.status = ops.BlockedStatus("Missing required FreeIPA data in relation")
            return

        # Write CA certificate
        ca_cert_path = "/etc/ipa/ca.crt"
        decoded_ca_cert = base64.b64decode(ca_cert)
        Path(ca_cert_path).write_bytes(decoded_ca_cert)
        os.chmod(ca_cert_path, 0o644)

        # Build command
        cmd = [
            "ipa-client-install",
            "--unattended",
            f"--domain={domain}",
            f"--realm={realm}",
            f"--server={server}",
            f"--principal={self.config['enroll-as']}"
        ]

        # Add password if provided
        if self.config["enrollment-password"]:
            cmd.append(f"--password={self.config['enrollment-password']}")

        # Add flags based on config
        if self.config["mkhomedir"]:
            cmd.append("--mkhomedir")
        if self.config["automount"]:
            cmd.append("--automount-location=default")

        # Run command
        try:
            subprocess.run(cmd, check=True)
            self.unit.status = ops.ActiveStatus(f"Enrolled in {domain}")
        except subprocess.CalledProcessError as e:
            self.unit.status = ops.BlockedStatus(e.stderr.decode().splitlines()[0] if e.stderr else "Installation failed")

    def _on_freeipa_relation_broken(self, _):
        """Handle FreeIPA relation broken."""
        try:
            subprocess.run(["ipa-client-install", "--uninstall", "-U"], check=True)
        except subprocess.CalledProcessError:
            pass
        self.unit.status = ops.BlockedStatus("Detached from FreeIPA")

    def _on_leave_domain_action(self, event):
        """Handle leave-domain action."""
        try:
            subprocess.run(["ipa-client-install", "--uninstall", "-U"], check=True)
        except subprocess.CalledProcessError:
            pass
        self.unit.status = ops.BlockedStatus("Left FreeIPA domain")
        event.set_results({"status": "left"})