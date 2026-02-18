"""
F1 Scoreboard Plugin

Main plugin class for the Formula 1 Scoreboard.
Displays driver standings, constructor standings, race results, qualifying,
practice, sprint results, upcoming races, and race calendar.
"""

import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from PIL import Image

from src.plugin_system.base_plugin import BasePlugin, VegasDisplayMode

from f1_data import F1DataSource
from f1_renderer import F1Renderer
from logo_downloader import F1LogoLoader
from scroll_display import ScrollDisplayManager
from team_colors import normalize_constructor_id

logger = logging.getLogger(__name__)


class F1ScoreboardPlugin(BasePlugin):
    """
    Formula 1 Scoreboard Plugin.

    Displays F1 standings, race results, qualifying breakdowns, practice
    standings, sprint results, upcoming races, and race calendar.
    Supports favorite driver/team highlighting and Vegas scroll mode.
    """

    def __init__(self, plugin_id, config, display_manager,
                 cache_manager, plugin_manager):
        super().__init__(plugin_id, config, display_manager,
                        cache_manager, plugin_manager)

        # Display dimensions
        self.display_width = display_manager.matrix.width
        self.display_height = display_manager.matrix.height

        # Favorites
        self.favorite_driver = config.get("favorite_driver", "").upper()
        self.favorite_team = normalize_constructor_id(
            config.get("favorite_team", ""))

        # Display duration
        self.display_duration = config.get("display_duration", 30)

        # Initialize components
        self.logo_loader = F1LogoLoader()
        self.data_source = F1DataSource(cache_manager, config)
        self.renderer = F1Renderer(
            self.display_width, self.display_height,
            config, self.logo_loader, self.logger)
        self._scroll_manager = ScrollDisplayManager(
            display_manager, config, self.logger)

        # Data state
        self._driver_standings: List[Dict] = []
        self._constructor_standings: List[Dict] = []
        self._recent_races: List[Dict] = []
        self._upcoming_race: Optional[Dict] = None
        self._qualifying: Optional[Dict] = None
        self._practice_results: Dict[str, Dict] = {}  # FP1/FP2/FP3
        self._sprint: Optional[Dict] = None
        self._calendar: List[Dict] = []
        self._pole_positions: Dict[str, int] = {}

        # Timing
        self._last_update = 0
        self._update_interval = config.get("update_interval", 3600)

        # Build enabled modes
        self.modes = self._build_enabled_modes()

        # Preload logos
        self.logo_loader.preload_all_teams(
            self.renderer.logo_max_height,
            self.renderer.logo_max_width)

        self.logger.info("F1 Scoreboard initialized with %d modes: %s",
                        len(self.modes), ", ".join(self.modes))

    def _build_enabled_modes(self) -> List[str]:
        """Build list of enabled display modes from config."""
        modes = []
        mode_configs = {
            "f1_driver_standings": self.config.get(
                "driver_standings", {}).get("enabled", True),
            "f1_constructor_standings": self.config.get(
                "constructor_standings", {}).get("enabled", True),
            "f1_recent_races": self.config.get(
                "recent_races", {}).get("enabled", True),
            "f1_upcoming": self.config.get(
                "upcoming", {}).get("enabled", True),
            "f1_qualifying": self.config.get(
                "qualifying", {}).get("enabled", True),
            "f1_practice": self.config.get(
                "practice", {}).get("enabled", True),
            "f1_sprint": self.config.get(
                "sprint", {}).get("enabled", True),
            "f1_calendar": self.config.get(
                "calendar", {}).get("enabled", True),
        }

        for mode, enabled in mode_configs.items():
            if enabled:
                modes.append(mode)

        return modes

    # ─── Update ────────────────────────────────────────────────────────

    def update(self):
        """Fetch and update all F1 data from APIs."""
        now = time.time()
        if now - self._last_update < self._update_interval:
            return

        self.logger.info("Updating F1 data...")
        self._last_update = now

        try:
            self._update_standings()
            self._update_recent_races()
            self._update_upcoming()
            self._update_qualifying()
            self._update_practice()
            self._update_sprint()
            self._update_calendar()
            self._prepare_scroll_content()
        except Exception as e:
            self.logger.error("Error updating F1 data: %s", e, exc_info=True)

    def _update_standings(self):
        """Update driver and constructor standings."""
        # Driver standings
        if "f1_driver_standings" in self.modes:
            standings = self.data_source.fetch_driver_standings()
            if standings:
                # Calculate poles
                self._pole_positions = (
                    self.data_source.calculate_pole_positions())

                # Shallow copy entries before adding poles to avoid
                # mutating the cached standings dicts
                standings = [dict(e) for e in standings]
                for entry in standings:
                    code = entry.get("code", "")
                    entry["poles"] = self._pole_positions.get(code, 0)

                # Apply favorite filter
                top_n = self.config.get(
                    "driver_standings", {}).get("top_n", 10)
                always_show = self.config.get(
                    "driver_standings", {}).get("always_show_favorite", True)

                self._driver_standings = self.data_source.apply_favorite_filter(
                    standings, top_n,
                    favorite_driver=self.favorite_driver,
                    favorite_team=self.favorite_team,
                    always_show_favorite=always_show)

        # Constructor standings
        if "f1_constructor_standings" in self.modes:
            standings = self.data_source.fetch_constructor_standings()
            if standings:
                top_n = self.config.get(
                    "constructor_standings", {}).get("top_n", 10)
                always_show = self.config.get(
                    "constructor_standings", {}).get(
                        "always_show_favorite", True)

                self._constructor_standings = (
                    self.data_source.apply_favorite_filter(
                        standings, top_n,
                        favorite_team=self.favorite_team,
                        always_show_favorite=always_show,
                        driver_key="constructor_id",
                        team_key="constructor_id"))

    def _update_recent_races(self):
        """Update recent race results."""
        if "f1_recent_races" not in self.modes:
            return

        count = self.config.get("recent_races", {}).get("number_of_races", 3)
        races = self.data_source.fetch_recent_races(count=count)
        if races:
            top_finishers = self.config.get(
                "recent_races", {}).get("top_finishers", 3)
            always_show = self.config.get(
                "recent_races", {}).get("always_show_favorite", True)

            # Shallow copy race dicts before mutating results to avoid
            # altering the cached objects from fetch_recent_races
            filtered_races = []
            for race in races:
                race_copy = dict(race)
                results = race.get("results", [])
                race_copy["results"] = self.data_source.apply_favorite_filter(
                    results, top_finishers,
                    favorite_driver=self.favorite_driver,
                    always_show_favorite=always_show)
                filtered_races.append(race_copy)

            self._recent_races = filtered_races

    def _update_upcoming(self):
        """Update upcoming race data."""
        if "f1_upcoming" not in self.modes:
            return

        upcoming = self.data_source.get_upcoming_race()
        if upcoming:
            self._upcoming_race = upcoming

    def _update_qualifying(self):
        """Update qualifying results."""
        if "f1_qualifying" not in self.modes:
            return

        qualifying = self.data_source.fetch_qualifying()
        if qualifying:
            self._qualifying = qualifying

    def _update_practice(self):
        """Update free practice results."""
        if "f1_practice" not in self.modes:
            return

        sessions = self.config.get(
            "practice", {}).get("sessions_to_show", ["FP1", "FP2", "FP3"])
        top_n = self.config.get("practice", {}).get("top_n", 10)

        session_name_map = {
            "FP1": "Practice 1",
            "FP2": "Practice 2",
            "FP3": "Practice 3",
        }

        for fp_key in sessions:
            session_name = session_name_map.get(fp_key)
            if not session_name:
                continue

            result = self.data_source.fetch_practice_results(session_name)
            if result:
                # Shallow copy before slicing to avoid mutating cached dict
                result_copy = dict(result)
                if result_copy.get("results"):
                    result_copy["results"] = result_copy["results"][:top_n]
                self._practice_results[fp_key] = result_copy

    def _update_sprint(self):
        """Update sprint race results."""
        if "f1_sprint" not in self.modes:
            return

        sprint = self.data_source.fetch_sprint_results()
        if sprint:
            # Shallow copy before slicing to avoid mutating cached dict
            sprint_copy = dict(sprint)
            top_n = self.config.get("sprint", {}).get("top_finishers", 10)
            if sprint_copy.get("results"):
                sprint_copy["results"] = sprint_copy["results"][:top_n]
            self._sprint = sprint_copy

    def _update_calendar(self):
        """Update race calendar."""
        if "f1_calendar" not in self.modes:
            return

        cal_config = self.config.get("calendar", {})
        calendar = self.data_source.get_calendar(
            show_practice=cal_config.get("show_practice", False),
            show_qualifying=cal_config.get("show_qualifying", True),
            show_sprint=cal_config.get("show_sprint", True),
            max_events=cal_config.get("max_events", 5))
        if calendar:
            self._calendar = calendar

    # ─── Scroll Content Preparation ────────────────────────────────────

    def _prepare_scroll_content(self):
        """Pre-render all scroll mode content."""
        separator = self.renderer.render_f1_separator()

        # Driver standings
        if self._driver_standings:
            cards = [self.renderer.render_driver_standing(e)
                    for e in self._driver_standings]
            self._scroll_manager.prepare_and_display(
                "driver_standings", cards, separator)

        # Constructor standings
        if self._constructor_standings:
            cards = [self.renderer.render_constructor_standing(e)
                    for e in self._constructor_standings]
            self._scroll_manager.prepare_and_display(
                "constructor_standings", cards, separator)

        # Recent races
        if self._recent_races:
            cards = [self.renderer.render_race_result(race)
                    for race in self._recent_races]
            self._scroll_manager.prepare_and_display(
                "recent_races", cards, separator)

        # Qualifying
        if self._qualifying:
            cards = self._build_qualifying_cards()
            if cards:
                self._scroll_manager.prepare_and_display(
                    "qualifying", cards, separator)

        # Practice
        practice_cards = self._build_practice_cards()
        if practice_cards:
            self._scroll_manager.prepare_and_display(
                "practice", practice_cards, separator)

        # Sprint
        if self._sprint and self._sprint.get("results"):
            cards = [self.renderer.render_sprint_header(
                        self._sprint.get("race_name", ""))]
            for entry in self._sprint["results"]:
                cards.append(self.renderer.render_sprint_entry(entry))
            self._scroll_manager.prepare_and_display(
                "sprint", cards, separator)

        # Calendar
        if self._calendar:
            cards = [self.renderer.render_calendar_entry(e)
                    for e in self._calendar]
            self._scroll_manager.prepare_and_display(
                "calendar", cards, separator)

    def _build_qualifying_cards(self) -> List[Image.Image]:
        """Build qualifying result cards grouped by Q session."""
        if not self._qualifying:
            return []

        cards = []
        quali_config = self.config.get("qualifying", {})
        results = self._qualifying.get("results", [])
        race_name = self._qualifying.get("race_name", "")

        for session_key, show_key, label in [
            ("q3", "show_q3", "Q3"),
            ("q2", "show_q2", "Q2"),
            ("q1", "show_q1", "Q1"),
        ]:
            if not quali_config.get(show_key, True):
                continue

            # Add session header
            cards.append(self.renderer.render_qualifying_header(
                label, race_name))

            # Add entries for this session
            for entry in results:
                # Only show entries that have a time for this session
                if entry.get(session_key):
                    cards.append(self.renderer.render_qualifying_entry(
                        entry, label))
                elif entry.get("eliminated_in") == label:
                    # Show eliminated driver
                    cards.append(self.renderer.render_qualifying_entry(
                        entry, label))

        return cards

    def _build_practice_cards(self) -> List[Image.Image]:
        """Build practice result cards for all configured sessions."""
        cards = []

        for fp_key in ["FP3", "FP2", "FP1"]:  # Most recent first
            if fp_key not in self._practice_results:
                continue

            fp_data = self._practice_results[fp_key]
            cards.append(self.renderer.render_practice_header(
                fp_key, fp_data.get("circuit", "")))

            for entry in fp_data.get("results", []):
                cards.append(self.renderer.render_practice_entry(entry))

        return cards

    # ─── Display ───────────────────────────────────────────────────────

    def display(self, force_clear=False, display_mode=None):
        """
        Display the current F1 mode.

        Args:
            force_clear: Whether to clear display first
            display_mode: Specific mode to display (from manifest display_modes)
        """
        if display_mode is None:
            display_mode = self.modes[0] if self.modes else "f1_driver_standings"

        if display_mode == "f1_upcoming":
            self._display_upcoming(force_clear)
        elif display_mode in ("f1_driver_standings",
                               "f1_constructor_standings",
                               "f1_recent_races",
                               "f1_qualifying",
                               "f1_practice",
                               "f1_sprint",
                               "f1_calendar"):
            self._display_scroll_mode(display_mode, force_clear)
        else:
            self.logger.warning("Unknown display mode: %s", display_mode)

    def _display_upcoming(self, force_clear: bool):
        """Display the upcoming race card (static)."""
        if not self._upcoming_race:
            return

        if force_clear:
            self.display_manager.image.paste(
                Image.new("RGB",
                          (self.display_manager.matrix.width,
                           self.display_manager.matrix.height),
                          (0, 0, 0)),
                (0, 0))

        # Re-calculate countdown for live updates
        self._upcoming_race["countdown_seconds"] = None
        now = datetime.now(timezone.utc)
        next_session = None

        for session in self._upcoming_race.get("sessions", []):
            if session.get("status_state") == "pre" and session.get("date"):
                try:
                    session_dt = datetime.fromisoformat(
                        session["date"].replace("Z", "+00:00"))
                    if session_dt > now:
                        next_session = session
                        break
                except (ValueError, TypeError):
                    continue

        if next_session:
            try:
                session_dt = datetime.fromisoformat(
                    next_session["date"].replace("Z", "+00:00"))
                self._upcoming_race["countdown_seconds"] = max(
                    0, (session_dt - now).total_seconds())
                self._upcoming_race["next_session_type"] = next_session.get(
                    "type_abbr", "")
            except (ValueError, TypeError):
                pass

        card = self.renderer.render_upcoming_race(self._upcoming_race)
        self.display_manager.image.paste(card, (0, 0))
        self.display_manager.update_display()

    def _display_scroll_mode(self, display_mode: str,
                              force_clear: bool):
        """Display a scrolling mode."""
        mode_key_map = {
            "f1_driver_standings": "driver_standings",
            "f1_constructor_standings": "constructor_standings",
            "f1_recent_races": "recent_races",
            "f1_qualifying": "qualifying",
            "f1_practice": "practice",
            "f1_sprint": "sprint",
            "f1_calendar": "calendar",
        }

        mode_key = mode_key_map.get(display_mode, display_mode)

        if not self._scroll_manager.is_mode_prepared(mode_key):
            self._prepare_scroll_content()

        self._scroll_manager.display_frame(mode_key, force_clear)

    # ─── Vegas Mode ────────────────────────────────────────────────────

    def get_vegas_content(self) -> Optional[List[Image.Image]]:
        """Return all rendered cards for Vegas scroll mode."""
        images = self._scroll_manager.get_all_vegas_content_items()

        # Add upcoming race card if available
        if self._upcoming_race:
            upcoming_card = self.renderer.render_upcoming_race(
                self._upcoming_race)
            images.insert(0, upcoming_card)

        return images if images else None

    def get_vegas_content_type(self) -> str:
        """Return multi for scrolling content."""
        return "multi"

    def get_vegas_display_mode(self) -> VegasDisplayMode:
        """Return SCROLL for continuous scrolling."""
        return VegasDisplayMode.SCROLL

    # ─── Lifecycle ─────────────────────────────────────────────────────

    def on_config_change(self, new_config):
        """Handle config changes."""
        super().on_config_change(new_config)

        self.favorite_driver = new_config.get("favorite_driver", "").upper()
        self.favorite_team = normalize_constructor_id(
            new_config.get("favorite_team", ""))
        self._update_interval = new_config.get("update_interval", 3600)
        self.display_duration = new_config.get("display_duration", 30)
        self.modes = self._build_enabled_modes()

        # Force re-render with new settings
        self.renderer = F1Renderer(
            self.display_width, self.display_height,
            new_config, self.logo_loader, self.logger)

        # Force data refresh
        self._last_update = 0

    def cleanup(self):
        """Clean up resources."""
        self.logger.info("F1 Scoreboard cleanup")
        self.logo_loader.clear_cache()
        super().cleanup()
