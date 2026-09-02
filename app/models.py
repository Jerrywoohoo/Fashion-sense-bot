"""Typed enums and Pydantic schemas used across the app.

`UserProfileInput` validates data before it's written to SQLite via
`database.upsert_user_profile`. `ExtractedGarment` is the structured output
contract for `extractor.extract_garment_metadata` — it's also what AWS
Bedrock's response gets validated against, so a malformed model reply is
caught here rather than silently corrupting the database.
"""
from __future__ import annotations
import json

from enum import Enum
from typing import Any, List, Literal, Optional

from pydantic import BaseModel, Field

try:
    from pydantic import field_validator
except ImportError:
    from pydantic import validator
    def field_validator(*fields, mode="after"):
        return validator(*fields, pre=(mode == "before"), allow_reuse=True)

if not hasattr(BaseModel, "model_dump"):
    BaseModel.model_dump = lambda self, **kwargs: self.dict(**kwargs)
if not hasattr(BaseModel, "model_validate"):
    BaseModel.model_validate = classmethod(lambda cls, obj: cls.parse_obj(obj))



class GenderFrame(str, Enum):
    MASCULINE = "masculine"
    FEMININE = "feminine"
    ANDROGYNOUS = "androgynous"


class BodyBuild(str, Enum):
    SLIM = "slim"
    ATHLETIC_BROAD = "athletic_broad"
    AVERAGE = "average"
    MUSCULAR = "muscular"
    STOCKY = "stocky"


class Proportions(str, Enum):
    LONG_TORSO = "long_torso"
    BALANCED = "balanced"
    LONG_LEGS = "long_legs"


class ThermalPreference(str, Enum):
    RUNS_HOT = "runs_hot"
    NEEDS_AC_LAYER = "needs_ac_layer"


class UserProfileInput(BaseModel):
    """Validated payload for ``database.upsert_user_profile``."""

    gender_frame: Optional[GenderFrame] = None
    height_cm: Optional[int] = Field(default=None, ge=100, le=250)
    weight_kg: Optional[int] = Field(default=None, ge=30, le=250)
    body_build: Optional[BodyBuild] = None
    proportions: Optional[Proportions] = None
    favorite_silhouettes: List[str] = Field(default_factory=list)
    thermal_preference: Optional[ThermalPreference] = None

    def to_db_dict(self) -> dict[str, Any]:
        """Return a dict of only the fields that were set, DB-ready types."""
        data = self.model_dump(exclude_none=True)
        for enum_field in ("gender_frame", "body_build", "proportions", "thermal_preference"):
            if enum_field in data and getattr(self, enum_field) is not None:
                data[enum_field] = getattr(self, enum_field).value

        # Serialize list to JSON string for SQLite storage
        if "favorite_silhouettes" in data and isinstance(
            data["favorite_silhouettes"], (list, tuple, set)
        ):
            data["favorite_silhouettes"] = json.dumps(list(data["favorite_silhouettes"]))

        return data


class ExtractedGarment(BaseModel):
    """Structured metadata a vision model extracts from a garment photo."""

    category: Literal["top", "bottom", "outerwear", "footwear", "accessory"] = Field(
        description="The garment's primary category."
    )
    sub_category: str = Field(
        description="Specific garment style, e.g. 'cargo pants', 'waffle knit henley'."
    )
    primary_color: str = Field(
        description="Dominant color name or pattern."
    )
    accent_colors: List[str] = Field(
        default_factory=list,
        description="Secondary accent colors, patterns, or hardware tones.",
    )
    silhouette_fit: Literal[
        "cropped", "slim", "regular", "boxy", "wide_leg", "oversized"
    ] = Field(default="regular", description="The garment's cut/silhouette.")
    fabric_weight: Literal[
        "lightweight_breathable", "medium", "heavy_structured"
    ] = Field(default="medium", description="Perceived fabric weight.")
    formality_tier: int = Field(
        default=2,
        ge=1,
        le=5,
        description="1: Casual, 2: Elevated Casual, 3: Smart Casual, 4: Business, 5: Black-tie.",
    )
    estimated_brand: Optional[str] = Field(
        default=None,
        description="Visible brand label if identifiable.",
    )
    style_tags: List[str] = Field(
        default_factory=list,
        description="Aesthetic tags, e.g. ['streetwear', 'utility', 'casual'].",
    )
    layering_role: Literal["base", "mid", "outer", "standalone"] = Field(
        default="standalone",
        description="'base', 'mid', 'outer', or 'standalone'."
    )

    @field_validator("silhouette_fit", mode="before")
    @classmethod
    def normalize_silhouette(cls, v: Any) -> str:
        s = str(v).lower().strip().replace("-", " ")
        if any(w in s for w in ("crop", "short")):
            return "cropped"
        if any(w in s for w in ("slim", "tight", "skinny", "fitted", "taper")):
            return "slim"
        if any(w in s for w in ("wide", "baggy", "flare", "skater")):
            return "wide_leg"
        if any(w in s for w in ("boxy", "square")):
            return "boxy"
        if any(w in s for w in ("over", "large", "relaxed", "loose")):
            return "oversized"
        return "regular"

    @field_validator("fabric_weight", mode="before")
    @classmethod
    def normalize_fabric(cls, v: Any) -> str:
        s = str(v).lower().strip()
        if any(w in s for w in ("light", "breath", "thin", "sheer", "linen")):
            return "lightweight_breathable"
        if any(w in s for w in ("heavy", "thick", "struct", "denim", "wool", "fleece")):
            return "heavy_structured"
        return "medium"

    @field_validator("category", mode="before")
    @classmethod
    def normalize_category(cls, v: Any) -> str:
        s = str(v).lower().strip()
        if any(w in s for w in ("top", "shirt", "tee", "sweater", "hoodie", "tank")):
            return "top"
        if any(w in s for w in ("bottom", "pant", "trouser", "jean", "short", "skirt", "cargo")):
            return "bottom"
        if any(w in s for w in ("outer", "jacket", "coat", "overshirt", "blazer", "cardigan")):
            return "outerwear"
        if any(w in s for w in ("foot", "shoe", "boot", "sneaker", "sandal", "loafer")):
            return "footwear"
        if any(w in s for w in ("access", "hat", "cap", "belt", "bag", "jewelry")):
            return "accessory"
        return "top"

    @field_validator("layering_role", mode="before")
    @classmethod
    def normalize_layering(cls, v: Any) -> str:
        s = str(v).lower().strip()
        if any(w in s for w in ("base", "inner", "under")):
            return "base"
        if any(w in s for w in ("mid", "middle")):
            return "mid"
        if any(w in s for w in ("out", "jacket", "coat")):
            return "outer"
        return "standalone"

    def to_db_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "sub_category": self.sub_category,
            "brand": self.estimated_brand,
            "color": self.primary_color,
            "accent_colors": self.accent_colors,
            "silhouette_fit": self.silhouette_fit,
            "fabric_weight": self.fabric_weight,
            "formality_tier": self.formality_tier,
            "layering_role": self.layering_role,
            "tags": self.style_tags,
        }


