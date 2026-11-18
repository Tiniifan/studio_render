"""Settings kept between sessions in studio_render.ini, next to main.py (next to studio_render.exe once built)."""
import configparser
import os
import sys

# A built studio_render keeps its modules in _internal: the settings stay next to the exe the user sees
FOLDER = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(FOLDER, "studio_render.ini")

SCREENSHOT_FILE = "file"
SCREENSHOT_CLIPBOARD = "clipboard"

DEFAULTS = {
    ("view", "assets"): True,
    ("view", "timeline"): False,
    ("languages", "squirrel"): True,
    ("screenshot", "destination"): SCREENSHOT_FILE,
    ("home", "path"): "",
    ("home", "enabled"): True,
    ("engine", "default"): "",          # chosen at the first start, empty until then
}


class Preferences:
    def __init__(self, path=PATH):
        self.path = path
        self.config = configparser.ConfigParser()
        try:
            self.config.read(path, encoding="utf-8")
        except (OSError, configparser.Error) as error:
            print(f"[studio_render] {path}: {error}")
        if not os.path.exists(path):
            self._write_defaults()

    def _write_defaults(self):
        """First start: the file is made with every setting at its default, so it can be edited by hand."""
        for (section, key), value in DEFAULTS.items():
            if not self.config.has_option(section, key):
                if not self.config.has_section(section):
                    self.config.add_section(section)
                self.config.set(section, key, ("true" if value else "false") if isinstance(value, bool) else str(value))
        self._save()

    def _get(self, section, key):
        default = DEFAULTS[(section, key)]
        if not self.config.has_option(section, key):
            return default
        if isinstance(default, bool):
            try:
                return self.config.getboolean(section, key)
            except ValueError:
                return default
        return self.config.get(section, key)

    def _set(self, section, key, value):
        if not self.config.has_section(section):
            self.config.add_section(section)
        self.config.set(section, key, ("true" if value else "false") if isinstance(value, bool) else str(value))
        self._save()

    def _save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as stream:
                self.config.write(stream)
        except OSError as error:
            print(f"[studio_render] {self.path}: {error}")

    @property
    def show_assets(self):
        return self._get("view", "assets")

    @show_assets.setter
    def show_assets(self, value):
        self._set("view", "assets", bool(value))

    @property
    def show_timeline(self):
        return self._get("view", "timeline")

    @show_timeline.setter
    def show_timeline(self, value):
        self._set("view", "timeline", bool(value))

    @property
    def squirrel(self):
        return self._get("languages", "squirrel")

    @squirrel.setter
    def squirrel(self, value):
        self._set("languages", "squirrel", bool(value))

    @property
    def screenshot(self):
        value = self._get("screenshot", "destination")
        return value if value in (SCREENSHOT_FILE, SCREENSHOT_CLIPBOARD) else SCREENSHOT_FILE

    @screenshot.setter
    def screenshot(self, value):
        self._set("screenshot", "destination", value)

    @property
    def engine(self):
        """Preferred engine: the one of a new project. Empty until it is chosen (first start)."""
        return self._get("engine", "default")

    @engine.setter
    def engine(self, value):
        self._set("engine", "default", value or "")

    @property
    def home_project(self):
        return self._get("home", "path")

    @home_project.setter
    def home_project(self, value):
        self._set("home", "path", value or "")

    @property
    def home_enabled(self):
        return self._get("home", "enabled")

    @home_enabled.setter
    def home_enabled(self, value):
        self._set("home", "enabled", bool(value))
