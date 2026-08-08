"""Small profile-oriented desktop UI for the direct FIFINE D6 controller."""

from __future__ import annotations

import json
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from d6_controller import D6Controller


class D6App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("D6 Controller")
        self.root.geometry("900x650")
        self.profile: dict = {}
        self.profile_path: Path | None = None
        self.preview_images: list[object] = []
        self.buttons: list[ttk.Button] = []
        self.events: queue.Queue[str] = queue.Queue()
        self.stop_listener = threading.Event()
        self.listener_thread: threading.Thread | None = None
        self.listener_controller: D6Controller | None = None
        self.scene = tk.StringVar()
        self.page = tk.StringVar()
        self.status = tk.StringVar(value="Load a profile, then apply a scene/page.")
        self.brightness = tk.IntVar(value=42)

        toolbar = ttk.Frame(root, padding=8)
        toolbar.pack(fill="x")
        ttk.Button(toolbar, text="Load profile…", command=self.load_profile).pack(side="left")
        ttk.Button(toolbar, text="Apply selected page", command=self.apply_page).pack(side="left", padx=6)
        ttk.Button(toolbar, text="Listen for events", command=self.toggle_listener).pack(side="left")
        ttk.Label(toolbar, text="Scene").pack(side="left", padx=(24, 4))
        self.scene_box = ttk.Combobox(toolbar, textvariable=self.scene, state="readonly", width=16)
        self.scene_box.pack(side="left")
        self.scene_box.bind("<<ComboboxSelected>>", lambda _event: self.refresh_pages())
        ttk.Label(toolbar, text="Page").pack(side="left", padx=(12, 4))
        self.page_box = ttk.Combobox(toolbar, textvariable=self.page, state="readonly", width=16)
        self.page_box.pack(side="left")

        device_bar = ttk.Frame(root, padding=(8, 0, 8, 8))
        device_bar.pack(fill="x")
        ttk.Label(device_bar, text="Brightness").pack(side="left")
        ttk.Scale(device_bar, from_=0, to=100, variable=self.brightness, orient="horizontal", length=180).pack(side="left", padx=8)
        ttk.Button(device_bar, text="Set brightness", command=self.set_brightness).pack(side="left")
        ttk.Label(device_bar, text="D6 RGB: unsupported on this connected unit").pack(side="right")

        body = ttk.Frame(root, padding=8)
        body.pack(fill="both", expand=True)
        grid = ttk.Frame(body)
        grid.pack(side="left", fill="both", expand=True)
        for key in range(1, 16):
            button = ttk.Button(grid, text=str(key), width=16)
            button.grid(row=(key - 1) // 5, column=(key - 1) % 5, padx=5, pady=5, sticky="nsew")
            self.buttons.append(button)
        for row in range(3):
            grid.rowconfigure(row, weight=1)
        for column in range(5):
            grid.columnconfigure(column, weight=1)

        events_frame = ttk.LabelFrame(body, text="Events", padding=6)
        events_frame.pack(side="right", fill="both", padx=(12, 0))
        self.event_text = tk.Text(events_frame, width=30, height=22, state="disabled")
        self.event_text.pack(fill="both", expand=True)
        ttk.Label(root, textvariable=self.status, relief="sunken", anchor="w").pack(fill="x", side="bottom")
        self.root.after(100, self.drain_events)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def load_profile(self) -> None:
        selected = filedialog.askopenfilename(filetypes=[("JSON profile", "*.json"), ("All files", "*.*")])
        if not selected:
            return
        try:
            path = Path(selected)
            self.profile = json.loads(path.read_text(encoding="utf-8"))
            self.profile_path = path
        except (OSError, ValueError) as exc:
            messagebox.showerror("Profile error", str(exc))
            return
        scenes = list(self.profile.get("scenes", {"default": {}})) or ["default"]
        self.scene_box["values"] = scenes
        self.scene.set(scenes[0])
        self.refresh_pages()
        self.status.set(f"Loaded {path.name}")

    def selected_page(self) -> dict:
        scenes = self.profile.get("scenes")
        if not scenes:
            return self.profile
        selected_scene = scenes.get(self.scene.get(), next(iter(scenes.values())))
        pages = selected_scene.get("pages")
        if not pages:
            return selected_scene
        return pages.get(self.page.get(), next(iter(pages.values())))

    def refresh_pages(self) -> None:
        scenes = self.profile.get("scenes", {})
        selected_scene = scenes.get(self.scene.get(), {})
        pages = selected_scene.get("pages", {})
        values = list(pages) or ["main"]
        self.page_box["values"] = values
        self.page.set(values[0])
        self.refresh_previews()

    def refresh_previews(self) -> None:
        self.preview_images.clear()
        definitions = self.selected_page().get("keys", self.selected_page())
        root = self.profile_path.parent if self.profile_path else Path.cwd()
        try:
            from PIL import Image, ImageOps, ImageTk
        except ImportError:
            Image = ImageOps = ImageTk = None
        for key, button in enumerate(self.buttons, start=1):
            definition = definitions.get(str(key), {}) if isinstance(definitions, dict) else {}
            image_path = root / definition["image"] if isinstance(definition, dict) and definition.get("image") else None
            if image_path and image_path.exists() and Image is not None:
                with Image.open(image_path) as source:
                    image = ImageOps.fit(source.convert("RGB"), (90, 90))
                photo = ImageTk.PhotoImage(image)
                self.preview_images.append(photo)
                button.configure(text=str(key), image=photo, compound="top")
            else:
                button.configure(text=str(key), image="", compound="none")

    def apply_page(self) -> None:
        if not self.profile_path:
            messagebox.showinfo("Profile", "Load a JSON profile first.")
            return
        profile = self.profile
        path = self.profile_path
        scene = self.scene.get() or None
        page = self.page.get() or None
        self.status.set("Applying images to the D6…")

        def worker() -> None:
            try:
                with D6Controller() as controller:
                    count = controller.apply_profile(profile, scene=scene, page=page, root=path.parent)
                self.events.put(f"Applied {count} image(s) from {scene}/{page}")
            except Exception as exc:  # surfaced in the UI status instead of killing the worker
                self.events.put(f"ERROR applying profile: {exc}")

        threading.Thread(target=worker, daemon=True).start()

    def set_brightness(self) -> None:
        value = int(round(self.brightness.get()))

        def worker() -> None:
            try:
                with D6Controller() as controller:
                    controller.set_brightness(value)
                self.events.put(f"Brightness set to {value}")
            except Exception as exc:
                self.events.put(f"ERROR setting brightness: {exc}")

        threading.Thread(target=worker, daemon=True).start()

    def toggle_listener(self) -> None:
        if self.listener_thread and self.listener_thread.is_alive():
            self.stop_listener.set()
            if self.listener_controller:
                self.listener_controller.close()
            self.status.set("Stopping event listener…")
            return
        self.stop_listener.clear()
        self.status.set("Listening for D6 button events…")

        def worker() -> None:
            try:
                with D6Controller() as controller:
                    self.listener_controller = controller
                    while not self.stop_listener.is_set():
                        report = controller.read_report(500)
                        if report:
                            event = controller.decode_key_report(report)
                            if event:
                                key, pressed = event
                                self.events.put(f"Key {key}: {'press' if pressed else 'release'}")
            except Exception as exc:
                self.events.put(f"ERROR listening: {exc}")
            finally:
                self.listener_controller = None
                self.events.put("Event listener stopped")

        self.listener_thread = threading.Thread(target=worker, daemon=True)
        self.listener_thread.start()

    def drain_events(self) -> None:
        try:
            while True:
                message = self.events.get_nowait()
                self.event_text.configure(state="normal")
                self.event_text.insert("end", message + "\n")
                self.event_text.see("end")
                self.event_text.configure(state="disabled")
                self.status.set(message)
        except queue.Empty:
            pass
        self.root.after(100, self.drain_events)

    def close(self) -> None:
        self.stop_listener.set()
        if self.listener_controller:
            self.listener_controller.close()
        self.root.destroy()


if __name__ == "__main__":
    app_root = tk.Tk()
    D6App(app_root)
    app_root.mainloop()
