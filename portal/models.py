from enum import IntEnum


class Tier(IntEnum):
    BASIC = 10
    PRO = 20
    EXPERT = 30


TIERS = {tier.value: tier.name.title() for tier in Tier}
BRANDING = {
    "name": "Trust Centre",
    "introduction": "Explore our security and compliance documents.",
    "team": "Our compliance team maintains the reports, certificates and policies in this portal.",
    "logo_url": "",
    "accent": "#076e65",
    "ink": "#132a42",
    "font": "sans",
    "access_inbox": "",
    "show_demo_team": False,
}
