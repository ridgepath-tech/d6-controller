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
import shutil
import subprocess
import threading
import time
import tempfile
import uuid
import zipfile
import io
from collections import deque
from ctypes import wintypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from d6_controller import D6Controller, D6Error
from d6_auth import AuthError, AuthStore


BASE_DIR = Path(__file__).resolve().parent
PROFILE_DIR = Path(os.environ.get("D6_PROFILE_DIR", str(BASE_DIR / "profiles"))).resolve()
DATA_DIR = Path(os.environ.get("D6_DATA_DIR", str(BASE_DIR / "data"))).resolve()
AUTH_PATH = DATA_DIR / "auth.json"
FRONTEND_DIR = BASE_DIR / "dist"
NAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")
STRUCTURE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9 ._-]*[A-Za-z0-9])?$")
MAX_BODY_SIZE = 12 * 1024 * 1024
MAX_BACKUP_SIZE = 64 * 1024 * 1024
CONFIG_SCHEMA_VERSION = 1
SESSION_COOKIE = "d6_session"
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


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort), ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]


def _guid(value: str) -> _GUID:
    parsed = uuid.UUID(value)
    return _GUID(parsed.time_low, parsed.time_mid, parsed.time_hi_version, (ctypes.c_ubyte * 8)(*parsed.bytes[8:]))


CLSID_MMDEVICE_ENUMERATOR = _guid("BCDE0395-E52F-467C-8E3D-C4579291692E")
IID_IMMDEVICE_ENUMERATOR = _guid("A95664D2-9614-4F35-A746-DE8DB63617E6")
IID_IAUDIO_ENDPOINT_VOLUME = _guid("5CDF2C82-841E-4546-9722-0CF74078229A")
CLSCTX_INPROC_SERVER = 1
E_CAPTURE = 1
E_CONSOLE = 0


class RequestDenied(PermissionError):
    """Raised when a request is not from the local configurator boundary."""


CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
SW_RESTORE = 9
BUILTIN_ACTION_LABELS = {
    "back": "Back",
    "home": "Home",
    "previous_page": "Prev",
    "next_page": "Next",
    "page_indicator": "Page",
    "sleep": "Sleep",
    "mic_mute": "Mic",
}
BUILTIN_ACTION_TYPES = set(BUILTIN_ACTION_LABELS)
RENDERED_ACTION_TYPES = BUILTIN_ACTION_TYPES | {"website", "launch", "open_folder", "navigate", "hotkey"}


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


def foreground_window() -> int | None:
    if os.name != "nt":
        return None
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetForegroundWindow.restype = wintypes.HWND
    return int(user32.GetForegroundWindow() or 0) or None


def find_window_for_process(pid: int) -> int | None:
    """Return a visible top-level window owned by *pid*, if one exists."""

    if os.name != "nt" or not pid:
        return None
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM), wintypes.LPARAM]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    found: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        owner_pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner_pid))
        if owner_pid.value == pid:
            found.append(int(hwnd))
            return False
        return True

    user32.EnumWindows(callback, 0)
    return found[0] if found else None


