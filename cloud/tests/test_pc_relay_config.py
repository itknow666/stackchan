import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class PCRelayConfigTests(unittest.TestCase):
    def test_compose_pc_relay_is_opt_in(self):
        compose = (ROOT / "cloud/stackchan-server/docker-compose.standalone.yml").read_text(encoding="utf-8")
        example = (ROOT / "cloud/stackchan-server/.env.standalone.example").read_text(encoding="utf-8")
        for key in ("STACKCHAN_PC_TOKEN", "STACKCHAN_PC_DEVICE_IDS"):
            self.assertIn(f"{key}: ${{{key}:-}}", compose)
            self.assertIn(f"\n{key}=\n", example)
        self.assertIn("STACKCHAN_PC_ID: ${STACKCHAN_PC_ID:-desktop}", compose)

    def render(self, enabled=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = {"domain": "robot.example.com", "device_id": "00:00:00:00:00:00"}
            if enabled is not None:
                config["pc_relay_enabled"] = enabled
            source = root / "config.json"
            source.write_text(json.dumps(config), encoding="utf-8")
            result = subprocess.run([sys.executable, str(ROOT / "tools/render-gateway.py"),
                                     str(source), "--output", str(root / "generated")],
                                    capture_output=True, text=True)
            content = root / "generated/Caddyfile"
            return result, content.read_text(encoding="utf-8") if content.exists() else ""

    def test_gateway_only_exposes_pc_route_when_requested(self):
        for enabled in (None, False, True):
            result, content = self.render(enabled)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual("@computer path /dania/pc/ws" in content, enabled is True)
            self.assertNotIn("PC_RELAY_ROUTE", content)
            self.assertIn("reverse_proxy 127.0.0.1:12800", content)
            self.assertIn("respond 404", content)
            self.assertNotIn("robot.example.com/", result.stdout)

    def test_gateway_rejects_truthy_string(self):
        result, content = self.render("false")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(content)


if __name__ == "__main__":
    unittest.main()
