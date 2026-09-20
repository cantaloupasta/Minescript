#!/usr/bin/env python3
"""
radar.py - all-in-one Minescript radar tool with Tkinter GUI, live compiled frame syncing
"""

import json
import os
import tkinter as tk
from tkinter import ttk, colorchooser, messagebox

from system.lib.java import eval_pyjinn_script as eps

# Paths
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MOB_DB_PATH = os.path.join(SCRIPT_DIR, "mob_db.json")
RADAR_CFG_PATH = os.path.join(SCRIPT_DIR, "radar_config.json")


def load_json_file(path, default=None):
    if default is None:
        default = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return default


class RadarConfigApp(tk.Tk):
    def __init__(self, mob_db):
        super().__init__()
        self.title("Radar Config Builder")
        self.geometry("600x650")
        self.resizable(False, False)

        self.mob_db = mob_db
        self.mob_names = sorted(mob_db.keys())
        self.mob_names_lower = {name.lower(): name for name in mob_db}
        self.entries = []
        self.selected_color = (0, 240, 255)
        self.editing_index = None

        saved_config = load_json_file(RADAR_CFG_PATH, [])
        if isinstance(saved_config, list):
            for item in saved_config:
                if isinstance(item, dict):
                    item.setdefault("enabled", True)
                    self.entries.append(item)

        self._build_ui()
        self._refresh_list_ui()
        self._initialize_radar_script()

    def _build_ui(self):
        pad = {"padx": 8, "pady": 4}

        # Mob selection frame
        mob_frame = ttk.Frame(self)
        mob_frame.pack(fill="x", **pad)
        ttk.Label(mob_frame, text="Mob type:").pack(side="left")
        self.mob_type_var = tk.StringVar()
        self.mob_combo = ttk.Combobox(mob_frame, textvariable=self.mob_type_var,
                                       values=self.mob_names, width=28)
        self.mob_combo.pack(side="left", padx=6)
        self.mob_combo.bind("<KeyRelease>", self._filter_mob_list)

        # Display name frame
        name_frame = ttk.Frame(self)
        name_frame.pack(fill="x", **pad)
        ttk.Label(name_frame, text="Display name:").pack(side="left")
        self.name_var = tk.StringVar()
        ttk.Entry(name_frame, textvariable=self.name_var, width=28).pack(side="left", padx=6)

        # Color picker frame
        color_frame = ttk.Frame(self)
        color_frame.pack(fill="x", **pad)
        ttk.Label(color_frame, text="Highlight color:").pack(side="left")
        self.color_swatch = tk.Canvas(color_frame, width=28, height=18,
                                       bg=self._rgb_to_hex(self.selected_color),
                                       highlightthickness=1, highlightbackground="black")
        self.color_swatch.pack(side="left", padx=6)
        ttk.Button(color_frame, text="Pick color...", command=self._pick_color).pack(side="left")

        # Action buttons frame
        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill="x", **pad)
        
        self.action_btn = ttk.Button(btn_frame, text="Add Entity", command=self._save_entry)
        self.action_btn.pack(side="left")

        self.cancel_edit_btn = ttk.Button(btn_frame, text="Cancel Edit", command=self._cancel_edit)

        ttk.Separator(self).pack(fill="x", pady=6)

        ttk.Label(self, text="Entities to watch (Toggle, Edit, or Delete):").pack(anchor="w", padx=8)

        # Scrollable container for entries
        container_outer = ttk.Frame(self)
        container_outer.pack(fill="both", expand=True, padx=8, pady=4)

        self.canvas = tk.Canvas(container_outer, borderwidth=0, highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(container_outer, orient="vertical", command=self.canvas.yview)
        self.scrollable_frame = ttk.Frame(self.canvas)

        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )
        self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")

        # Start / Update Radar button frame
        start_frame = ttk.Frame(self)
        start_frame.pack(fill="x", **pad)
        ttk.Button(start_frame, text="Start / Update Radar", command=self._initialize_radar_script).pack(side="right", padx=4)

        # Status text label
        self.status_var = tk.StringVar(
            value=f"Loaded {len(self.mob_names)} mob types. Ready." if self.mob_names else "Could not load mob_db.json."
        )
        ttk.Label(self, textvariable=self.status_var, foreground="blue").pack(anchor="w", padx=8, pady=2)

    def _filter_mob_list(self, _event=None):
        typed = self.mob_type_var.get().lower()
        filtered = [m for m in self.mob_names if typed in m.lower()] if typed else self.mob_names
        self.mob_combo["values"] = filtered

    def _pick_color(self):
        rgb, _hexval = colorchooser.askcolor(color=self._rgb_to_hex(self.selected_color))
        if rgb:
            self.selected_color = tuple(int(c) for c in rgb)
            self.color_swatch.config(bg=self._rgb_to_hex(self.selected_color))

    @staticmethod
    def _rgb_to_hex(rgb):
        return "#{:02x}{:02x}{:02x}".format(*rgb)

    def _save_entry(self):
        typed_mob_type = self.mob_type_var.get().strip()
        name = self.name_var.get().strip()

        mob_type = self.mob_names_lower.get(typed_mob_type.lower())

        if mob_type is None:
            messagebox.showerror("Invalid mob type", f"'{typed_mob_type}' was not found in mob_db.json.")
            return
        if not name:
            messagebox.showerror("Missing name", "Please choose a display name for this entry.")
            return

        for idx, entry in enumerate(self.entries):
            if entry["mob_type"] == mob_type:
                if self.editing_index is None or self.editing_index != idx:
                    messagebox.showerror("Duplicate Entity", f"'{mob_type}' is already in your radar list.")
                    return

        if self.editing_index is None:
            entry = {
                "name": name,
                "mob_type": mob_type,
                "color": list(self.selected_color),
                "enabled": True
            }
            self.entries.append(entry)
        else:
            self.entries[self.editing_index]["mob_type"] = mob_type
            self.entries[self.editing_index]["name"] = name
            self.entries[self.editing_index]["color"] = list(self.selected_color)
            self.editing_index = None
            self.action_btn.config(text="Add Entity")
            self.cancel_edit_btn.pack_forget()

        self.mob_type_var.set("")
        self.name_var.set("")
        self._refresh_list_ui()
        self._write_config_to_disk()
        self._initialize_radar_script()

    def _start_edit(self, index):
        if 0 <= index < len(self.entries):
            self.editing_index = index
            entry = self.entries[index]
            
            self.mob_type_var.set(entry["mob_type"])
            self.name_var.set(entry["name"])
            self.selected_color = tuple(entry["color"])
            self.color_swatch.config(bg=self._rgb_to_hex(self.selected_color))
            
            self.action_btn.config(text="Update Entity")
            self.cancel_edit_btn.pack(side="left", padx=6)

    def _cancel_edit(self):
        self.editing_index = None
        self.mob_type_var.set("")
        self.name_var.set("")
        self.action_btn.config(text="Add Entity")
        self.cancel_edit_btn.pack_forget()

    def _remove_entry(self, index):
        if 0 <= index < len(self.entries):
            if self.editing_index == index:
                self._cancel_edit()
            elif self.editing_index is not None and self.editing_index > index:
                self.editing_index -= 1

            del self.entries[index]
            self._refresh_list_ui()
            self._write_config_to_disk()
            self._initialize_radar_script()

    def _refresh_list_ui(self):
        for widget in self.scrollable_frame.winfo_children():
            widget.destroy()

        for idx, entry in enumerate(self.entries):
            row = ttk.Frame(self.scrollable_frame, padding=2)
            row.pack(fill="x", expand=True, anchor="w")

            enabled_var = tk.BooleanVar(value=entry.get("enabled", True))
            chk = ttk.Checkbutton(row, variable=enabled_var, 
                                  command=lambda i=idx, var=enabled_var: self._update_toggle(i, var))
            chk.pack(side="left", padx=2)

            hex_color = self._rgb_to_hex(entry.get("color", [255, 255, 255]))
            color_box = tk.Canvas(row, width=16, height=16, bg=hex_color, highlightthickness=1, highlightbackground="black")
            color_box.pack(side="left", padx=4)

            text_desc = f"{entry['name']} ({entry['mob_type']})"
            lbl = ttk.Label(row, width=30, text=text_desc, anchor="w")
            lbl.pack(side="left", padx=4)

            edit_btn = ttk.Button(row, text="Edit", width=6, command=lambda i=idx: self._start_edit(i))
            edit_btn.pack(side="right", padx=2)

            del_btn = ttk.Button(row, text="Delete", width=8, command=lambda i=idx: self._remove_entry(i))
            del_btn.pack(side="right", padx=2)

    def _update_toggle(self, index, var):
        if 0 <= index < len(self.entries):
            self.entries[index]["enabled"] = var.get()
            self._write_config_to_disk()
            self._initialize_radar_script()

    def _write_config_to_disk(self):
        try:
            f = open(RADAR_CFG_PATH, "w", encoding="utf-8")
            json.dump(self.entries, f, indent=2)
            f.close()
        except Exception as e:
            messagebox.showerror("Save failed", str(e))

    def _initialize_radar_script(self):
        self._write_config_to_disk()
        try:
            # Pyjinn has no `json` module and no `open()` builtin, so the embedded
            # script can't read/parse these files itself. Instead we embed the data
            # we already have in memory directly as Python/Pyjinn literals via repr().
            mob_db_literal = repr(self.mob_db)
            config_literal = repr(self.entries)

            script_code = (
                PYJINN_RADAR_TEMPLATE
                .replace("__MOB_DB_LITERAL__", mob_db_literal)
                .replace("__CONFIG_LITERAL__", config_literal)
            )
            # Stop any previously running radar script's render listener before
            # embedding a new one, otherwise old listeners pile up and keep drawing.
            if getattr(self, "_radar_script", None) is not None:
                try:
                    stop_fn = self._radar_script.get("stop_radar")
                    stop_fn()
                except Exception:
                    pass

            self._radar_script = eps(script_code)
            self.status_var.set("Status: Radar synced and running!")
        except Exception as e:
            messagebox.showerror("Radar Error", f"Failed to start radar: {e}")


