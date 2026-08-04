from .auth_views import register
from .calendar_views import calendar
from .core_views import home, settings
from .message_views import messages, messages_live_updates
from .weather_views import weather, weather_map_tile, weather_point, weather_suggestions

__all__ = [
    "calendar",
    "home",
    "messages",
    "messages_live_updates",
    "register",
    "settings",
    "weather",
    "weather_map_tile",
    "weather_point",
    "weather_suggestions",
]