def process_ids_by_name(name: str) -> list[int]:
    if os.name != "nt":
        return []
    try:
        result = subprocess.run(
            ["tasklist.exe", "/FI", f"IMAGENAME eq {name}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=3,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    values: list[int] = []
    for line in result.stdout.splitlines():
        fields = [field.strip('"') for field in line.split(",")]
        if len(fields) < 2 or fields[0].lower() != name.lower():
            continue
        try:
            values.append(int(fields[1]))
        except ValueError:
            continue
    return values


def launch_target(command: str, *, process_name: str | None = None, timeout: float = 4.0) -> dict[str, Any]:
    """Launch a visible Windows target and make a bounded foreground attempt."""

    if not command.strip():
        raise ValueError("launch action has no command")
    before = set(process_ids_by_name(process_name)) if process_name else set()
    process = subprocess.Popen(command, cwd=str(BASE_DIR), shell=True, creationflags=CREATE_NO_WINDOW)
    deadline = time.monotonic() + max(0.5, timeout)
    hwnd = find_window_for_process(process.pid)
    if hwnd is None and process_name:
        while time.monotonic() < deadline:
            candidates = [pid for pid in process_ids_by_name(process_name) if pid not in before]
            for candidate in candidates or process_ids_by_name(process_name):
                hwnd = find_window_for_process(candidate)
                if hwnd:
                    break
            if hwnd:
                break
            time.sleep(0.15)
    focused = bring_window_to_foreground(hwnd) if hwnd else False
    return {"pid": process.pid, "hwnd": hwnd, "focused": focused}


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


def open_website(url: str) -> bool:
    """Open an explicitly web-only action in the user's default browser."""

    value = str(url or "").strip()
    parsed = urlparse(value)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise ValueError("website actions require an http:// or https:// URL")
    before = foreground_window()
    startfile = getattr(os, "startfile", None)
    if startfile:
        startfile(value)
    else:
        subprocess.Popen(["xdg-open", value], creationflags=CREATE_NO_WINDOW)
    if os.name != "nt":
        return True
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        current = foreground_window()
        if current and current != before:
            return bring_window_to_foreground(current)
        time.sleep(0.1)
    current = foreground_window()
    return bool(current and bring_window_to_foreground(current))


def select_folder_native() -> str | None:
    """Open a real Windows folder picker without flashing a console window."""

    if os.name != "nt":
        raise ValueError("native folder selection is only available on Windows")
    script = r"""
Add-Type -AssemblyName System.Windows.Forms
[System.Windows.Forms.Application]::EnableVisualStyles()
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = 'Choose a folder for the D6 action'
$dialog.ShowNewFolderButton = $false
if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
    [Console]::WriteLine([System.IO.Path]::GetFullPath($dialog.SelectedPath))
}
"""
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-STA", "-WindowStyle", "Hidden", "-Command", script],
            capture_output=True,
            text=True,
            timeout=300,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("native folder dialog could not be opened") from exc
    value = next((line.strip() for line in result.stdout.splitlines() if line.strip()), "")
    if not value:
        return None
    path = Path(value).expanduser().resolve()
    if not path.is_dir():
        raise ValueError("folder dialog returned an inaccessible folder")
    return str(path)


def valid_name(value: str) -> bool:
    return bool(value) and bool(NAME_PATTERN.fullmatch(value)) and value not in {".", ".."}


def valid_structure_name(value: str) -> bool:
    return bool(value) and bool(STRUCTURE_NAME_PATTERN.fullmatch(value)) and value not in {".", ".."}


def _is_named_key(value: str) -> bool:
    token = value.strip().upper()
    return token in SPECIAL_VK or bool(re.fullmatch(r"F(?:[1-9]|1[0-9]|2[0-4])", token))


def action_label(action: dict[str, Any] | None, *, page_index: int | None = None, page_total: int | None = None) -> str:
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
    if action_type == "open_folder":
        return str(action.get("path") or "Folder")
    if action_type == "website":
        return "Website"
    if action_type == "page_indicator" and page_index is not None and page_total:
        return f"{page_index + 1}/{page_total}"
    if action_type in BUILTIN_ACTION_LABELS:
        return BUILTIN_ACTION_LABELS[action_type]
    return action_type.title() or "Action"


def action_font_size(action: dict[str, Any] | None, default: int = 16) -> int:
    if not isinstance(action, dict):
        return default
    try:
        value = int(action.get("font_size", default))
    except (TypeError, ValueError):
        value = default
    return max(8, min(28, value))


def _action_image_path(
    profile_dir: Path,
    profile_name: str,
    scene: str,
    page: str,
    key: int,
    action: dict[str, Any],
    *,
    page_index: int = 0,
    page_total: int = 1,
) -> Path:
    digest = hashlib.sha1(
        json.dumps(
            {"action": action, "profile": profile_name, "scene": scene, "page": page, "key": key, "page_index": page_index, "page_total": page_total},
            sort_keys=True,
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()[:12]
    path = profile_dir / "assets" / f"action-{profile_name}-{digest}-key-{key}.jpg"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _fit_font(draw: Any, font_path: Path | None, text: str, requested_size: int, box: tuple[int, int, int, int]) -> tuple[Any, list[str]]:
    from PIL import ImageFont

    left, top, right, bottom = box
    for size in range(max(8, min(28, int(requested_size))), 7, -1):
        font = ImageFont.truetype(str(font_path), size) if font_path else ImageFont.load_default()
        lines: list[str] = []
        for paragraph in str(text).replace("\r\n", "\n").replace("\r", "\n").split("\n"):
            if not paragraph:
                lines.append("")
                continue
            current = ""
            for word in paragraph.split():
                candidate = word if not current else f"{current} {word}"
                if draw.textbbox((0, 0), candidate, font=font)[2] <= right - left:
                    current = candidate
                    continue
                if current:
                    lines.append(current)
                current = ""
                while word and draw.textbbox((0, 0), word, font=font)[2] > right - left:
                    chunk = ""
                    for character in word:
                        if draw.textbbox((0, 0), chunk + character, font=font)[2] > right - left:
                            break
                        chunk += character
                    if not chunk:
                        chunk = word[0]
                    lines.append(chunk)
                    word = word[len(chunk):]
                current = word
            if current:
                lines.append(current)
        lines = lines or ["Action"]
        widths = [draw.textbbox((0, 0), line, font=font)[2] for line in lines]
        height = sum(draw.textbbox((0, 0), line, font=font)[3] for line in lines) + max(0, len(lines) - 1) * 2
        if max(widths, default=0) <= right - left and height <= bottom - top:
            return font, lines
    return (ImageFont.truetype(str(font_path), 8) if font_path else ImageFont.load_default()), [line[:18] for line in str(text).splitlines() or ["Action"]]


def _draw_fitted_text(
    draw: Any,
    font: Any,
    lines: list[str],
    box: tuple[int, int, int, int],
    fill: tuple[int, int, int],
    shadow: tuple[int, int, int] | None = None,
) -> None:
    left, top, right, bottom = box
    heights = [draw.textbbox((0, 0), line, font=font)[3] for line in lines]
    total_height = sum(heights) + max(0, len(lines) - 1) * 2
    y = top + max(0, (bottom - top - total_height) / 2)
    for line, height in zip(lines, heights):
        bbox = draw.textbbox((0, 0), line, font=font)
        x = left + max(0, (right - left - (bbox[2] - bbox[0])) / 2)
        if shadow:
            draw.text((x + 1, y + 1), line, fill=shadow, font=font)
        draw.text((x, y), line, fill=fill, font=font)
        y += height + 2


def render_action_image(path: Path, label: str, font_size: int = 16, action_type: str | None = None, *, muted: bool = False) -> Path:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        raise D6Error("Pillow is required for LCD action labels") from exc

    image = Image.new("RGB", ACTION_IMAGE_SIZE, (7, 22, 34))
    draw = ImageDraw.Draw(image)
    outline = (53, 196, 190)
    accent = (45, 155, 216)
    light = (235, 250, 249)
    font_candidates = [
        Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "segoeuib.ttf",
        Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "arialbd.ttf",
    ]
    font_path = next((candidate for candidate in font_candidates if candidate.is_file()), None)
    action_type = (action_type or "label").lower()
    if action_type != "mic_mute":
        draw.rounded_rectangle((2, 2, 97, 97), radius=10, outline=outline, width=3)
        draw.line((15, 17, 85, 17), fill=accent, width=2)
    if action_type == "open_folder":
        folder = [(13, 38), (13, 30), (35, 30), (41, 35), (86, 35), (86, 80), (13, 80)]
        draw.polygon(folder, fill=(11, 91, 119), outline=outline)
        draw.line((17, 47, 82, 47), fill=(99, 220, 205), width=2)
        text_box = (17, 49, 82, 79)
    else:
        icon_y = 45
        if action_type == "back":
            draw.polygon([(18, icon_y), (43, 24), (43, 34), (71, 34), (82, 45), (71, 56), (43, 56), (43, 66)], fill=accent)
            text_box = (14, 68, 86, 92)
        elif action_type == "home":
            draw.polygon([(18, 43), (50, 21), (82, 43), (75, 43), (75, 70), (25, 70), (25, 43)], fill=accent)
            draw.rectangle((43, 51, 57, 70), fill=(7, 22, 34), outline=outline)
            text_box = (14, 72, 86, 93)
        elif action_type in {"previous_page", "next_page"}:
            direction = -1 if action_type == "previous_page" else 1
            if direction < 0:
                points = [(67, 24), (35, 45), (67, 66), (67, 54), (84, 54), (84, 36), (67, 36)]
            else:
                points = [(33, 24), (65, 45), (33, 66), (33, 54), (16, 54), (16, 36), (33, 36)]
            draw.polygon(points, fill=accent)
            text_box = (14, 70, 86, 93)
        elif action_type == "page_indicator":
            draw.rounded_rectangle((20, 26, 80, 61), radius=6, outline=accent, width=3)
            draw.line((29, 34, 71, 34), fill=outline, width=2)
            draw.ellipse((31, 45, 39, 53), fill=outline)
            draw.ellipse((46, 45, 54, 53), fill=outline)
            draw.ellipse((61, 45, 69, 53), fill=outline)
            text_box = (12, 66, 88, 93)
        elif action_type == "sleep":
            draw.ellipse((30, 23, 70, 63), fill=accent)
            draw.ellipse((42, 17, 76, 52), fill=(7, 22, 34))
            draw.line((24, 70, 76, 70), fill=outline, width=3)
            text_box = (12, 74, 88, 93)
        elif action_type == "mic_mute":
            mic_fill = (206, 57, 67) if muted else accent
            mic_outline = (255, 147, 153) if muted else outline
            draw.rounded_rectangle((37, 22, 63, 56), radius=13, fill=mic_fill, outline=mic_outline, width=2)
            draw.arc((25, 34, 75, 72), 0, 180, fill=mic_outline, width=4)
            draw.line((50, 72, 50, 80), fill=mic_outline, width=4)
            draw.line((38, 81, 62, 81), fill=mic_outline, width=4)
            if muted:
                draw.line((27, 25, 73, 71), fill=(255, 203, 207), width=4)
            text_box = (12, 82, 88, 98)
        elif action_type == "website":
            draw.ellipse((25, 24, 75, 68), outline=accent, width=4)
            draw.line((25, 46, 75, 46), fill=outline, width=2)
            draw.arc((39, 24, 61, 68), 90, 270, fill=outline, width=2)
            text_box = (12, 72, 88, 93)
        elif action_type in {"launch", "open_app", "codex"}:
            draw.rounded_rectangle((24, 26, 76, 66), radius=5, outline=accent, width=4)
            draw.line((35, 76, 65, 76), fill=outline, width=3)
            draw.line((50, 66, 50, 76), fill=outline, width=3)
            text_box = (12, 79, 88, 94)
        else:
            draw.rounded_rectangle((18, 28, 82, 64), radius=6, outline=accent, width=3)
            text_box = (12, 68, 88, 93)
    display_label = "" if action_type == "mic_mute" and label in {"", BUILTIN_ACTION_LABELS["mic_mute"]} else label or BUILTIN_ACTION_LABELS.get(action_type, "Action")
    if display_label:
        chosen_font, chosen_lines = _fit_font(draw, font_path, display_label, font_size, text_box)
        _draw_fitted_text(draw, chosen_font, chosen_lines, text_box, light)
    image.save(path, format="JPEG", quality=95, optimize=False)
    return path


def render_labeled_custom_image(source: Path, destination: Path, label: str, font_size: int, action_type: str) -> Path:
    """Preserve custom artwork while adding the configured LCD label."""

    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        raise D6Error("Pillow is required for LCD labels") from exc

    image = Image.open(source).convert("RGB").resize(ACTION_IMAGE_SIZE, Image.Resampling.LANCZOS)
    draw = ImageDraw.Draw(image)
    font_candidates = [
        Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "segoeuib.ttf",
        Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "arialbd.ttf",
    ]
    font_path = next((candidate for candidate in font_candidates if candidate.is_file()), None)
    text_box = (8, 68, 92, 98) if action_type == "open_folder" else (12, 72, 88, 94)
    chosen_font, chosen_lines = _fit_font(draw, font_path, label, font_size, text_box)
    _draw_fitted_text(draw, chosen_font, chosen_lines, text_box, (235, 246, 255), shadow=(3, 12, 28))
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination, format="JPEG", quality=95, optimize=False)
    return destination


def microphone_artwork_path(profile_dir: Path, muted: bool) -> Path | None:
    """Return the polished state artwork when it is installed with the profile."""

    candidate = profile_dir / "assets" / ("mic-muted.png" if muted else "mic.png")
    return candidate if candidate.is_file() else None


def _com_call(interface: ctypes.c_void_p, index: int, restype: Any, argtypes: list[Any], *args: Any) -> Any:
    vtable = ctypes.cast(interface, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    function = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vtable[index])
    return function(interface, *args)


def _com_release(interface: ctypes.c_void_p | None) -> None:
    if interface:
        _com_call(interface, 2, wintypes.ULONG, [],)


def _with_microphone_endpoint(callback: Any) -> Any:
    if os.name != "nt":
        raise D6Error("microphone mute is only supported on Windows")
    ole32 = ctypes.WinDLL("ole32", use_last_error=True)
    ole32.CoInitialize.argtypes = [ctypes.c_void_p]
    ole32.CoInitialize.restype = wintypes.LONG
    ole32.CoUninitialize.argtypes = []
    ole32.CoUninitialize.restype = None
    ole32.CoCreateInstance.argtypes = [ctypes.POINTER(_GUID), ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p)]
    ole32.CoCreateInstance.restype = wintypes.LONG
    ole32.CoInitialize(None)
    enumerator = ctypes.c_void_p()
    device = ctypes.c_void_p()
    volume = ctypes.c_void_p()
    try:
        result = ole32.CoCreateInstance(ctypes.byref(CLSID_MMDEVICE_ENUMERATOR), None, CLSCTX_INPROC_SERVER, ctypes.byref(IID_IMMDEVICE_ENUMERATOR), ctypes.byref(enumerator))
        if result < 0:
            raise D6Error(f"could not initialize the Windows audio device enumerator (HRESULT 0x{result & 0xFFFFFFFF:08X})")
        result = _com_call(enumerator, 4, wintypes.LONG, [ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)], E_CAPTURE, E_CONSOLE, ctypes.byref(device))
        if result < 0:
            raise D6Error(f"could not find the default microphone (HRESULT 0x{result & 0xFFFFFFFF:08X})")
        result = _com_call(device, 3, wintypes.LONG, [ctypes.POINTER(_GUID), wintypes.DWORD, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)], ctypes.byref(IID_IAUDIO_ENDPOINT_VOLUME), CLSCTX_INPROC_SERVER, None, ctypes.byref(volume))
        if result < 0:
            raise D6Error(f"could not access the default microphone volume endpoint (HRESULT 0x{result & 0xFFFFFFFF:08X})")
        return callback(volume)
    finally:
        _com_release(volume)
        _com_release(device)
        _com_release(enumerator)
        ole32.CoUninitialize()


