"""Tests for dashboard config generation."""

from unittest.mock import MagicMock

from custom_components.rowenta_roboeye.dashboard import _build_config


def _mock_hass() -> MagicMock:
    hass = MagicMock()
    hass.states.get.return_value = MagicMock(state="on")
    return hass


def test_build_config_filters_schedule_entries_to_active_map():
    hass = _mock_hass()
    rooms = [{"id": 3, "name": "Bedroom", "room_type": "sleeping"}]
    schedules = [
        {
            "task_id": 10,
            "enabled": 1,
            "time": {"days_of_week": [1], "hour": 8, "min": 30},
            "task": {"map_id": "3", "cleaning_mode": 2, "parameters": [3]},
        },
        {
            "task_id": 11,
            "enabled": 1,
            "time": {"days_of_week": [2], "hour": 9, "min": 0},
            "task": {"map_id": "4", "cleaning_mode": 2, "parameters": [3]},
        },
    ]

    config = _build_config(
        hass,
        rooms=rooms,
        device_id="dev123",
        active_map_id="3",
        available_maps=[{"map_id": "3", "display_name": "Home"}],
        schedule_entries=schedules,
    )

    control_view = config["views"][0]
    schedule_card = next(card for card in control_view["cards"] if card.get("title") == "Schedule")
    entities = schedule_card["entities"]
    assert {"entity": "switch.dev123_schedule_10", "name": "Enabled"} in entities
    assert {"entity": "switch.dev123_schedule_11", "name": "Enabled"} not in entities
