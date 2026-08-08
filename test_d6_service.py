import json
import io
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen

from d6_auth import AuthError, AuthStore
from d6_service import (
    D6HTTPServer,
    D6Service,
    action_label,
    action_font_size,
    add_structure,
    focus_explorer_path,
    key_definitions,
    load_profile,
    launch_target,
    normalize_profile,
    resolve_page,
    select_folder_native,
    save_profile,
    _fit_font,
)


class D6ServiceHelpersTests(unittest.TestCase):
    def test_auth_store_hashes_password_and_rotates_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = AuthStore(Path(temporary) / "auth.json")
            token = store.setup("TestPassword123", "TestPassword123")
            self.assertTrue(store.session_valid(token))
            self.assertNotIn("TestPassword123", Path(temporary, "auth.json").read_text())
            with self.assertRaises(AuthError):
                store.login("wrong-password")
            rotated = store.change_password(token, "NewPassword123", "NewPassword123")
            self.assertFalse(store.session_valid(token))
            self.assertTrue(store.session_valid(rotated))

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
        self.assertEqual(action_label({"type": "back"}), "Back")
        self.assertEqual(action_label({"type": "page_indicator"}, page_index=1, page_total=3), "2/3")
        self.assertEqual(action_label({"type": "website", "label": "Docs"}), "Docs")
        self.assertEqual(action_label({"type": "open_folder", "path": "D:\\Projects", "label": "Work\nProjects"}), "Work\nProjects")

    def test_lcd_label_layout_preserves_explicit_line_breaks(self) -> None:
        from PIL import Image, ImageDraw

        image = Image.new("RGB", (100, 100))
        _, lines = _fit_font(ImageDraw.Draw(image), None, "Work\nProjects", 16, (10, 10, 90, 90))
        self.assertEqual(lines, ["Work", "Projects"])

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

    def test_legacy_explorer_launch_normalizes_to_open_folder(self) -> None:
        profile = {"scenes": {"default": {"pages": {"main": {"keys": {"12": {"action": {"type": "launch", "command": "explorer.exe /n,/root,\\\"D:\\\\Projects\\\"", "focus_path": "D:\\Projects", "label": "Projects"}}}}}}}}
        normalized = normalize_profile(profile)
        self.assertEqual(normalized["scenes"]["default"]["pages"]["main"]["keys"]["12"]["action"]["type"], "open_folder")

    def test_backup_round_trip_preserves_profile_and_artwork_without_auth(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            profile_dir = Path(temporary) / "profiles"
            data_dir = Path(temporary) / "data"
            profile_dir.mkdir()
            (profile_dir / "assets").mkdir()
            save_profile("default", {"scenes": {"default": {"pages": {"main": {"keys": {"1": {"image": "assets/one.jpg"}}}}}}}, profile_dir)
            (profile_dir / "assets" / "one.jpg").write_bytes(b"art")
            service = D6Service(profile_dir, data_dir)
            service.auth.setup("TestPassword123", "TestPassword123")
            backup = service.export_config()
            self.assertNotIn(b"auth.json", backup)
            (profile_dir / "default.json").unlink()
            (profile_dir / "assets" / "one.jpg").unlink()
            restored = service.restore_config(backup)
            self.assertEqual(restored, ["default"])
            self.assertTrue((profile_dir / "assets" / "one.jpg").is_file())

    def test_backup_rejects_zip_slip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            service = D6Service(Path(temporary) / "profiles", Path(temporary) / "data")
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, "w") as archive:
                archive.writestr("manifest.json", json.dumps({"format": "d6config", "schema_version": 1}))
                archive.writestr("../outside.json", "bad")
            with self.assertRaises(ValueError):
                service.restore_config(stream.getvalue())

    def test_native_folder_picker_returns_selection_cancel_and_rejects_bad_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            selected = Path(temporary).resolve()
            completed = type("Completed", (), {"stdout": f"{selected}\n"})()
            with patch("d6_service.subprocess.run", return_value=completed):
                self.assertEqual(select_folder_native(), str(selected))
            cancelled = type("Completed", (), {"stdout": ""})()
            with patch("d6_service.subprocess.run", return_value=cancelled):
                self.assertIsNone(select_folder_native())
            malformed = type("Completed", (), {"stdout": "C:\\does-not-exist\\d6\n"})()
            with patch("d6_service.subprocess.run", return_value=malformed):
                with self.assertRaises(ValueError):
                    select_folder_native()

    def test_launch_target_activates_a_new_window_with_a_bounded_helper(self) -> None:
        class FakeProcess:
            pid = 42

        with patch("d6_service.subprocess.Popen", return_value=FakeProcess()), patch("d6_service.find_window_for_process", return_value=99), patch("d6_service.bring_window_to_foreground", return_value=True) as focus:
            result = launch_target("notepad.exe")
        self.assertEqual(result["pid"], 42)
        self.assertTrue(result["focused"])
        focus.assert_called_once_with(99)

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
            service = D6Service(profile_dir, Path(temporary) / "data")
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
            mocked.assert_called_once_with("D:\\Projects", timeout=4.0)

    def test_key_dispatch_supports_page_controls_and_website(self) -> None:
        class FakeController:
            def clear_screen(self) -> None:
                return None

            def set_key_image(self, key, path) -> None:
                return None

            def sleep_screen(self) -> None:
                self.slept = True

        with tempfile.TemporaryDirectory() as temporary:
            profile_dir = Path(temporary)
            profile = {
                "scenes": {
                    "default": {
                        "pages": {
                            "main": {"keys": {"1": {"action": {"type": "navigate", "page": "Second"}}}},
                            "Second": {"keys": {"1": {"action": {"type": "previous_page"}}, "2": {"action": {"type": "website", "url": "https://example.com"}}, "3": {"action": {"type": "sleep"}}}},
                        }
                    }
                }
            }
            save_profile("default", profile, profile_dir)
            service = D6Service(profile_dir)
            service.controller = FakeController()
            service._set_active_context("default", "default", "main")
            service._dispatch_key(1)
            self.assertEqual(service.active_page, "Second")
            service._dispatch_key(1)
            self.assertEqual(service.active_page, "main")
            with patch("d6_service.open_website") as mocked:
                service._set_active_context("default", "default", "Second")
                service._dispatch_key(2)
            mocked.assert_called_once_with("https://example.com")
            service._dispatch_key(3)
            self.assertTrue(service.controller.slept)


class D6ServiceHttpTests(unittest.TestCase):
    def test_state_and_static_frontend_routes_do_not_require_hid(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            profile_dir = Path(temporary)
            save_profile("default", {"scenes": {}}, profile_dir)
            service = D6Service(profile_dir, Path(temporary) / "data")
            server = D6HTTPServer(("127.0.0.1", 0), service)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base_url = f"http://127.0.0.1:{server.server_address[1]}"
                with urlopen(f"{base_url}/api/auth/status") as response:
                    status = json.load(response)
                self.assertTrue(status["setup_required"])
                opener = build_opener(HTTPCookieProcessor())
                setup_request = Request(f"{base_url}/api/auth/setup", data=json.dumps({"password": "TestPassword123", "confirmation": "TestPassword123"}).encode(), headers={"Content-Type": "application/json"}, method="POST")
                with opener.open(setup_request) as response:
                    self.assertEqual(response.status, 200)
                with opener.open(f"{base_url}/api/state") as response:
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
