import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.request import urlopen

from d6_service import (
    D6HTTPServer,
    D6Service,
    action_label,
    action_font_size,
    add_structure,
    focus_explorer_path,
    key_definitions,
    load_profile,
    resolve_page,
    save_profile,
)


class D6ServiceHelpersTests(unittest.TestCase):
    def test_refresh_forwards_signal_to_controller(self) -> None:
        class FakeController:
            def refresh(self) -> None:
                self.refreshed = True

        with tempfile.TemporaryDirectory() as temporary:
            service = D6Service(Path(temporary))
            controller = FakeController()
            controller.refreshed = False
            service.controller = controller
            service.refresh()
            self.assertTrue(controller.refreshed)

    def test_action_font_size_is_clamped(self) -> None:
        self.assertEqual(action_font_size({"font_size": 12}), 12)
        self.assertEqual(action_font_size({"font_size": 3}), 8)
        self.assertEqual(action_font_size({"font_size": 40}), 28)
        self.assertEqual(action_font_size({"font_size": "bad"}), 16)

    def test_action_labels_preserve_named_hotkeys_without_exposing_text(self) -> None:
        self.assertEqual(action_label({"type": "hotkey", "keys": ["CTRL", "Shift", "F1"]}), "CTRL + Shift + F1")
        self.assertEqual(action_label({"type": "hotkey", "keys": ["DemoSecret!983"]}), "Password")
        self.assertEqual(action_label({"type": "hotkey", "keys": ["DemoSecret!983"], "label": "Work account"}), "Work account")

    def test_structure_and_page_resolution(self) -> None:
        profile = {"scenes": {"default": {"pages": {"main": {"keys": {"1": {"action": "hotkey"}}}}}}}

        add_structure(profile, "scene", "streaming")
        add_structure(profile, "page", "starting", "default")
        add_structure(profile, "scene", "Password Entry")
        add_structure(profile, "page", "Work Accounts", "Password Entry")

        selected, scene, page = resolve_page(profile, "default", "starting")
        self.assertEqual((scene, page), ("default", "starting"))
        self.assertEqual(selected, {"keys": {}})
        self.assertEqual(key_definitions(profile, "default", "main")["1"]["action"], "hotkey")

    def test_profile_round_trip_is_atomic_at_helper_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            profile_dir = Path(temporary)
            profile = {"name": "test", "scenes": {"default": {"pages": {"main": {"keys": {}}}}}}
            save_profile("test", profile, profile_dir)
            self.assertEqual(load_profile("test", profile_dir), profile)

    def test_apply_renders_hotkey_label_for_the_lcd(self) -> None:
        class FakeController:
            def __init__(self) -> None:
                self.images = []

            def clear_screen(self) -> None:
                return None

            def set_key_image(self, key, path) -> None:
                self.images.append((key, Path(path)))

        with tempfile.TemporaryDirectory() as temporary:
            profile_dir = Path(temporary)
            service = D6Service(profile_dir)
            controller = FakeController()
            service.controller = controller
            profile = {"scenes": {"default": {"pages": {"main": {"keys": {"1": {"action": {"type": "hotkey", "keys": ["CTRL", "Shift", "F1"]}}}}}}}}
            self.assertEqual(service.apply(profile, "default", "main", profile_name="default"), 1)
            self.assertEqual(controller.images[0][0], 1)
            self.assertTrue(controller.images[0][1].is_file())

    def test_key_dispatch_navigates_and_emits_hotkeys(self) -> None:
        class FakeController:
            def clear_screen(self) -> None:
                return None

            def set_key_image(self, key, path) -> None:
                return None

        with tempfile.TemporaryDirectory() as temporary:
            profile_dir = Path(temporary)
            profile = {
                "scenes": {
                    "default": {
                        "pages": {
                            "main": {"keys": {"1": {"action": {"type": "navigate", "scene": "default", "page": "Password Entry"}}}},
                            "Password Entry": {"keys": {"1": {"action": {"type": "hotkey", "keys": ["Ctrl", "Shift", "F1"]}}}},
                        }
                    }
                }
            }
            save_profile("default", profile, profile_dir)
            service = D6Service(profile_dir)
            service.controller = FakeController()
            service._set_active_context("default", "default", "main")
            service._dispatch_key(1)
            self.assertEqual((service.active_scene, service.active_page), ("default", "Password Entry"))
            with patch("d6_service.send_hotkey") as mocked:
                service._dispatch_key(1)
            mocked.assert_called_once_with(["Ctrl", "Shift", "F1"])

    def test_key_dispatch_focuses_a_configured_folder(self) -> None:
        class FakeController:
            def clear_screen(self) -> None:
                return None

            def set_key_image(self, key, path) -> None:
                return None

        with tempfile.TemporaryDirectory() as temporary:
            profile_dir = Path(temporary)
            profile = {
                "scenes": {
                    "default": {
                        "pages": {
                            "main": {
                                "keys": {
                                    "12": {
                                        "action": {
                                            "type": "launch",
                                            "command": "explorer.exe /n,/root,\\\"D:\\\\Projects\\\"",
                                            "focus_path": "D:\\Projects",
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
            save_profile("default", profile, profile_dir)
            service = D6Service(profile_dir)
            service.controller = FakeController()
            service._set_active_context("default", "default", "main")
            with patch("d6_service.focus_explorer_path") as mocked:
                service._dispatch_key(12)
            mocked.assert_called_once_with("D:\\Projects", 'explorer.exe /n,/root,\\\"D:\\\\Projects\\\"')


class D6ServiceHttpTests(unittest.TestCase):
    def test_state_and_static_frontend_routes_do_not_require_hid(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            profile_dir = Path(temporary)
            save_profile("default", {"scenes": {}}, profile_dir)
            service = D6Service(profile_dir)
            server = D6HTTPServer(("127.0.0.1", 0), service)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base_url = f"http://127.0.0.1:{server.server_address[1]}"
                with urlopen(f"{base_url}/api/state") as response:
                    state = json.load(response)
                self.assertEqual(state["profiles"], ["default"])
                self.assertIn("capabilities", state["device"])

                with urlopen(f"{base_url}/") as response:
                    self.assertEqual(response.status, 200)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)
                service.stop()


if __name__ == "__main__":
    unittest.main()
