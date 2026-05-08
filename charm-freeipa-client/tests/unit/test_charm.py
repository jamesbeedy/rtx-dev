import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest
from ops.testing import Harness
from ops.model import ActiveStatus
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from charm import FreeIPAClientCharm

@pytest.fixture
def harness():
    harness = Harness(FreeIPAClientCharm)
    harness.set_leader(True)
    harness.begin()
    yield harness
    harness.cleanup()

def test_install_apt_installs_packages(harness):
    with patch("subprocess.run") as mock_run:
        harness.begin_with_initial_hooks()
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert "apt-get" in args
        assert "install" in args
        assert "freeipa-client" in args
        assert "sssd" in args

def test_relation_changed_enrolls_when_not_present(harness):
    with patch("os.path.exists") as mock_exists, \
         patch("subprocess.run") as mock_run, \
         patch("pathlib.Path.write_bytes") as mock_write:
        mock_exists.return_value = False
        relation_id = harness.add_relation("freeipa", "freeipa")
        harness.update_relation_data(
            relation_id,
            "freeipa",
            {
                "realm": "EXAMPLE.LOCAL",
                "domain": "example.local",
                "server": "ipa.example.local",
                "ca_cert": "LS0tLS1GQUtFLS0tLS0="
            }
        )
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert "--domain=example.local" in args
        assert "--realm=EXAMPLE.LOCAL" in args
        assert "--server=ipa.example.local" in args
        assert "--principal=admin" in args
        mock_write.assert_called_once()

def test_relation_changed_skips_when_already_enrolled(harness):
    with patch("os.path.exists") as mock_exists, \
         patch("subprocess.run") as mock_run:
        mock_exists.return_value = True
        relation_id = harness.add_relation("freeipa", "freeipa")
        harness.update_relation_data(
            relation_id,
            "freeipa",
            {
                "realm": "EXAMPLE.LOCAL",
                "domain": "example.local",
                "server": "ipa.example.local",
                "ca_cert": "LS0tLS1GQUtFLS0tLS0="
            }
        )
        mock_run.assert_not_called()
        assert isinstance(harness.model.unit.status, ActiveStatus)

def test_relation_broken_runs_uninstall(harness):
    with patch("subprocess.run") as mock_run:
        relation_id = harness.add_relation("freeipa", "freeipa")
        harness.remove_relation(relation_id)
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert "ipa-client-install" in args
        assert "--uninstall" in args
        assert "-U" in args

def test_leave_domain_action(harness):
    with patch("subprocess.run") as mock_run:
        result = harness.run_action("leave-domain")
        assert result.results == {"status": "left"}
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert "ipa-client-install" in args
        assert "--uninstall" in args
        assert "-U" in args