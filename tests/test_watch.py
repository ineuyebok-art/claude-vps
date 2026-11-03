"""Watchdog: keys, schedules, tunnels."""
import importlib.machinery
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "server/bin/watch"


def load():
    loader = importlib.machinery.SourceFileLoader("ccwatch", str(SCRIPT))
    spec = importlib.util.spec_from_loader("ccwatch", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


watch = load()


class Tunnels(unittest.TestCase):
    def test_detected(self):
        for argv in (["ssh", "-R", "9000:localhost:22", "host"], ["/usr/bin/ssh", "-fNR", "9:l:22", "host"],
                     ["ssh", "-NR9000:l:22", "host"], ["ssh", "-oRemoteForward=9 l:22", "host"],
                     ["ssh", "-o", "RemoteForward 9 l:22", "host"], ["ssh", "host", "-R", "9:l:22"],
                     ["autossh", "-M", "0", "host"], ["/opt/x/autossh", "host"]):
            self.assertTrue(watch.is_tunnel(argv), argv)

    def test_not_detected(self):
        for argv in (["ssh", "host"], ["ssh", "-L", "5901:127.0.0.1:5901", "host"], ["ssh", "-i", "R", "host"],
                     ["ssh", "-lR", "host"], ["ssh", "-o", "ServerAliveInterval=30", "host"],
                     ["ssh", "host", "ls", "-R"], ["sshd:", "claude@pts/0"],
                     ["/home/claude/.mutagen/agents/0.18.1/mutagen-agent", "synchronizer"], ["grep", "-R", "x"], []):
            self.assertFalse(watch.is_tunnel(argv), argv)


class Scenarios(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        (self.tmp / "home").mkdir()
        self.keys = self.tmp / "keys"
        self.keys.write_text("ssh-ed25519 AAAA pc\n", encoding="ascii")
        self.cron = self.tmp / "cron"
        self.alerts = self.tmp / "alerts"
        self.env = dict(os.environ, CCVPS_KEYS_FILE=str(self.keys), CCVPS_KEYS_BASELINE=str(self.tmp / "base"),
                        CCVPS_WATCH_STATE=str(self.tmp / "state.json"), CCVPS_ALERTS=str(self.alerts),
                        CCVPS_CRON=str(self.cron), CCVPS_HOME=str(self.tmp / "home"), CCVPS_NO_PKILL="1")
        self.run_watch()  # baseline

    def run_watch(self):
        subprocess.run([sys.executable, str(SCRIPT)], env=self.env, check=True)

    def alert_texts(self):
        return [p.read_text(encoding="utf-8") for p in sorted(self.alerts.glob("*.txt"))] if self.alerts.exists() else []

    def test_quiet_when_nothing_changed(self):
        self.run_watch()
        self.assertEqual(self.alert_texts(), [])

    def test_manual_key_raises_alert_once(self):
        with self.keys.open("a", encoding="ascii") as f:
            f.write("ssh-ed25519 BBBB evil\n")
        self.run_watch()
        self.assertEqual(len(self.alert_texts()), 1)
        self.assertIn("SSH-ключи", self.alert_texts()[0])
        self.run_watch()
        self.assertEqual(len(self.alert_texts()), 1)

    def test_key_added_by_helper_is_quiet(self):
        import base64
        import struct
        name = b"ssh-ed25519"
        blob = base64.b64encode(struct.pack(">I", len(name)) + name + struct.pack(">I", 32) + os.urandom(32)).decode()
        subprocess.run([sys.executable, str(ROOT / "server/bin/keys"), "add", "ssh-ed25519", blob, "pc"],
                       env=self.env, check=True, capture_output=True)
        self.run_watch()
        self.assertEqual(self.alert_texts(), [])

    def test_cron_reboot_raises_alert(self):
        self.cron.write_text("# comment\n@reboot /tmp/x\n", encoding="utf-8")
        self.run_watch()
        self.assertIn("@reboot /tmp/x", self.alert_texts()[0])

    def test_user_systemd_unit_raises_alert(self):
        folder = self.tmp / "home/.config/systemd/user"
        folder.mkdir(parents=True)
        (folder / "evil.service").write_text("[Service]\n", encoding="utf-8")
        self.run_watch()
        self.assertIn("evil.service", self.alert_texts()[0])

    @unittest.skipUnless(shutil.which("ssh"), "ssh client not installed")
    def test_real_outgoing_tunnel_raises_alert(self):
        proc = subprocess.Popen(["ssh", "-N", "-F", "/dev/null", "-o", "BatchMode=yes", "-o", "ProxyCommand=sleep 20",
                                 "-R", "9999:127.0.0.1:22", "tunnel-target"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(proc.wait)
        self.addCleanup(proc.kill)
        time.sleep(0.2)
        self.run_watch()
        self.assertTrue(any("туннель" in t and "-R" in t for t in self.alert_texts()))

    def test_mutagen_agent_is_quiet(self):
        agent = self.tmp / "home/.mutagen/agents/0.18.1"
        agent.mkdir(parents=True)
        shutil.copy("/bin/sleep", agent / "mutagen-agent")
        proc = subprocess.Popen([str(agent / "mutagen-agent"), "5"])
        self.addCleanup(proc.wait)
        self.addCleanup(proc.kill)
        self.run_watch()
        self.assertEqual(self.alert_texts(), [])


if __name__ == "__main__":
    unittest.main()
