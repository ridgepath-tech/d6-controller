"""Local-only service that exposes the verified D6 controller over HTTP/SSE."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import mimetypes
import os
import queue
import re
import subprocess
import threading
import textwrap
import time
from collections import deque
from ctypes import wintypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from d6_controller import D6Controller, D6Error


BASE_DIR = Path(__file__).resolve().parent
PROFILE_DIR = Path(os.environ.get("D6_PROFILE_DIR", str(BASE_DIR / "profiles"))).resolve()
FRONTEND_DIR = BASE_DIR / "dist"
NAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")
STRUCTURE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9 ._-]*[A-Za-z0-9])?$")
MAX_BODY_SIZE = 12 * 1024 * 1024
ACTION_IMAGE_SIZE = (100, 100)
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
MODIFIER_KEYS = {0x10, 0x11, 0x12, 0x5B, 0xA0, 0xA1, 0xA2, 0xA3, 0x5C}
SPECIAL_VK = {
    "BACKSPACE": 0x08,
    "TAB": 0x09,
    "ENTER": 0x0D,
    "RETURN": 0x0D,
    "ESC": 0x1B,
    "ESCAPE": 0x1B,
    "SPACE": 0x20,
    "PAGEUP": 0x21,
    "PAGEDOWN": 0x22,
    "END": 0x23,
    "HOME": 0x24,
    "LEFT": 0x25,
    "UP": 0x26,
    "RIGHT": 0x27,
    "DOWN": 0x28,
    "INSERT": 0x2D,
    "DELETE": 0x2E,
    "CTRL": 0x11,
    "CONTROL": 0x11,
    "SHIFT": 0x10,
    "ALT": 0x12,
    "OPTION": 0x12,
    "WIN": 0x5B,
    "WINDOWS": 0x5B,
    "META": 0x5B,
}
SPECIAL_VK.update({f"F{index}": 0x6F + index for index in range(1, 25)})


class _KeybdInput(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _MouseInput(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _HardwareInput(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _InputUnion(ctypes.Union):
    # INPUT is a tagged union. Even when sending keyboard events, the union
    # must retain the size of MOUSEINPUT on 64-bit Windows (32 bytes), making
    # the complete INPUT structure 40 bytes. Omitting those members causes
    # SendInput to reject the call with zero events sent.
    _fields_ = [
        ("mi", _MouseInput),
        ("ki", _KeybdInput),
        ("hi", _HardwareInput),
    ]


class _Input(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _InputUnion)]


CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
SW_RESTORE = 9


def _powershell_path_literal(path: Path) -> str:
    """Return a single-quoted PowerShell literal for a local path."""

    return "'" + str(path).replace("'", "''") + "'"


def find_explorer_window_for_path(path: Path) -> int | None:
    """Find an Explorer HWND currently displaying *path*.

    Explorer exposes its current folder through the Shell.Application COM
    collection.  This avoids guessing a window from a process list when more
    than one Explorer window is open.
    """

    if os.name != "nt":
        return None
    target = path.expanduser().resolve()
    script = f"""
