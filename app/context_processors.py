from app.models import Profile


def _hex_to_rgb(hex_color):
    value = hex_color.lstrip("#")
    if len(value) != 6:
        return 194, 162, 118
    return tuple(int(value[index : index + 2], 16) for index in (0, 2, 4))


def _mix_colors(hex_color, target=(63, 55, 46), amount=0.28):
    source = _hex_to_rgb(hex_color)
    mixed = tuple(round(source[index] * (1 - amount) + target[index] * amount) for index in range(3))
    return "#{:02x}{:02x}{:02x}".format(*mixed)


def _appearance_from_profile(profile):
    softness = max(0, min(100, profile.background_softness))
    normalized = softness / 100
    return {
        "theme": profile.theme,
        "accent_color": profile.accent_color,
        "accent_strong": _mix_colors(profile.accent_color),
        "background_softness": softness,
        "background_overlay_alpha": f"{0.14 + normalized * 0.34:.2f}",
        "background_highlight_alpha": f"{0.20 + normalized * 0.28:.2f}",
        "glass_blur": f"{18 + normalized * 18:.0f}px",
        "density": profile.density,
        "date_format": profile.date_format,
        "time_format": profile.time_format,
        "timezone_name": profile.timezone_name,
    }


def appearance_settings(request):
    default_profile = Profile(
        display_name="",
        theme="light",
        accent_color="#c2a276",
        background_softness=55,
        density="comfortable",
        date_format="de_numeric",
        time_format="24h",
        timezone_name="Europe/Berlin",
    )

    profile = default_profile
    if request.user.is_authenticated:
        try:
            profile = request.user.profile
        except Profile.DoesNotExist:
            pass

    return {"appearance": _appearance_from_profile(profile)}
