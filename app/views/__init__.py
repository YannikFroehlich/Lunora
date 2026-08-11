from .administration_views import administration
from .auth_views import (
    LunoraLoginView,
    LunoraPasswordResetCompleteView,
    LunoraPasswordResetConfirmView,
    LunoraPasswordResetDoneView,
    LunoraPasswordResetView,
    register,
)
from .calendar_views import calendar
from .core_views import home, settings
from .message_views import messages, messages_live_updates
from .notification_views import claim_desktop_notifications
from .note_views import (
    note_action_api,
    note_attachment_download,
    note_attachment_upload_api,
    note_create_api,
    note_detail_api,
    note_pdf_export,
    note_share_candidates_api,
    note_share_delete_api,
    note_shares_api,
    note_shortcuts_api,
    note_version_restore_api,
    note_versions_api,
    notes,
)
from .weather_views import weather, weather_map_tile, weather_point, weather_suggestions

__all__ = [
    "calendar",
    "administration",
    "home",
    "messages",
    "messages_live_updates",
    "claim_desktop_notifications",
    "notes",
    "note_create_api",
    "note_detail_api",
    "note_pdf_export",
    "note_action_api",
    "note_shares_api",
    "note_share_delete_api",
    "note_share_candidates_api",
    "note_attachment_upload_api",
    "note_attachment_download",
    "note_versions_api",
    "note_version_restore_api",
    "note_shortcuts_api",
    "LunoraLoginView",
    "LunoraPasswordResetView",
    "LunoraPasswordResetDoneView",
    "LunoraPasswordResetConfirmView",
    "LunoraPasswordResetCompleteView",
    "register",
    "settings",
    "weather",
    "weather_map_tile",
    "weather_point",
    "weather_suggestions",
]