def get_microphone_mute() -> bool:
    def read_state(volume: ctypes.c_void_p) -> bool:
        muted = wintypes.BOOL()
        result = _com_call(volume, 15, wintypes.LONG, [ctypes.POINTER(wintypes.BOOL)], ctypes.byref(muted))
        if result < 0:
            raise D6Error(f"could not read microphone mute state (HRESULT 0x{result & 0xFFFFFFFF:08X})")
        return bool(muted.value)

    return bool(_with_microphone_endpoint(read_state))


def toggle_microphone_mute() -> bool:
    def toggle_state(volume: ctypes.c_void_p) -> bool:
        muted = wintypes.BOOL()
        result = _com_call(volume, 15, wintypes.LONG, [ctypes.POINTER(wintypes.BOOL)], ctypes.byref(muted))
        if result < 0:
            raise D6Error(f"could not read microphone mute state (HRESULT 0x{result & 0xFFFFFFFF:08X})")
        next_state = not bool(muted.value)
        result = _com_call(volume, 14, wintypes.LONG, [wintypes.BOOL, ctypes.c_void_p], wintypes.BOOL(next_state), None)
        if result < 0:
            raise D6Error(f"could not change microphone mute state (HRESULT 0x{result & 0xFFFFFFFF:08X})")
        return next_state

    return bool(_with_microphone_endpoint(toggle_state))


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