class PhotoType(str, Enum):
    """The kind of wardrobe photo submitted by the user."""

    SINGLE_ITEM = "single_item"
    OOTD = "ootd"


class GarmentExtractionResult(BaseModel):
    """All garments identified from one uploaded photo."""

    photo_type: PhotoType = Field(
        description=(
            "Classify whether the photo is a standalone single clothing item "
            "or a full outfit / OOTD on a person."
        )
    )
    overall_aesthetic: Optional[str] = Field(
        default=None,
        description=(
            "If OOTD, short vibe description, e.g. 'smart casual office wear', "
            "'relaxed streetwear'."
        ),
    )
    garments: List[ExtractedGarment] = Field(
        description=(
            "List of all distinct visible garments identified (1 for single_item, "
            "2-5 for OOTD)."
        )
    )


class WeatherReport(BaseModel):
    """Normalised current-weather data used by the styling workflow."""

    temperature: float = Field(description="Current air temperature in Celsius.")
    humidity: int = Field(ge=0, le=100, description="Current relative humidity percentage.")
    apparent_temperature: float = Field(
        description="Feels-like temperature in Celsius."
    )
    precipitation: float = Field(ge=0, description="Current precipitation in mm.")
    condition: str = Field(description="Human-readable WMO weather condition.")
    is_rainy: bool = Field(description="Whether rain-aware styling is appropriate.")
    thermal_category: Literal["hot_humid", "temperate", "cool_indoor"] = Field(
        description="Practical thermal category for clothing selection."
    )
    location_name: str = Field(
        default="Singapore",
        description="Human-readable place this forecast is for, e.g. 'Tokyo, Japan'.",
    )


class OutfitItemSelection(BaseModel):
    """One verified wardrobe item selected for an outfit."""

    item_id: str = Field(
        description="Must match an existing item_id in the user's verified wardrobe"
    )
    category: str = Field(description="top, bottom, outerwear, footwear, accessory")
    sub_category: str
    primary_color: str
    role_in_outfit: str = Field(description="Why this specific item was chosen")


class OutfitRecommendation(BaseModel):
    """Validated recommendation returned by the stylist workflow."""

    outfit_name: str = Field(
        description="Catchy aesthetic title, e.g., 'Relaxed Earthy Smart-Casual'"
    )
    occasion: str
    items: List[OutfitItemSelection] = Field(
        description="Selected wardrobe items forming the cohesive look"
    )
    styling_tips: List[str] = Field(
        default_factory=list,
        description="Actionable tips: e.g. tuck style, sleeve roll, accessory grounding"
    )
    weather_reasoning: str = Field(
        description="Explanation of thermal comfort for the forecasted weather"
    )
    proportion_reasoning: str = Field(
        description="How the silhouette flatters the user's specific build and height"
    )

    @field_validator("styling_tips", mode="before")
    @classmethod
    def normalize_styling_tips(cls, v: Any) -> list[str]:
        if isinstance(v, str):
            # Split by newlines or sentence periods if the model returns a paragraph
            lines = [
                line.strip().lstrip("•-*0123456789. ")
                for line in v.split("\n")
                if line.strip()
            ]
            return lines if lines else [v.strip()]
        if isinstance(v, list):
            return [str(item).strip() for item in v if str(item).strip()]
        return ["Keep proportions balanced and let one core neutral ground the look."]
