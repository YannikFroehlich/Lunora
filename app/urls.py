from django.contrib.auth.views import LogoutView
from django.urls import path
from django.views.generic import RedirectView

import app.views as view

login_view = view.LunoraLoginView.as_view()

urlpatterns = [
    path("", RedirectView.as_view(url="/home/", permanent=False)),
    path("login/", login_view, name="login"),
    path("accounts/login/", login_view, name="accounts_login"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("register/", view.register, name="register"),
    path("password-reset/", view.LunoraPasswordResetView.as_view(), name="password_reset"),
    path("password-reset/done/", view.LunoraPasswordResetDoneView.as_view(), name="password_reset_done"),
    path(
        "reset/<uidb64>/<token>/",
        view.LunoraPasswordResetConfirmView.as_view(),
        name="password_reset_confirm",
    ),
    path("reset/done/", view.LunoraPasswordResetCompleteView.as_view(), name="password_reset_complete"),
    path("administration/", view.administration, name="administration"),
    path("home/", view.home, name="home"),
    path("settings/", view.settings, name="settings"),
    path("weather/", view.weather, name="weather"),
    path("weather/point/", view.weather_point, name="weather_point"),
    path("weather/suggest/", view.weather_suggestions, name="weather_suggestions"),
    path(
        "weather/map/<str:layer>/<int:z>/<int:x>/<int:y>.png",
        view.weather_map_tile,
        name="weather_map_tile",
    ),
    path("calendar/", view.calendar, name="calendar"),
    path("notifications/claim/", view.claim_desktop_notifications, name="notification_claim"),
    path("messages/", view.messages, name="messages"),
    path("messages/live/", view.messages_live_updates, name="messages_live_updates"),
    path("messages/<int:conversation_id>/live/", view.messages_live_updates, name="messages_live_detail_updates"),
    path("messages/<int:conversation_id>/", view.messages, name="messages_detail"),
    path("notes/", view.notes, name="notes"),
    path("notes/<int:note_id>/export/pdf/", view.note_pdf_export, name="note_pdf_export"),
    path("notes/<int:note_id>/", view.notes, name="note_detail"),
    path("notes/api/create/", view.note_create_api, name="note_create_api"),
    path("notes/api/share-candidates/", view.note_share_candidates_api, name="note_share_candidates_api"),
    path("notes/api/shortcuts/", view.note_shortcuts_api, name="note_shortcuts_api"),
    path("notes/api/<int:note_id>/", view.note_detail_api, name="note_detail_api"),
    path("notes/api/<int:note_id>/actions/", view.note_action_api, name="note_action_api"),
    path("notes/api/<int:note_id>/shares/", view.note_shares_api, name="note_shares_api"),
    path(
        "notes/api/<int:note_id>/shares/<int:user_id>/",
        view.note_share_delete_api,
        name="note_share_delete_api",
    ),
    path("notes/api/<int:note_id>/attachments/", view.note_attachment_upload_api, name="note_attachment_upload_api"),
    path("notes/api/<int:note_id>/versions/", view.note_versions_api, name="note_versions_api"),
    path(
        "notes/api/<int:note_id>/versions/<int:version_id>/restore/",
        view.note_version_restore_api,
        name="note_version_restore_api",
    ),
    path(
        "notes/attachments/<uuid:file_id>/<str:disposition>/",
        view.note_attachment_download,
        name="note_attachment_download",
    ),
]