def normalize_profile(profile: dict[str, Any]) -> dict[str, Any]:
    """Normalize legacy actions in memory without discarding unknown fields."""

    scenes = profile.get("scenes") if isinstance(profile, dict) else None
    if not isinstance(scenes, dict):
        return profile
    for scene_data in scenes.values():
        pages = scene_data.get("pages") if isinstance(scene_data, dict) else None
        if not isinstance(pages, dict):
            continue
        for page_data in pages.values():
            keys = page_data.get("keys") if isinstance(page_data, dict) else None
            if not isinstance(keys, dict):
                continue
            for definition in keys.values():
                if not isinstance(definition, dict) or not isinstance(definition.get("action"), dict):
                    continue
                action = definition["action"]
                if str(action.get("type") or "").lower() != "launch" or not str(action.get("focus_path") or "").strip():
                    continue
                command = str(action.get("command") or "").strip().lower()
                if command and not command.startswith("explorer"):
                    continue
                definition["action"] = {
                    "type": "open_folder",
                    "path": str(action.get("focus_path")).strip(),
                    **({"label": action["label"]} if action.get("label") else {}),
                    **({"font_size": action["font_size"]} if action.get("font_size") is not None else {}),
                }
    return profile


def load_profile(name: str, profile_dir: Path = PROFILE_DIR) -> dict[str, Any]:
    return normalize_profile(json.loads(profile_path(name, profile_dir).read_text(encoding="utf-8")))