$target = [System.IO.Path]::GetFullPath({_powershell_path_literal(target)})
$shell = New-Object -ComObject Shell.Application
$window = @($shell.Windows()) | Where-Object {{
    try {{
        $folderPath = $_.Document.Folder.Self.Path
        [System.String]::Equals(
            [System.IO.Path]::GetFullPath($folderPath),
            $target,
            [System.StringComparison]::OrdinalIgnoreCase
        )
    }} catch {{ $false }}
}} | Select-Object -First 1
if ($window) {{ [Console]::WriteLine([Int64]$window.HWND) }}
"""
    try:
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-WindowStyle",
                "Hidden",
                "-Command",
                script,
            ],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    for line in reversed(result.stdout.splitlines()):
        try:
            hwnd = int(line.strip())
        except ValueError:
            continue
        if hwnd > 0:
            return hwnd
    return None


def bring_window_to_foreground(hwnd: int) -> bool:
    """Activate a top-level window, including across Windows foreground locks."""

    if os.name != "nt" or not hwnd:
        return False
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    user32.AttachThreadInput.restype = wintypes.BOOL
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow.restype = wintypes.BOOL
    user32.BringWindowToTop.argtypes = [wintypes.HWND]
    user32.BringWindowToTop.restype = wintypes.BOOL
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL

    foreground = user32.GetForegroundWindow()
    foreground_pid = wintypes.DWORD()
    target_pid = wintypes.DWORD()
    foreground_thread = user32.GetWindowThreadProcessId(foreground, ctypes.byref(foreground_pid)) if foreground else 0
    target_thread = user32.GetWindowThreadProcessId(hwnd, ctypes.byref(target_pid))
    attached = bool(foreground_thread and target_thread and foreground_thread != target_thread)
    if attached:
        user32.AttachThreadInput(foreground_thread, target_thread, True)
    try:
        user32.ShowWindow(hwnd, SW_RESTORE)
        user32.BringWindowToTop(hwnd)
        return bool(user32.SetForegroundWindow(hwnd))
    finally:
        if attached:
            user32.AttachThreadInput(foreground_thread, target_thread, False)


def focus_explorer_path(path: str | Path, command: str | None = None, *, timeout: float = 4.0) -> bool:
    """Open/reuse an Explorer folder and activate its window in the foreground."""

    target = Path(path).expanduser().resolve()
    hwnd = find_explorer_window_for_path(target)
    if hwnd is None:
        if command:
            subprocess.Popen(command, cwd=str(BASE_DIR), shell=True, creationflags=CREATE_NO_WINDOW)
        else:
            subprocess.Popen(
                ["explorer.exe", f"/n,/root,{target}"],
                cwd=str(BASE_DIR),
                creationflags=CREATE_NO_WINDOW,
            )
        deadline = time.monotonic() + max(0.5, timeout)
        while time.monotonic() < deadline:
            time.sleep(0.15)
            hwnd = find_explorer_window_for_path(target)
            if hwnd is not None:
                break
    return bring_window_to_foreground(hwnd) if hwnd is not None else False


def valid_name(value: str) -> bool:
    return bool(value) and bool(NAME_PATTERN.fullmatch(value)) and value not in {".", ".."}


def valid_structure_name(value: str) -> bool:
    return bool(value) and bool(STRUCTURE_NAME_PATTERN.fullmatch(value)) and value not in {".", ".."}


def _is_named_key(value: str) -> bool:
    token = value.strip().upper()
    return token in SPECIAL_VK or bool(re.fullmatch(r"F(?:[1-9]|1[0-9]|2[0-4])", token))


def action_label(action: dict[str, Any] | None) -> str:
    """Return a safe LCD label without exposing literal text-entry secrets."""

    if not isinstance(action, dict):
        return ""
    explicit = str(action.get("label") or "").strip()
    if explicit:
        return explicit
    action_type = str(action.get("type") or "").strip().lower()
    if action_type == "hotkey":
        keys = [str(key).strip() for key in action.get("keys", []) if str(key).strip()]
        if len(keys) == 1 and len(keys[0]) > 1 and not _is_named_key(keys[0]):
            return "Password"
        return " + ".join(keys) or "Hotkey"
    if action_type == "navigate":
        return str(action.get("page") or action.get("scene") or "Navigate")
    if action_type == "launch":
        return str(action.get("command") or "Launch")
    return action_type.title() or "Action"


def action_font_size(action: dict[str, Any] | None, default: int = 16) -> int:
    if not isinstance(action, dict):
        return default
    try:
        value = int(action.get("font_size", default))
    except (TypeError, ValueError):
        value = default
    return max(8, min(28, value))


def _action_image_path(profile_dir: Path, profile_name: str, scene: str, page: str, key: int, action: dict[str, Any]) -> Path:
    digest = hashlib.sha1(json.dumps(action, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:12]
    path = profile_dir / "assets" / f"action-{profile_name}-{digest}-key-{key}.jpg"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def render_action_image(path: Path, label: str, font_size: int = 16) -> Path:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        raise D6Error("Pillow is required for LCD action labels") from exc

    image = Image.new("RGB", ACTION_IMAGE_SIZE, (10, 18, 29))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((2, 2, 97, 97), radius=10, outline=(45, 190, 255), width=3)
    draw.line((15, 19, 85, 19), fill=(45, 190, 255), width=2)
    font_candidates = [
        Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "segoeuib.ttf",
        Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "arialbd.ttf",
    ]
    font_path = next((candidate for candidate in font_candidates if candidate.is_file()), None)
    words = textwrap.wrap(label, width=12, break_long_words=True) or ["Action"]
    chosen_font = None
    chosen_lines = words
    requested_size = max(8, min(28, int(font_size)))
    for size in range(requested_size, 7, -1):
        font = ImageFont.truetype(str(font_path), size) if font_path else ImageFont.load_default()
        lines = textwrap.wrap(label, width=max(5, int(120 / max(size, 1))), break_long_words=True) or ["Action"]
        widths = [draw.textbbox((0, 0), line, font=font)[2] for line in lines]
        height = sum(draw.textbbox((0, 0), line, font=font)[3] for line in lines) + max(0, len(lines) - 1) * 2
        if max(widths, default=0) <= 82 and height <= 67:
            chosen_font, chosen_lines = font, lines
            break
    chosen_font = chosen_font or (ImageFont.truetype(str(font_path), 9) if font_path else ImageFont.load_default())
    heights = [draw.textbbox((0, 0), line, font=chosen_font)[3] for line in chosen_lines]
    total_height = sum(heights) + max(0, len(chosen_lines) - 1) * 2
    y = 52 - total_height / 2
    for line, height in zip(chosen_lines, heights):
        bbox = draw.textbbox((0, 0), line, font=chosen_font)
        x = (100 - (bbox[2] - bbox[0])) / 2
        draw.text((x, y), line, fill=(236, 249, 255), font=chosen_font)
        y += height + 2
    image.save(path, format="JPEG", quality=95, optimize=False)
    return path


def _key_vk(value: str) -> int | None:
    token = value.strip().upper()
    if token in SPECIAL_VK:
        return SPECIAL_VK[token]
    if len(token) == 1 and token.isprintable():
        return ord(token)
    return None


def send_hotkey(keys: list[Any]) -> None:
    """Emit a named hotkey or literal text through Windows SendInput."""

    tokens = [str(key).strip() for key in keys if str(key).strip()]
    if not tokens:
        return
    events: list[_Input] = []

    def add_vk(vk: int, flags: int = 0) -> None:
        item = _Input(type=1)
        item.ki = _KeybdInput(wVk=vk, wScan=0, dwFlags=flags, time=0, dwExtraInfo=0)
        events.append(item)

    def add_unicode(char: str, flags: int = 0) -> None:
        item = _Input(type=1)
        item.ki = _KeybdInput(wVk=0, wScan=ord(char), dwFlags=KEYEVENTF_UNICODE | flags, time=0, dwExtraInfo=0)
        events.append(item)

    if len(tokens) == 1 and not _is_named_key(tokens[0]) and len(tokens[0]) > 1:
        for char in tokens[0]:
            add_unicode(char)
            add_unicode(char, KEYEVENTF_KEYUP)
    else:
        modifiers: list[int] = []
        normal: list[int] = []
        for token in tokens:
            vk = _key_vk(token)
            if vk is None:
                for char in token:
                    add_unicode(char)
                    add_unicode(char, KEYEVENTF_KEYUP)
                continue
            (modifiers if vk in MODIFIER_KEYS else normal).append(vk)
        for vk in modifiers:
            add_vk(vk)
        for vk in normal:
            add_vk(vk)
            add_vk(vk, KEYEVENTF_KEYUP)
        for vk in reversed(modifiers):
            add_vk(vk, KEYEVENTF_KEYUP)

    if events:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(_Input), ctypes.c_int]
        user32.SendInput.restype = wintypes.UINT
        sent = user32.SendInput(len(events), (_Input * len(events))(*events), ctypes.sizeof(_Input))
        if sent != len(events):
            raise D6Error(f"SendInput sent {sent}/{len(events)} keyboard events")


def profile_path(name: str, profile_dir: Path = PROFILE_DIR) -> Path:
    if not valid_name(name):
        raise ValueError("invalid profile name")
    return profile_dir / f"{name}.json"


def load_profile(name: str, profile_dir: Path = PROFILE_DIR) -> dict[str, Any]:
    return json.loads(profile_path(name, profile_dir).read_text(encoding="utf-8"))


def save_profile(name: str, profile: dict[str, Any], profile_dir: Path = PROFILE_DIR) -> None:
    profile_dir.mkdir(parents=True, exist_ok=True)
    target = profile_path(name, profile_dir)
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(profile, indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)


def scene_names(profile: dict[str, Any]) -> list[str]:
    scenes = profile.get("scenes")
    return list(scenes) if isinstance(scenes, dict) and scenes else ["default"]


def page_names(profile: dict[str, Any], scene: str | None = None) -> list[str]:
    scenes = profile.get("scenes")
    if not isinstance(scenes, dict) or not scenes:
        return ["main"]
    selected_scene = scene or next(iter(scenes))
    scene_data = scenes.get(selected_scene, {})
    pages = scene_data.get("pages") if isinstance(scene_data, dict) else None
    return list(pages) if isinstance(pages, dict) and pages else ["main"]


def resolve_page(
    profile: dict[str, Any], scene: str | None = None, page: str | None = None
) -> tuple[dict[str, Any], str, str]:
    scenes = profile.get("scenes")
    if not isinstance(scenes, dict) or not scenes:
        return profile, scene or "default", page or "main"
    scene_name = scene or next(iter(scenes))
    scene_data = scenes.get(scene_name)
    if not isinstance(scene_data, dict):
        raise ValueError(f"unknown scene: {scene_name}")
    pages = scene_data.get("pages")
    if not isinstance(pages, dict) or not pages:
        return scene_data, scene_name, page or "main"
    page_name = page or scene_data.get("active_page") or next(iter(pages))
    page_data = pages.get(page_name)
    if not isinstance(page_data, dict):
        raise ValueError(f"unknown page: {scene_name}/{page_name}")
    return page_data, scene_name, page_name


def key_definitions(profile: dict[str, Any], scene: str | None, page: str | None) -> dict[str, Any]:
    selected, _, _ = resolve_page(profile, scene, page)
    keys = selected.get("keys", selected)
    return keys if isinstance(keys, dict) else {}


def add_structure(profile: dict[str, Any], kind: str, name: str, parent: str | None = None) -> None:
    if not valid_structure_name(name):
        raise ValueError("names may contain letters, numbers, spaces, dots, dashes, and underscores")
    scenes = profile.setdefault("scenes", {})
    if not isinstance(scenes, dict):
        raise ValueError("profile scenes must be an object")
    if kind == "scene":
        if name in scenes:
            raise ValueError("scene already exists")
        scenes[name] = {"pages": {"main": {"keys": {}}}}
        return
    if kind != "page" or not parent or parent not in scenes:
        raise ValueError("a page requires an existing parent scene")
    pages = scenes[parent].setdefault("pages", {})
    if name in pages:
        raise ValueError("page already exists")
    pages[name] = {"keys": {}}


class D6Service:
    """Own one controller handle and serialize all device writes."""

    def __init__(self, profile_dir: Path = PROFILE_DIR):
        self.profile_dir = profile_dir
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.controller: D6Controller | None = None
        self.controller_lock = threading.RLock()
        self.operation_lock = threading.RLock()
        self.stop_event = threading.Event()
        self.listener_thread: threading.Thread | None = None
        self.heartbeat_thread: threading.Thread | None = None
        self.subscribers: set[queue.Queue[dict[str, Any]]] = set()
        self.subscribers_lock = threading.Lock()
        self.recent_events: deque[dict[str, Any]] = deque(maxlen=100)
        self.last_error: str | None = None
        self.last_connect_attempt = 0.0
        self.context_lock = threading.RLock()
        self.active_profile_name: str | None = None
        self.active_scene: str | None = None
        self.active_page: str | None = None
        self.report_count = 0
        self.decoded_event_count = 0
        self.ignored_report_count = 0
        self.last_report_hex: str | None = None
        self.transport = os.environ.get("D6_TRANSPORT", "auto").strip().lower()

    def start(self) -> None:
        self.stop_event.clear()
        self.listener_thread = threading.Thread(target=self._listener_loop, daemon=True)
        self.heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self.listener_thread.start()
        self.heartbeat_thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        with self.controller_lock:
            controller, self.controller = self.controller, None
        if controller:
            controller.close()
        for thread in (self.listener_thread, self.heartbeat_thread):
            if thread and thread.is_alive():
                thread.join(timeout=1.5)

    def _connect(self) -> D6Controller | None:
        now = time.monotonic()
        if now - self.last_connect_attempt < 2:
            return None
        self.last_connect_attempt = now
        try:
            controller = D6Controller(transport=self.transport)
            with self.operation_lock:
                controller.wake_screen()
                controller.heartbeat()
        except Exception as exc:
            self.last_error = str(exc)
            if "controller" in locals():
                controller.close()
            return None
        with self.controller_lock:
            if self.controller is None:
                self.controller = controller
                self.last_error = None
                return controller
        controller.close()
        return self.controller

    def _current_controller(self) -> D6Controller | None:
        with self.controller_lock:
            controller = self.controller
        return controller or self._connect()

    def _disconnect(self, controller: D6Controller) -> None:
        with self.controller_lock:
            if self.controller is controller:
                self.controller = None
        controller.close()

    def _listener_loop(self) -> None:
        while not self.stop_event.is_set():
            controller = self._current_controller()
            if controller is None:
                self.stop_event.wait(1)
                continue
            try:
                report = controller.read_report(500)
                if report:
                    self.report_count += 1
                    self.last_report_hex = report[:64].hex()
                    event = controller.decode_key_report(report)
                    if event:
                        self.decoded_event_count += 1
                        key, pressed = event
                        self.publish_event(
                            {
                                "type": "key",
                                "key": key,
                                "pressed": pressed,
                                "timestamp": time.time(),
                            }
                        )
                        if pressed:
                            try:
                                self._dispatch_key(key)
                            except Exception as exc:
                                self.last_error = str(exc)
                                self.publish_event(
                                    {
                                        "type": "error",
                                        "message": f"Key {key} action failed: {exc}",
                                        "timestamp": time.time(),
                                    }
                                )
                    else:
                        self.ignored_report_count += 1
            except Exception as exc:
                self.last_error = str(exc)
                self._disconnect(controller)

    def _heartbeat_loop(self) -> None:
        while not self.stop_event.wait(10):
            controller = self._current_controller()
            if controller is None:
                continue
            try:
                with self.operation_lock:
                    controller.heartbeat()
            except Exception as exc:
                self.last_error = str(exc)
                self._disconnect(controller)

    def publish_event(self, event: dict[str, Any]) -> None:
        self.recent_events.append(event)
        with self.subscribers_lock:
            subscribers = list(self.subscribers)
        for subscriber in subscribers:
            subscriber.put(event)

    def subscribe(self) -> queue.Queue[dict[str, Any]]:
        subscriber: queue.Queue[dict[str, Any]] = queue.Queue()
        with self.subscribers_lock:
            self.subscribers.add(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: queue.Queue[dict[str, Any]]) -> None:
        with self.subscribers_lock:
            self.subscribers.discard(subscriber)

    def device_state(self) -> dict[str, Any]:
        with self.controller_lock:
            connected = self.controller is not None
            path = self.controller.device_path if self.controller else None
            transport = self.controller.transport if self.controller else self.transport
            transport_note = self.controller.transport_note if self.controller else None
        return {
            "connected": connected,
            "path": path,
            "transport": transport,
            "transport_note": transport_note,
            "last_error": self.last_error,
            "input": {
                "reports": self.report_count,
                "decoded_events": self.decoded_event_count,
                "ignored_reports": self.ignored_report_count,
                "last_report": self.last_report_hex,
            },
            "capabilities": D6Controller.capabilities(),
        }

    def _set_active_context(self, profile_name: str, scene: str, page: str) -> None:
        with self.context_lock:
            self.active_profile_name = profile_name
            self.active_scene = scene
            self.active_page = page

    def _active_context(self) -> tuple[str, str, str]:
        with self.context_lock:
            profile_name = self.active_profile_name
            scene = self.active_scene
            page = self.active_page
        if not profile_name:
            names = sorted(path.stem for path in self.profile_dir.glob("*.json"))
            profile_name = names[0] if names else "default"
        profile = load_profile(profile_name, self.profile_dir)
        scene = scene if scene in scene_names(profile) else scene_names(profile)[0]
        page = page if page in page_names(profile, scene) else page_names(profile, scene)[0]
        return profile_name, scene, page

    def _dispatch_key(self, key: int) -> None:
        profile_name, scene, page = self._active_context()
        profile = load_profile(profile_name, self.profile_dir)
        definition = key_definitions(profile, scene, page).get(str(key), {})
        action = definition.get("action") if isinstance(definition, dict) else None
        if not isinstance(action, dict):
            return
        action_type = str(action.get("type") or "").strip().lower()
        if action_type == "navigate":
            target_scene = str(action.get("scene") or scene)
            target_page = str(action.get("page") or page)
            applied = self.apply(profile, target_scene, target_page, profile_name=profile_name)
            self.publish_event(
                {
                    "type": "action",
                    "key": key,
                    "action": "navigate",
                    "scene": target_scene,
                    "page": target_page,
                    "count": applied,
                    "timestamp": time.time(),
                }
            )
        elif action_type == "hotkey":
            with self.operation_lock:
                send_hotkey(action.get("keys", []))
            self.publish_event(
                {
                    "type": "action",
                    "key": key,
                    "action": "hotkey",
                    "label": action_label(action),
                    "timestamp": time.time(),
                }
            )
        elif action_type == "launch":
            command = str(action.get("command") or "").strip()
            focus_path = str(action.get("focus_path") or "").strip()
            if not command and not focus_path:
                raise ValueError("launch action has no command")
            if focus_path:
                focus_explorer_path(focus_path, command or None)
            else:
                subprocess.Popen(command, cwd=str(BASE_DIR), shell=True, creationflags=CREATE_NO_WINDOW)
            self.publish_event(
                {
                    "type": "action",
                    "key": key,
                    "action": "launch",
                    "label": action_label(action),
                    "timestamp": time.time(),
                }
            )

    def apply(
        self,
        profile: dict[str, Any],
        scene: str | None,
        page: str | None,
        *,
        profile_name: str | None = None,
    ) -> int:
        controller = self._current_controller()
        if controller is None:
            raise D6Error("D6 is not connected")
        selected, selected_scene, selected_page = resolve_page(profile, scene, page)
        device = profile.get("device", {})
        brightness = selected.get("brightness", device.get("brightness"))
        keys = selected.get("keys", selected)
        with self.operation_lock:
            # A page describes the complete 15-key layout. Clear artwork left
            # by the previous page so empty keys do not retain stale images.
            controller.clear_screen()
            if brightness is not None:
                controller.set_brightness(int(brightness))
            count = 0
            if isinstance(keys, dict):
                for raw_key, definition in sorted(keys.items(), key=lambda item: int(item[0])):
                    if not isinstance(definition, dict):
                        continue
                    action = definition.get("action") if isinstance(definition.get("action"), dict) else None
                    if action and str(action.get("type") or "").lower() == "hotkey":
                        image_path = render_action_image(
                            _action_image_path(
                                self.profile_dir,
                                profile_name or "profile",
                                selected_scene,
                                selected_page,
                                int(raw_key),
                                action,
                            ),
                            action_label(action),
                            action_font_size(action),
                        )
                    elif definition.get("image"):
                        image_path = (self.profile_dir / definition["image"]).resolve()
                    else:
                        continue
                    if image_path:
                        if self.profile_dir not in image_path.parents:
                            raise ValueError("profile image is outside the profile directory")
                        controller.set_key_image(int(raw_key), image_path)
                        count += 1
        if profile_name:
            self._set_active_context(profile_name, selected_scene, selected_page)
        self.publish_event({"type": "apply", "scene": selected_scene, "page": selected_page, "count": count, "timestamp": time.time()})
        return count

    def set_brightness(self, value: int) -> None:
        if not 0 <= value <= 100:
            raise ValueError("brightness must be between 0 and 100")
        controller = self._current_controller()
        if controller is None:
            raise D6Error("D6 is not connected")
        with self.operation_lock:
            controller.set_brightness(value)

    def refresh(self) -> None:
        controller = self._current_controller()
        if controller is None:
            raise D6Error("D6 is not connected")
        with self.operation_lock:
            controller.refresh()
        self.publish_event({"type": "refresh", "timestamp": time.time()})


def json_response(handler: BaseHTTPRequestHandler, payload: Any, status: int = 200) -> None:
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class D6RequestHandler(BaseHTTPRequestHandler):
    server: "D6HTTPServer"
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        return

    @property
    def service(self) -> D6Service:
        return self.server.service

    @property
    def profile_dir(self) -> Path:
        return self.server.profile_dir

    def end_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "http://127.0.0.1:5173")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS")
        super().end_headers()

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.end_headers()

    def _body(self) -> bytes:
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("invalid content length") from exc
        if size > MAX_BODY_SIZE:
            raise ValueError("request body is too large")
        return self.rfile.read(size)

    def _json_body(self) -> dict[str, Any]:
        value = json.loads(self._body() or b"{}")
        if not isinstance(value, dict):
            raise ValueError("request body must be a JSON object")
        return value

    def _segments(self) -> list[str]:
        return [unquote(segment) for segment in urlparse(self.path).path.split("/") if segment]

    def do_GET(self) -> None:
        try:
            parsed = urlparse(self.path)
            segments = [unquote(segment) for segment in parsed.path.split("/") if segment]
            if segments and segments[0] == "api":
                self._api_get(segments)
                return
            self._static_get(parsed.path)
        except FileNotFoundError:
            json_response(self, {"error": "not found"}, 404)
        except Exception as exc:
            json_response(self, {"error": str(exc)}, 500)

    def _api_get(self, segments: list[str]) -> None:
        if segments == ["api", "state"]:
            profiles = sorted(path.stem for path in self.profile_dir.glob("*.json"))
            json_response(self, {"device": self.service.device_state(), "profiles": profiles})
            return
        if segments == ["api", "profiles"]:
            json_response(self, {"profiles": sorted(path.stem for path in self.profile_dir.glob("*.json"))})
            return
        if len(segments) == 3 and segments[:2] == ["api", "profiles"]:
            json_response(self, load_profile(segments[2], self.profile_dir))
            return
        if len(segments) == 10 and segments[:2] == ["api", "profiles"] and segments[-1] == "image":
            self._send_key_image(segments[2], segments[4], segments[6], segments[8])
            return
        if segments == ["api", "events"]:
            self._send_events()
            return
        raise FileNotFoundError

    def _send_key_image(self, name: str, scene: str, page: str, key: str) -> None:
        profile = load_profile(name, self.profile_dir)
        definition = key_definitions(profile, scene, page).get(str(int(key)), {})
        image_name = definition.get("image") if isinstance(definition, dict) else None
        if not image_name:
            raise FileNotFoundError
        image_path = (self.profile_dir / image_name).resolve()
        if self.profile_dir not in image_path.parents or not image_path.is_file():
            raise FileNotFoundError
        body = image_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(image_path.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_events(self) -> None:
        subscriber = self.service.subscribe()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            for event in list(self.service.recent_events):
                self.wfile.write(f"data: {json.dumps(event)}\n\n".encode("utf-8"))
            self.wfile.flush()
            while not self.service.stop_event.is_set():
                try:
                    event = subscriber.get(timeout=15)
                    payload = f"data: {json.dumps(event)}\n\n"
                except queue.Empty:
                    payload = ": keep-alive\n\n"
                self.wfile.write(payload.encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        finally:
            self.service.unsubscribe(subscriber)

    def _static_get(self, path: str) -> None:
        relative = path.lstrip("/") or "index.html"
        candidate = (FRONTEND_DIR / relative).resolve()
        if FRONTEND_DIR not in candidate.parents and candidate != FRONTEND_DIR:
            raise FileNotFoundError
        if not candidate.is_file():
            candidate = FRONTEND_DIR / "index.html"
        if not candidate.is_file():
            body = b"D6 service is running. Build the frontend with npm run build."
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        body = candidate.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(candidate.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_PUT(self) -> None:
        try:
            segments = self._segments()
            if len(segments) == 3 and segments[:2] == ["api", "profiles"]:
                profile = self._json_body()
                save_profile(segments[2], profile, self.profile_dir)
                json_response(self, profile)
                return
            raise FileNotFoundError
        except FileNotFoundError:
            json_response(self, {"error": "not found"}, 404)
        except ValueError as exc:
            json_response(self, {"error": str(exc)}, 400)
        except Exception as exc:
            json_response(self, {"error": str(exc)}, 500)

    def do_POST(self) -> None:
        try:
            segments = self._segments()
            if len(segments) == 4 and segments[:2] == ["api", "profiles"] and segments[3] == "apply":
                data = self._json_body()
                profile = load_profile(segments[2], self.profile_dir)
                count = self.service.apply(
                    profile,
                    data.get("scene"),
                    data.get("page"),
                    profile_name=segments[2],
                )
                json_response(self, {"applied": count})
                return
            if segments == ["api", "device", "brightness"]:
                data = self._json_body()
                self.service.set_brightness(int(data["value"]))
                json_response(self, {"brightness": int(data["value"])})
                return
            if segments == ["api", "device", "refresh"]:
                self.service.refresh()
                json_response(self, {"refreshed": True})
                return
            if len(segments) == 4 and segments[:2] == ["api", "profiles"] and segments[3] == "structure":
                data = self._json_body()
                profile = load_profile(segments[2], self.profile_dir)
                add_structure(profile, str(data.get("kind")), str(data.get("name")), data.get("parent"))
                save_profile(segments[2], profile, self.profile_dir)
                json_response(self, profile)
                return
            if len(segments) == 10 and segments[:2] == ["api", "profiles"] and segments[-1] == "image":
                self._upload_key_image(segments[2], segments[4], segments[6], segments[8])
                return
            raise FileNotFoundError
        except FileNotFoundError:
            json_response(self, {"error": "not found"}, 404)
        except (ValueError, KeyError) as exc:
            json_response(self, {"error": str(exc)}, 400)
        except Exception as exc:
            json_response(self, {"error": str(exc)}, 500)

    def _upload_key_image(self, name: str, scene: str, page: str, key: str) -> None:
        raw_key = int(key)
        if not 1 <= raw_key <= 15:
            raise ValueError("key must be between 1 and 15")
        body = self._body()
        if not body:
            raise ValueError("image body is empty")
        profile = load_profile(name, self.profile_dir)
        selected, _, _ = resolve_page(profile, scene, page)
        keys = selected.setdefault("keys", {})
        if not isinstance(keys, dict):
            raise ValueError("selected page keys must be an object")
        assets = self.profile_dir / "assets"
        assets.mkdir(parents=True, exist_ok=True)
        filename = f"{name}-{scene}-{page}-key-{raw_key}.jpg"
        image_path = (assets / filename).resolve()
        if assets.resolve() not in image_path.parents:
            raise ValueError("invalid image path")
        image_path.write_bytes(body)
        keys[str(raw_key)] = {**(keys.get(str(raw_key)) or {}), "image": str(image_path.relative_to(self.profile_dir))}
        save_profile(name, profile, self.profile_dir)
        json_response(self, {"profile": profile, "image": str(image_path.relative_to(self.profile_dir))})


class D6HTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], service: D6Service):
        self.service = service
        self.profile_dir = service.profile_dir
        super().__init__(address, D6RequestHandler)


def main() -> int:
    parser = argparse.ArgumentParser(description="Local FIFINE D6 controller service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit("For safety, the D6 service only binds to localhost")
    service = D6Service()
    service.start()
    server = D6HTTPServer((args.host, args.port), service)
    print(f"D6 service listening at http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
        service.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
