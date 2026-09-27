#!/usr/bin/python

import importlib.util
import json
import os
import pathlib
import tempfile
import unittest
from unittest import mock


MODULE_PATH = pathlib.Path(__file__).resolve().parents[1] / "scripts/portal_config.py"
SPEC = importlib.util.spec_from_file_location("portal_config", MODULE_PATH)
assert SPEC and SPEC.loader
portal_config = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(portal_config)


class PortalConfigTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.temporary.name)
        self.config_home = root / "config"
        self.state_home = root / "state"
        self.picker = root / "plugin/omarchy-cast-picker"
        self.picker.parent.mkdir(parents=True)
        self.picker.write_text("#!/bin/bash\n", encoding="utf-8")
        self.environment = mock.patch.dict(
            os.environ,
            {
                "XDG_CONFIG_HOME": str(self.config_home),
                "XDG_STATE_HOME": str(self.state_home),
            },
        )
        self.environment.start()

    def tearDown(self) -> None:
        self.environment.stop()
        self.temporary.cleanup()

    def test_install_and_remove_restore_existing_values(self) -> None:
        target = self.config_home / "hypr/xdph.conf"
        target.parent.mkdir(parents=True)
        original = (
            "screencopy {\n"
            "    allow_token_by_default = false\n"
            "    custom_picker_binary = /opt/old-picker\n"
            "}\n"
        )
        target.write_text(original, encoding="utf-8")

        portal_config.install(self.picker)
        installed = target.read_text(encoding="utf-8")
        self.assertIn("allow_token_by_default = false", installed)
        self.assertIn(f"custom_picker_binary = {self.picker.resolve()}", installed)

        portal_config.remove()
        self.assertEqual(target.read_text(encoding="utf-8"), original)
        self.assertFalse((self.state_home / "omacast/portal.json").exists())

    def test_remove_does_not_overwrite_a_later_user_change(self) -> None:
        portal_config.install(self.picker)
        target = self.config_home / "hypr/xdph.conf"
        changed = target.read_text(encoding="utf-8").replace(
            str(self.picker.resolve()), "/opt/new-picker"
        )
        target.write_text(changed, encoding="utf-8")

        portal_config.remove()

        self.assertIn("custom_picker_binary = /opt/new-picker", target.read_text(encoding="utf-8"))

    def test_state_records_a_full_pre_install_backup(self) -> None:
        portal_config.install(self.picker)
        state = json.loads(
            (self.state_home / "omacast/portal.json").read_text(encoding="utf-8")
        )
        self.assertEqual(state["version"], 1)
        self.assertTrue((self.state_home / "omacast/xdph.conf.before-omacast").exists())

    def test_remove_restores_legacy_allow_token_state(self) -> None:
        target = self.config_home / "hypr/xdph.conf"
        target.parent.mkdir(parents=True)
        target.write_text(
            "screencopy {\n"
            "    allow_token_by_default = true\n"
            f"    custom_picker_binary = {self.picker.resolve()}\n"
            "}\n",
            encoding="utf-8",
        )
        state_root = self.state_home / "omacast"
        state_root.mkdir(parents=True)
        (state_root / "portal.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "config": str(target),
                    "config_existed": True,
                    "section_present": True,
                    "installed_picker": str(self.picker.resolve()),
                    "previous": {
                        "allow_token_by_default": {"present": True, "value": "false"},
                        "custom_picker_binary": {"present": False, "value": ""},
                    },
                }
            ),
            encoding="utf-8",
        )

        portal_config.remove()

        restored = target.read_text(encoding="utf-8")
        self.assertIn("allow_token_by_default = false", restored)
        self.assertNotIn("custom_picker_binary", restored)


if __name__ == "__main__":
    unittest.main()
