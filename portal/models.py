from enum import IntEnum


class Tier(IntEnum):
    BASIC = 10
    PRO = 20
    EXPERT = 30


TIERS = {tier.value: tier.name.title() for tier in Tier}
BURGUNDY = "#3F0123"
CORAL = "#FF7458"

BRANDING = {
    "name": "Trust Centre",
    "introduction": "Explore our security and compliance documents.",
    "team": "Our compliance team maintains the reports, certificates and policies in this portal.",
    "logo_url": "",
    "accent": CORAL,
    "ink": BURGUNDY,
    "font": "chromatica",
    "access_inbox": "",
    "show_demo_team": False,
}


def button_text(accent):
    def luminance(color):
        channels = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
        return sum(channel * weight for channel, weight in zip(linear, (0.2126, 0.7152, 0.0722)))

    background = luminance(accent)
    dark = luminance(BURGUNDY)
    if (max(background, dark) + 0.05) / (min(background, dark) + 0.05) >= 4.5:
        return BURGUNDY
    return "#FFFFFF" if 1.05 / (background + 0.05) >= 4.5 else "#000000"