# Embedded Pyjinn radar script template.
#
# NOTE: Pyjinn does not support the Python standard library (no `import json`)
# and does not support `open()` (see pyjinndocs.md: built-in function table,
# `open()` -> "no"). So instead of having this script read/parse the config
# and mob_db JSON files itself, the Python GUI embeds that data directly as
# Python/Pyjinn dict & list literals (via repr()) each time it rebuilds the
# script. That is why __MOB_DB_LITERAL__ / __CONFIG_LITERAL__ are substituted
# with actual literal text (e.g. `{'Zombie': [0.6, 1.95], ...}`) rather than
# file paths.
PYJINN_RADAR_TEMPLATE = r'''
from minescript import *

Gizmos     = JavaClass("net.minecraft.gizmos.Gizmos")
GizmoStyle = JavaClass("net.minecraft.gizmos.GizmoStyle")
AABB       = JavaClass("net.minecraft.world.phys.AABB")
ARGB       = JavaClass("net.minecraft.util.ARGB")

MOB_DB = __MOB_DB_LITERAL__
CONFIG = __CONFIG_LITERAL__

log("[RADAR DEBUG] Script (re)initialized.")
log("[RADAR DEBUG] MOB_DB has " + str(len(MOB_DB)) + " known mob types.")
log("[RADAR DEBUG] CONFIG has " + str(len(CONFIG)) + " watch entries.")
for _dbg_entry in CONFIG:
    log("[RADAR DEBUG]   entry: " + str(_dbg_entry))

_frame_count = 0

def on_render(event):
    global _frame_count
    _frame_count += 1
    # Only print the noisy per-frame debug info roughly once every 100 frames
    # (about every 1.5-3 seconds) so the log/chat aren't spammed every tick.
    debug = (_frame_count % 100 == 1)

    if debug:
        log("[RADAR DEBUG] on_render fired (frame " + str(_frame_count) + ")")

    if not MOB_DB:
        if debug:
            echo("§c[RADAR DEBUG] MOB_DB is empty - nothing can be matched")
        return
    if not CONFIG:
        if debug:
            echo("§c[RADAR DEBUG] CONFIG is empty - no entries to watch")
        return

    all_entities = entities()
    if debug:
        log("[RADAR DEBUG] entities() returned " + str(len(all_entities)) + " nearby entities")

    drawn = 0
    for entry in CONFIG:
        if not entry.get("enabled", True):
            if debug:
                log("[RADAR DEBUG] skipping disabled entry: " + str(entry.get("mob_type")))
            continue

        mob_type = entry.get("mob_type")
        color = entry.get("color", [255, 255, 255])

        dims = MOB_DB.get(mob_type)
        if dims is None:
            if debug:
                echo("§c[RADAR DEBUG] '" + str(mob_type) + "' not found in MOB_DB")
            continue

        hw = dims[0] / 2.0
        h = dims[1]

        r, g, b = int(color[0]), int(color[1]), int(color[2])
        stroke_color = ARGB.color(255, r, g, b)
        fill_color   = ARGB.color(50, r, g, b)
        style = GizmoStyle.strokeAndFill(stroke_color, JavaFloat(1.5), fill_color)

        match_str = mob_type.lower().replace(" ", "_")
        matched_this_entry = 0

        for entity in all_entities:
            try:
                if entity is None or entity.type is None or entity.position is None:
                    continue

                e_type = str(entity.type).lower()
                if match_str in e_type:
                    matched_this_entry += 1
                    x, y, z = entity.position
                    box = AABB(
                        JavaFloat(x - hw), JavaFloat(y), JavaFloat(z - hw),
                        JavaFloat(x + hw), JavaFloat(y + h), JavaFloat(z + hw),
                    )
                    Gizmos.cuboid(box, style).setAlwaysOnTop()
                    drawn += 1
            except:
                if debug:
                    log("[RADAR DEBUG] error while processing an entity for " + str(mob_type))
                continue

        if debug:
            log("[RADAR DEBUG] '" + str(mob_type) + "' matched " + str(matched_this_entry) + " nearby entities")

    if debug:
        log("[RADAR DEBUG] frame " + str(_frame_count) + ": drew " + str(drawn) + " gizmo box(es) total")

_radar_listener_id = add_event_listener("render", on_render)

def stop_radar():
    remove_event_listener(_radar_listener_id)
    log("[RADAR DEBUG] stop_radar() called, listener " + str(_radar_listener_id) + " removed.")

echo("§b§l[RADAR]§r Radar updated successfully! (" + str(len(CONFIG)) + " entries loaded, " + str(len(MOB_DB)) + " known mob types)")
'''


def main():
    mob_db = load_json_file(MOB_DB_PATH, {})
    app = RadarConfigApp(mob_db)
    app.mainloop()


if __name__ == "__main__":
    main()
