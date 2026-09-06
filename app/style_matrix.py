"""Deterministic zero-token styling rules used as local RAG context."""
from __future__ import annotations

import json
from typing import Any
from .models import WeatherReport




def get_styling_directives(
    weather: WeatherReport, profile: dict[str, Any] | None, occasion: str
) -> dict[str, list[str]]:
    """Return compact, deterministic outfit guidance for the current context."""
    profile = profile or {}
    build = str(profile.get("body_build") or "average")
    proportions = str(profile.get("proportions") or "balanced")
    height = profile.get("height_cm")
    weight = profile.get("weight_kg")
    gender = str(profile.get("gender_frame") or "androgynous")
    thermal_pref = str(profile.get("thermal_preference") or "")

    raw_sil = profile.get("favorite_silhouettes") or "[]"
    fav_silhouettes = json.loads(raw_sil) if isinstance(raw_sil, str) else raw_sil

    silhouettes = [
        "Apply the Rule of Thirds (1:2 vertical proportion): avoid splitting top and bottom 50/50."
    ]

    if proportions == "long_torso" or (height is not None and int(height) < 172):
        silhouettes.append("Longer torso: recommend tucked or cropped tops with mid/high-rise pants to visually raise waistline.")
    elif proportions == "long_legs":
        silhouettes.append("Longer legs: untucked boxy tops and drop-waist silhouettes create balanced visual weight.")

    if build in {"athletic_broad", "muscular"}:
        silhouettes.append("Broad frame: drop-shoulder seams or relaxed boxy cuts drape cleanly without pulling across chest.")
    elif build == "slim":
        silhouettes.append("Slim frame: slight layering (overshirt/cardigan) or textured fabrics add structure without drowning the frame.")

    if "fitted_top_wide_bottom" in fav_silhouettes:
        silhouettes.append("User loves Fitted Top + Wide Bottom: balance slim upper layer with relaxed/wide lower layer.")
    if "relaxed_oversized" in fav_silhouettes:
        silhouettes.append("User loves Relaxed/Oversized: ensure clean hem breaks or distinct textures so volume looks intentional.")

    fabrics: list[str] = []
    needs_ac_layer = thermal_pref == "needs_ac_layer"


    if weather.is_rainy:
        fabrics.append("Rainy conditions: avoid floor-sweeping hems and delicate suede footwear.")

    colors = [
        "Ground the outfit with 1 core neutral (cream, white, washed charcoal, navy, olive, or brown).",
        "Limit palette to 2-3 harmonious shades to maintain an intentional, elevated aesthetic."
    ]

    return {
        "target_silhouettes": silhouettes,
        "ideal_fabrics": fabrics,
        "color_suggestions": colors,
    }