def save_profile(name: str, profile: dict[str, Any], profile_dir: Path = PROFILE_DIR) -> None:
    profile_dir.mkdir(parents=True, exist_ok=True)
    target = profile_path(name, profile_dir)
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(normalize_profile(profile), indent=2) + "\n", encoding="utf-8")
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

    def __init__(self, profile_dir: Path = PROFILE_DIR, data_dir: Path = DATA_DIR):
        self.profile_dir = profile_dir
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.auth = AuthStore(self.data_dir / "auth.json")
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
        self.navigation_history: list[tuple[str, str, str]] = []
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

        def apply_target(target_profile_name: str, target_scene: str, target_page: str, *, record_history: bool = True) -> int:
            current = (profile_name, scene, page)
            target = (target_profile_name, target_scene, target_page)
            if record_history and target != current:
                self.navigation_history.append(current)
            target_profile = load_profile(target_profile_name, self.profile_dir)
            return self.apply(target_profile, target_scene, target_page, profile_name=target_profile_name)

        if action_type == "navigate":
            target_scene = str(action.get("scene") or scene)
            target_page = str(action.get("page") or page)
            applied = apply_target(profile_name, target_scene, target_page)
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
        elif action_type == "back":
            destination: tuple[str, str, str] | None = None
            while self.navigation_history:
                candidate = self.navigation_history.pop()
                try:
                    candidate_profile = load_profile(candidate[0], self.profile_dir)
                    resolve_page(candidate_profile, candidate[1], candidate[2])
                except (OSError, ValueError, json.JSONDecodeError):
                    continue
                destination = candidate
                break
            if destination is None:
                destination = (profile_name, scene, "main" if "main" in page_names(profile, scene) else page_names(profile, scene)[0])
            applied = apply_target(*destination, record_history=False)
            self.publish_event({"type": "action", "key": key, "action": "back", "scene": destination[1], "page": destination[2], "count": applied, "timestamp": time.time()})
        elif action_type == "home":
            home_scene = "default" if "default" in scene_names(profile) else scene_names(profile)[0]
            home_pages = page_names(profile, home_scene)
            home_page = "main" if "main" in home_pages else home_pages[0]
            applied = apply_target(profile_name, home_scene, home_page)
            self.publish_event({"type": "action", "key": key, "action": "home", "scene": home_scene, "page": home_page, "count": applied, "timestamp": time.time()})
        elif action_type in {"previous_page", "next_page"}:
            pages = page_names(profile, scene)
            current_index = pages.index(page) if page in pages else 0
            delta = -1 if action_type == "previous_page" else 1
            target_page = pages[(current_index + delta) % len(pages)]
            applied = apply_target(profile_name, scene, target_page)
            self.publish_event({"type": "action", "key": key, "action": action_type, "scene": scene, "page": target_page, "count": applied, "timestamp": time.time()})
        elif action_type == "page_indicator":
            self.publish_event({"type": "action", "key": key, "action": "page_indicator", "scene": scene, "page": page, "timestamp": time.time()})
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
        elif action_type == "website":
            with self.operation_lock:
                focused = open_website(str(action.get("url") or ""))
            self.publish_event({"type": "action", "key": key, "action": "website", "label": action_label(action), "focused": focused, "timestamp": time.time()})
        elif action_type == "sleep":
            controller = self._current_controller()
            if controller is None:
                raise D6Error("D6 is not connected")
            with self.operation_lock:
                controller.sleep_screen()
            self.publish_event({"type": "action", "key": key, "action": "sleep", "timestamp": time.time()})
        elif action_type == "mic_mute":
            with self.operation_lock:
                muted = toggle_microphone_mute()
                applied = self.apply(profile, scene, page, profile_name=profile_name)
            self.publish_event({"type": "action", "key": key, "action": "mic_mute", "muted": muted, "count": applied, "timestamp": time.time()})
        elif action_type == "open_folder":
            folder = str(action.get("path") or "").strip()
            if not folder:
                raise ValueError("open folder action has no folder path")
            focused = focus_explorer_path(folder, timeout=4.0)
            self.publish_event({"type": "action", "key": key, "action": "open_folder", "label": action_label(action), "focused": focused, "timestamp": time.time()})
        elif action_type == "launch":
            command = str(action.get("command") or "").strip()
            focus_path = str(action.get("focus_path") or "").strip()
            if not command and not focus_path:
                raise ValueError("launch action has no command")
            if focus_path:
                focused = focus_explorer_path(focus_path, command or None)
            else:
                configured_process = str(action.get("process_name") or "").strip()
                first_token = command.split(maxsplit=1)[0].strip('"') if command else ""
                inferred_process = Path(first_token).name if first_token.lower().endswith((".exe", ".com")) else None
                result = launch_target(command, process_name=configured_process or inferred_process)
                focused = result["focused"]
            self.publish_event(
                {
                    "type": "action",
                    "key": key,
                    "action": "launch",
                    "label": action_label(action),
                    "focused": focused,
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
        available_pages = page_names(profile, selected_scene)
        selected_page_index = available_pages.index(selected_page) if selected_page in available_pages else 0
        with self.operation_lock:
            # A page describes the complete 15-key layout. Clear artwork left
            # by the previous page so empty keys do not retain stale images.
            controller.clear_screen()
            if brightness is not None:
                controller.set_brightness(int(brightness))
            count = 0
            if isinstance(keys, dict):
                microphone_muted: bool | None = None
                for raw_key, definition in sorted(keys.items(), key=lambda item: int(item[0])):
                    if not isinstance(definition, dict):
                        continue
                    action = definition.get("action") if isinstance(definition.get("action"), dict) else None
                    action_type = str(action.get("type") or "").lower() if action else ""
                    if action and action_type == "open_folder" and definition.get("image") and action.get("label"):
                        source_path = (self.profile_dir / str(definition["image"])).resolve()
                        if self.profile_dir not in source_path.parents or not source_path.is_file():
                            raise FileNotFoundError(f"custom folder artwork not found: {definition['image']}")
                        image_path = render_labeled_custom_image(
                            source_path,
                            _action_image_path(
                                self.profile_dir,
                                profile_name or "profile",
                                selected_scene,
                                selected_page,
                                int(raw_key),
                                {**action, "_custom_image": str(definition["image"])},
                                page_index=selected_page_index,
                                page_total=len(available_pages),
                            ),
                            action_label(action, page_index=selected_page_index, page_total=len(available_pages)),
                            action_font_size(action),
                            action_type,
                        )
                    elif action and action_type in RENDERED_ACTION_TYPES and not definition.get("image"):
                        if action_type == "mic_mute" and microphone_muted is None:
                            microphone_muted = get_microphone_mute()
                        mic_artwork = microphone_artwork_path(self.profile_dir, bool(microphone_muted)) if action_type == "mic_mute" else None
                        image_path = mic_artwork or render_action_image(
                            _action_image_path(
                                self.profile_dir,
                                profile_name or "profile",
                                selected_scene,
                                selected_page,
                                int(raw_key),
                                action,
                                page_index=selected_page_index,
                                page_total=len(available_pages),
                            ),
                            action_label(action, page_index=selected_page_index, page_total=len(available_pages)),
                            action_font_size(action),
                            action_type,
                            muted=bool(microphone_muted),
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

    def export_config(self) -> bytes:
        """Create a portable profile-and-artwork archive without auth state."""

        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "manifest.json",
                json.dumps(
                    {
                        "format": "d6config",
                        "schema_version": CONFIG_SCHEMA_VERSION,
                        "created_at": int(time.time()),
                        "application": "RidgePath D6 Controller",
                    },
                    indent=2,
                ),
            )
            for profile_path in sorted(self.profile_dir.glob("*.json")):
                archive.writestr(f"profiles/{profile_path.name}", json.dumps(load_profile(profile_path.stem, self.profile_dir), indent=2))
            assets = self.profile_dir / "assets"
            if assets.is_dir():
                for asset in sorted(path for path in assets.rglob("*") if path.is_file()):
                    archive.write(asset, f"assets/{asset.relative_to(assets).as_posix()}")
        return stream.getvalue()

    def restore_config(self, payload: bytes) -> list[str]:
        """Validate and restore a .d6config archive transactionally."""

        if len(payload) > MAX_BACKUP_SIZE:
            raise ValueError("configuration backup is too large")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=self.data_dir) as temporary:
            stage = Path(temporary)
            staged_profiles = stage / "profiles"
            staged_assets = stage / "assets"
            staged_profiles.mkdir()
            staged_assets.mkdir()
            try:
                with zipfile.ZipFile(io.BytesIO(payload), "r") as archive:
                    infos = archive.infolist()
                    if sum(info.file_size for info in infos) > MAX_BACKUP_SIZE:
                        raise ValueError("configuration backup expands beyond the size limit")
                    names = {info.filename for info in infos}
                    if "manifest.json" not in names:
                        raise ValueError("configuration backup is missing its manifest")
                    manifest = json.loads(archive.read("manifest.json"))
                    if manifest.get("format") != "d6config" or int(manifest.get("schema_version", -1)) != CONFIG_SCHEMA_VERSION:
                        raise ValueError("unsupported configuration backup format")
                    profile_names: list[str] = []
                    for info in infos:
                        name = info.filename.replace("\\", "/")
                        if name.endswith("/") or name == "manifest.json":
                            continue
                        parts = name.split("/")
                        if any(part in {"", ".", ".."} for part in parts) or name.startswith("/"):
                            raise ValueError("configuration backup contains an unsafe path")
                        if parts[0] == "profiles" and len(parts) == 2 and parts[1].endswith(".json"):
                            profile_name = parts[1][:-5]
                            if not valid_name(profile_name):
                                raise ValueError("configuration backup contains an invalid profile name")
                            profile = json.loads(archive.read(info))
                            if not isinstance(profile, dict):
                                raise ValueError("profile backup must contain an object")
                            json.dumps(profile)
                            (staged_profiles / parts[1]).write_text(json.dumps(profile, indent=2) + "\n", encoding="utf-8")
                            profile_names.append(profile_name)
                        elif parts[0] == "assets" and len(parts) >= 2:
                            target = staged_assets.joinpath(*parts[1:]).resolve()
                            if staged_assets.resolve() not in target.parents:
                                raise ValueError("configuration backup contains an unsafe asset path")
                            target.parent.mkdir(parents=True, exist_ok=True)
                            target.write_bytes(archive.read(info))
                        else:
                            raise ValueError("configuration backup contains an unexpected file")
            except zipfile.BadZipFile as exc:
                raise ValueError("configuration backup is not a valid archive") from exc
            if not profile_names:
                raise ValueError("configuration backup contains no profiles")
            for profile_name in profile_names:
                load_profile(profile_name, staged_profiles)

            rollback = stage / "rollback"
            rollback_profiles = rollback / "profiles"
            rollback_assets = rollback / "assets"
            rollback_profiles.mkdir(parents=True)
            for existing in self.profile_dir.glob("*.json"):
                shutil.copy2(existing, rollback_profiles / existing.name)
            if (self.profile_dir / "assets").is_dir():
                shutil.copytree(self.profile_dir / "assets", rollback_assets)
            try:
                for existing in self.profile_dir.glob("*.json"):
                    existing.unlink()
                for staged in staged_profiles.glob("*.json"):
                    shutil.copy2(staged, self.profile_dir / staged.name)
                current_assets = self.profile_dir / "assets"
                if current_assets.exists():
                    shutil.rmtree(current_assets)
                shutil.copytree(staged_assets, current_assets)
            except Exception:
                for existing in self.profile_dir.glob("*.json"):
                    existing.unlink()
                for old in rollback_profiles.glob("*.json"):
                    shutil.copy2(old, self.profile_dir / old.name)
                current_assets = self.profile_dir / "assets"
                if current_assets.exists():
                    shutil.rmtree(current_assets)
                if rollback_assets.exists():
                    shutil.copytree(rollback_assets, current_assets)
                raise
            return sorted(profile_names)


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
        origin = self.headers.get("Origin")
        if self._origin_allowed(origin):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Credentials", "true")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "DELETE, GET, POST, PUT, OPTIONS")
        pending_cookie = getattr(self, "pending_cookie", None)
        if pending_cookie:
            self.send_header("Set-Cookie", pending_cookie)
            self.pending_cookie = None
        super().end_headers()

    def do_OPTIONS(self) -> None:
        try:
            self._guard_local_request()
            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()
        except RequestDenied as exc:
            json_response(self, {"error": str(exc)}, 403)

    def _body(self, max_size: int = MAX_BODY_SIZE) -> bytes:
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("invalid content length") from exc
        if size > max_size:
            raise ValueError("request body is too large")
        return self.rfile.read(size)

    def _json_body(self) -> dict[str, Any]:
        value = json.loads(self._body() or b"{}")
        if not isinstance(value, dict):
            raise ValueError("request body must be a JSON object")
        return value

    def _segments(self) -> list[str]:
        return [unquote(segment) for segment in urlparse(self.path).path.split("/") if segment]

    def _guard_local_request(self) -> None:
        host = (self.headers.get("Host") or "").split(":", 1)[0].lower()
        if host not in {"127.0.0.1", "localhost", "::1"}:
            raise RequestDenied("requests must use the localhost service")
        origin = self.headers.get("Origin")
        if origin and not self._origin_allowed(origin):
            raise RequestDenied("request origin is not allowed")

    @staticmethod
    def _origin_allowed(origin: str | None) -> bool:
        if not origin:
            return False
        parsed = urlparse(origin)
        return parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}

    def _session_token(self) -> str | None:
        from http.cookies import SimpleCookie

        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        morsel = cookie.get(SESSION_COOKIE)
        return morsel.value if morsel else None

    def _set_session_cookie(self, token: str, *, clear: bool = False) -> None:
        max_age = 0 if clear else AuthStore.SESSION_TTL
        value = "" if clear else token
        self.pending_cookie = f"{SESSION_COOKIE}={value}; Path=/; HttpOnly; SameSite=Strict; Max-Age={max_age}"

    def _require_auth(self) -> None:
        self._guard_local_request()
        if not self.service.auth.session_valid(self._session_token()):
            raise PermissionError("authentication required")

    def do_GET(self) -> None:
        try:
            parsed = urlparse(self.path)
            segments = [unquote(segment) for segment in parsed.path.split("/") if segment]
            if segments and segments[0] == "api":
                self._api_get(segments)
                return
            self._static_get(parsed.path)
        except RequestDenied as exc:
            json_response(self, {"error": str(exc)}, 403)
        except PermissionError as exc:
            json_response(self, {"error": str(exc)}, 401)
        except FileNotFoundError:
            json_response(self, {"error": "not found"}, 404)
        except Exception as exc:
            json_response(self, {"error": str(exc)}, 500)

    def _api_get(self, segments: list[str]) -> None:
        self._guard_local_request()
        if segments == ["api", "auth", "status"]:
            json_response(self, self.service.auth.status(self._session_token()))
            return
        self._require_auth()
        if segments == ["api", "state"]:
            profiles = sorted(path.stem for path in self.profile_dir.glob("*.json"))
            json_response(self, {"device": self.service.device_state(), "profiles": profiles})
            return
        if segments == ["api", "backup", "export"]:
            body = self.service.export_config()
            filename = f"d6-controller-{time.strftime('%Y-%m-%d')}.d6config"
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
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
        if len(segments) == 10 and segments[:2] == ["api", "profiles"] and segments[-1] == "preview":
            self._send_action_preview(segments[2], segments[4], segments[6], segments[8])
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

    def _send_action_preview(self, name: str, scene: str, page: str, key: str) -> None:
        profile = load_profile(name, self.profile_dir)
        selected, selected_scene, selected_page = resolve_page(profile, scene, page)
        definition = key_definitions(profile, scene, page).get(str(int(key)), {})
        action = definition.get("action") if isinstance(definition, dict) else None
        if not isinstance(action, dict):
            raise FileNotFoundError
        pages = page_names(profile, selected_scene)
        page_index = pages.index(selected_page) if selected_page in pages else 0
        action_type = str(action.get("type") or "label")
        label = action_label(action, page_index=page_index, page_total=len(pages))
        if definition.get("image") and action_type == "open_folder" and action.get("label"):
            source_path = (self.profile_dir / str(definition["image"])).resolve()
            if self.profile_dir not in source_path.parents or not source_path.is_file():
                raise FileNotFoundError
            image_path = render_labeled_custom_image(
                source_path,
                _action_image_path(self.profile_dir, name, selected_scene, selected_page, int(key), {**action, "_custom_image": str(definition["image"])}, page_index=page_index, page_total=len(pages)),
                label,
                action_font_size(action),
                action_type,
            )
        elif definition.get("image"):
            raise FileNotFoundError
        else:
            muted = get_microphone_mute() if action_type == "mic_mute" else False
            mic_artwork = microphone_artwork_path(self.profile_dir, muted) if action_type == "mic_mute" else None
            image_path = mic_artwork or render_action_image(
                _action_image_path(self.profile_dir, name, selected_scene, selected_page, int(key), action, page_index=page_index, page_total=len(pages)),
                label,
                action_font_size(action),
                action_type,
                muted=muted,
            )
        body = image_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(image_path.name)[0] or "application/octet-stream")
        self.send_header("Cache-Control", "no-store")
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
            self._require_auth()
            segments = self._segments()
            if len(segments) == 3 and segments[:2] == ["api", "profiles"]:
                profile = self._json_body()
                save_profile(segments[2], profile, self.profile_dir)
                json_response(self, profile)
                return
            raise FileNotFoundError
        except RequestDenied as exc:
            json_response(self, {"error": str(exc)}, 403)
        except PermissionError as exc:
            json_response(self, {"error": str(exc)}, 401)
        except FileNotFoundError:
            json_response(self, {"error": "not found"}, 404)
        except ValueError as exc:
            json_response(self, {"error": str(exc)}, 400)
        except Exception as exc:
            json_response(self, {"error": str(exc)}, 500)

    def do_POST(self) -> None:
        try:
            segments = self._segments()
            if segments in (["api", "auth", "setup"], ["api", "auth", "login"], ["api", "auth", "change-password"], ["api", "auth", "logout"]):
                self._guard_local_request()
                data = self._json_body()
                if segments[-1] == "setup":
                    token = self.service.auth.setup(str(data.get("password") or ""), str(data.get("confirmation") or ""))
                    self._set_session_cookie(token)
                    json_response(self, {"authenticated": True})
                    return
                if segments[-1] == "login":
                    token = self.service.auth.login(str(data.get("password") or ""), self.client_address[0])
                    self._set_session_cookie(token)
                    json_response(self, {"authenticated": True})
                    return
                if segments[-1] == "logout":
                    self.service.auth.logout(self._session_token())
                    self._set_session_cookie("", clear=True)
                    json_response(self, {"authenticated": False})
                    return
                self._require_auth()
                token = self.service.auth.change_password(self._session_token() or "", str(data.get("password") or ""), str(data.get("confirmation") or ""))
                self._set_session_cookie(token)
                json_response(self, {"authenticated": True})
                return
            self._require_auth()
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
            if segments == ["api", "dialog", "folder"]:
                selected = select_folder_native()
                json_response(self, {"selected": bool(selected), "path": selected})
                return
            if segments == ["api", "backup", "restore"]:
                restored = self.service.restore_config(self._body(MAX_BACKUP_SIZE))
                json_response(self, {"profiles": restored})
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
        except RequestDenied as exc:
            json_response(self, {"error": str(exc)}, 403)
        except PermissionError as exc:
            json_response(self, {"error": str(exc)}, 401)
        except AuthError as exc:
            json_response(self, {"error": str(exc)}, 400)
        except FileNotFoundError:
            json_response(self, {"error": "not found"}, 404)
        except (ValueError, KeyError) as exc:
            json_response(self, {"error": str(exc)}, 400)
        except Exception as exc:
            json_response(self, {"error": str(exc)}, 500)

    def do_DELETE(self) -> None:
        try:
            self._require_auth()
            segments = self._segments()
            if len(segments) == 10 and segments[:2] == ["api", "profiles"] and segments[-1] == "image":
                self._delete_key_image(segments[2], segments[4], segments[6], segments[8])
                return
            if len(segments) == 9 and segments[:2] == ["api", "profiles"] and segments[7] == "keys":
                self._delete_key(segments[2], segments[4], segments[6], segments[8])
                return
            raise FileNotFoundError
        except RequestDenied as exc:
            json_response(self, {"error": str(exc)}, 403)
        except PermissionError as exc:
            json_response(self, {"error": str(exc)}, 401)
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

    def _delete_key_image(self, name: str, scene: str, page: str, key: str) -> None:
        raw_key = int(key)
        if not 1 <= raw_key <= 15:
            raise ValueError("key must be between 1 and 15")
        profile = load_profile(name, self.profile_dir)
        selected, _, _ = resolve_page(profile, scene, page)
        keys = selected.setdefault("keys", {})
        definition = keys.get(str(raw_key)) if isinstance(keys, dict) else None
        image_name = definition.get("image") if isinstance(definition, dict) else None
        if image_name:
            image_path = (self.profile_dir / image_name).resolve()
            if self.profile_dir not in image_path.parents:
                raise ValueError("profile image is outside the profile directory")
            assets_root = (self.profile_dir / "assets").resolve()
            if image_path.is_file() and assets_root in image_path.parents:
                image_path.unlink()
            definition.pop("image", None)
            if not definition.get("action"):
                keys.pop(str(raw_key), None)
            save_profile(name, profile, self.profile_dir)
        json_response(self, {"profile": profile})

    def _delete_key(self, name: str, scene: str, page: str, key: str) -> None:
        raw_key = int(key)
        if not 1 <= raw_key <= 15:
            raise ValueError("key must be between 1 and 15")
        profile = load_profile(name, self.profile_dir)
        selected, _, _ = resolve_page(profile, scene, page)
        keys = selected.setdefault("keys", {})
        if isinstance(keys, dict):
            definition = keys.get(str(raw_key))
            image_name = definition.get("image") if isinstance(definition, dict) else None
            if image_name:
                image_path = (self.profile_dir / image_name).resolve()
                if self.profile_dir not in image_path.parents:
                    raise ValueError("profile image is outside the profile directory")
                assets_root = (self.profile_dir / "assets").resolve()
                if image_path.is_file() and assets_root in image_path.parents:
                    image_path.unlink()
            keys.pop(str(raw_key), None)
        save_profile(name, profile, self.profile_dir)
        json_response(self, {"profile": profile})


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
