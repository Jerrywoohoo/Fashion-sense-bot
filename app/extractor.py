"""AWS Bedrock multimodal vision extraction for garment photos.

Reads AWS credentials from the environment (populated via `.env`):
``AWS_ACCESS_KEY_ID``, ``AWS_SECRET_ACCESS_KEY``, ``AWS_SESSION_TOKEN``
(optional, for temporary/STS credentials), and ``AWS_DEFAULT_REGION``.

If credentials aren't configured, ``check_aws_credentials_configured()``
lets callers detect that up front and degrade gracefully (e.g. save the
photo and tell the user extraction isn't available yet) instead of the
process crashing on a boto3 call.
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
from typing import Any, Optional

from PIL import Image, ImageDraw, ImageFont

from .models import ExtractedGarment, GarmentExtractionResult, PhotoType

logger = logging.getLogger(__name__)

# Any Bedrock-hosted multimodal model that supports the Converse API works
# here. Claude Haiku is a good default: fast and cheap for a cataloging
# task like this.
DEFAULT_MODEL_ID = "amazon.nova-pro-v1:0"

MAX_IMAGE_DIM = 1024

_SYSTEM_PROMPT = (
    "You are an expert wardrobe cataloger for a personal styling assistant. "
    "Given a clothing photo and a caption from its owner, classify the item(s) "
    "and extract detailed aesthetic metadata.\n\n"
    "CRITICAL RULES:\n"
    "1. Give HIGH PRIORITY to the owner's caption for brands, sports teams, artist collaborations, "
    "colors, patterns (e.g., stripes, plaid), and specific nicknames or styles.\n"
    "2. BRAND & LOGO RECOGNITION: Inspect visible logos, brand typography, tags, sports club crests, "
    "or graphic artist signatures (e.g. Nike, Adidas, Uniqlo, Stussy, Zara, Arsenal F.C., Keith Haring). "
    "Set 'estimated_brand' to the detected brand, team, or artist name (e.g. 'Uniqlo', 'Arsenal F.C.', 'Keith Haring'). "
    "If no brand is identifiable from the photo or caption, set 'estimated_brand' to null.\n"
    "3. If an item is striped, printed, or multi-colored, reflect this in primary_color "
    '(e.g., "navy and cream striped", "olive plaid") or specify in accent_colors.\n'
    "4. For a single-item photo, return exactly one garment. For an OOTD on a person, "
    "return every distinct visible garment (2 to 5).\n\n"
    "Respond with STRICTLY VALID JSON only — no markdown fences, no prose — matching this schema:\n"
    "{\n"
    '  "photo_type": "single_item" or "ootd",\n'
    '  "overall_aesthetic": short OOTD vibe or null for single item,\n'
    '  "garments": [\n'
    "    {\n"
    '      "category": one of "top", "bottom", "outerwear", "footwear", "accessory",\n'
    '      "sub_category": specific garment style (e.g. "horizontal striped crewneck tee", "2024 home kit jersey"),\n'
    '      "primary_color": dominant color/pattern (e.g. "navy and cream striped"),\n'
    '      "accent_colors": list of secondary tones, stripes, or hardware colors,\n'
    '      "silhouette_fit": one of "cropped", "slim", "regular", "boxy", "wide_leg", "oversized",\n'
    '      "fabric_weight": one of "lightweight_breathable", "medium", "heavy_structured",\n'
    '      "formality_tier": integer 1-5,\n'
    '      "estimated_brand": brand / team / artist string or null,\n'
    '      "style_tags": list of 3-5 tags,\n'
    '      "layering_role": one of "base", "mid", "outer", "standalone"\n'
    "    }\n"
    "  ]\n"
    "}"
)

_IDENTITY_PROMPT = (
    "You decide whether two wardrobe records describe the exact same physical "
    "clothing item, using metadata only. Be highly conservative: items of the "
    "same type are NOT identical merely because they share a generic feature "
    "such as crewneck, jeans, or sneakers. Different dominant colours, patterns, "
    "brands, cuts, fabrics, or materially different tags mean NOT_IDENTICAL. "
    "Return strictly valid JSON only: {\"identical\": true|false, "
    "\"reason\": \"brief reason\"}."
)


class CredentialsNotConfiguredError(RuntimeError):
    """Raised when AWS Bedrock credentials are missing."""


def extract_demo_garment_metadata(user_caption: Optional[str]) -> GarmentExtractionResult:
    """Return deterministic fixture metadata for an admin-only Telegram demo.

    Captions must start with ``demo:``. This intentionally does not inspect
    image pixels: it lets the entire capture → confirmation → style workflow
    be shown while Bedrock access is pending.
    """
    caption = (user_caption or "").strip().lower()
    if not caption.startswith("demo:"):
        raise ValueError(
            "Demo photos need a caption such as 'demo: linen shirt', "
            "'demo: olive chinos', 'demo: white sneakers', or 'demo: ootd'."
        )
    label = caption.removeprefix("demo:").strip()

    def garment(**fields: Any) -> dict[str, Any]:
        return fields

    fixtures: list[tuple[tuple[str, ...], dict[str, Any]]] = [
        (
            ("linen shirt", "shirt", "tee", "t shirt", "t-shirt"),
            garment(
                category="top", sub_category="linen camp collar shirt",
                primary_color="cream", accent_colors=[], silhouette_fit="boxy",
                fabric_weight="lightweight_breathable", formality_tier=2,
                estimated_brand=None, style_tags=["minimal", "summer", "smart_casual"],
                layering_role="standalone",
            ),
        ),
        (
            ("chinos", "trousers", "pants", "jeans"),
            garment(
                category="bottom", sub_category="straight chinos", primary_color="olive",
                accent_colors=[], silhouette_fit="regular", fabric_weight="lightweight_breathable",
                formality_tier=3, estimated_brand=None,
                style_tags=["earthy", "smart_casual", "versatile"], layering_role="standalone",
            ),
        ),
        (
            ("sneakers", "sneaker", "shoes", "shoe"),
            garment(
                category="footwear", sub_category="white canvas sneakers", primary_color="white",
                accent_colors=[], silhouette_fit="regular", fabric_weight="medium",
                formality_tier=2, estimated_brand=None,
                style_tags=["minimal", "casual", "versatile"], layering_role="standalone",
            ),
        ),
        (
            ("jacket", "overshirt", "cardigan"),
            garment(
                category="outerwear", sub_category="lightweight overshirt", primary_color="navy",
                accent_colors=[], silhouette_fit="regular", fabric_weight="lightweight_breathable",
                formality_tier=2, estimated_brand=None,
                style_tags=["layering", "minimal", "smart_casual"], layering_role="outer",
            ),
        ),
    ]
    if "ootd" in label:
        return GarmentExtractionResult.model_validate(
            {
                "photo_type": PhotoType.OOTD,
                "overall_aesthetic": "relaxed tropical smart casual",
                "garments": [fixture for _, fixture in fixtures[:3]],
            }
        )
    for keywords, fixture in fixtures:
        if any(keyword in label for keyword in keywords):
            return GarmentExtractionResult.model_validate(
                {"photo_type": PhotoType.SINGLE_ITEM, "overall_aesthetic": None, "garments": [fixture]}
            )
    raise ValueError(
        "Unknown demo fixture. Use linen shirt, olive chinos, white sneakers, "
        "lightweight jacket, or ootd after 'demo:'."
    )


def check_aws_credentials_configured() -> bool:
    """Return True if the minimum required AWS env vars are populated.

    This only checks presence, not validity — a misconfigured but
    non-empty key will still fail later at the Bedrock API call, which
    callers should catch separately (see ``extract_garment_metadata``).
    """
    return bool(os.getenv("AWS_ACCESS_KEY_ID")) and bool(
        os.getenv("AWS_SECRET_ACCESS_KEY")
    )


def _get_bedrock_client() -> Any:
    """Build a ``bedrock-runtime`` client from environment credentials.

    Raises:
        CredentialsNotConfiguredError: If required env vars are missing.
    """
    if not check_aws_credentials_configured():
        raise CredentialsNotConfiguredError(
            "AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY are not set. Vision "
            "extraction is disabled until AWS Bedrock credentials are "
            "configured in .env (see .env.example)."
        )

    try:
        import boto3
    except ImportError:
        raise CredentialsNotConfiguredError(
            "boto3 is not installed. Install boto3 to enable AWS Bedrock features."
        )

    session = boto3.Session(
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
        aws_session_token=os.getenv("AWS_SESSION_TOKEN") or None,
        region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-1"),
    )
    return session.client("bedrock-runtime")


def preprocess_image(image_path: str, max_dim: int = MAX_IMAGE_DIM) -> tuple[bytes, str]:
    """Load an image, normalize to RGB, downscale, and re-encode as JPEG.

    Keeps Bedrock request payloads small and avoids format issues (e.g.
    Telegram photos with an alpha channel or palette mode).

    Returns:
        A tuple of ``(jpeg_bytes, media_type)``; ``media_type`` is always
        ``'image/jpeg'``.
    """
    with Image.open(image_path) as img:
        if img.mode != "RGB":
            img = img.convert("RGB")

        width, height = img.size
        if max(width, height) > max_dim:
            scale = max_dim / float(max(width, height))
            new_size = (max(1, int(width * scale)), max(1, int(height * scale)))
            img = img.resize(new_size, Image.LANCZOS)

        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=88)
        return buffer.getvalue(), "image/jpeg"


def compute_image_dhash(image_path: str, hash_size: int = 8) -> str:
    """Compute difference hash (dHash) for an image to detect duplicate photos."""
    try:
        with Image.open(image_path) as img:
            img = img.convert("L").resize((hash_size + 1, hash_size), Image.LANCZOS)
            pixels = list(img.getdata())
            diff = []
            for row in range(hash_size):
                for col in range(hash_size):
                    pixel_left = pixels[row * (hash_size + 1) + col]
                    pixel_right = pixels[row * (hash_size + 1) + col + 1]
                    diff.append(pixel_left > pixel_right)
            return "".join("1" if d else "0" for d in diff)
    except Exception:
        return ""


def hamming_distance(hash1: str, hash2: str) -> int:
    """Calculate hamming distance between two binary hash strings."""
    if not hash1 or not hash2 or len(hash1) != len(hash2):
        return 999
    return sum(c1 != c2 for c1, c2 in zip(hash1, hash2))


def create_labeled_image_bytes(
    image_path: Any,
    labels: list[str],
) -> io.BytesIO:
    """Draw high-contrast, rounded badges with item metadata on the photo in memory."""
    with Image.open(image_path) as img:
        img = img.convert("RGBA")
        overlay = Image.new("RGBA", img.size, (255, 255, 255, 0))
        draw = ImageDraw.Draw(overlay)

        font_size = max(16, min(36, int(min(img.size) * 0.045)))
        try:
            font = ImageFont.load_default(size=font_size)
        except TypeError:
            font = ImageFont.load_default()

        y_offset = int(img.height * 0.03)
        x_offset = int(img.width * 0.03)
        pad_x = max(8, int(font_size * 0.4))
        pad_y = max(4, int(font_size * 0.25))

        for label in labels:
            bbox = draw.textbbox((x_offset + pad_x, y_offset + pad_y), label, font=font)
            bg_box = (x_offset, y_offset, bbox[2] + pad_x, bbox[3] + pad_y)
            # High-contrast solid dark slate badge with bright white border and white text
            draw.rounded_rectangle(
                bg_box,
                radius=max(6, pad_y * 2),
                fill=(15, 23, 42, 230),
                outline=(255, 255, 255, 255),
                width=max(2, int(font_size * 0.08)),
            )
            draw.text(
                (x_offset + pad_x, y_offset + pad_y),
                label,
                fill=(255, 255, 255, 255),
                font=font,
            )
            y_offset += (bbox[3] - bbox[1]) + pad_y * 2 + int(font_size * 0.35)

        combined = Image.alpha_composite(img, overlay).convert("RGB")
        buffer = io.BytesIO()
        combined.save(buffer, format="JPEG", quality=92)
        buffer.seek(0)
        return buffer


def _extract_json(text: str) -> dict[str, Any]:
    """Pull a JSON object out of a model response.

    Tolerates the model wrapping its answer in a ```json ... ``` fence, or
    adding stray text around the object, even though the system prompt
    asks it not to.
    """
    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else text

    start = candidate.find("{")
    if start < 0:
        raise json.JSONDecodeError("No JSON object found", candidate, 0)
    payload, _end = json.JSONDecoder().raw_decode(candidate[start:])
    if not isinstance(payload, dict):
        raise ValueError("Expected a JSON object")
    return payload


def extract_garment_metadata(
    image_path: str,
    user_caption: Optional[str] = None,
    model_id: str = DEFAULT_MODEL_ID,
) -> GarmentExtractionResult:
    """Call AWS Bedrock's Converse API to extract structured garment metadata.

    This is a blocking (synchronous) boto3 call — in an async context like
    the Telegram bot, run it via ``asyncio.to_thread`` rather than
    ``await``-ing it directly.

    Raises:
        CredentialsNotConfiguredError: If AWS credentials aren't set.
        botocore.exceptions.ClientError: For AWS-side failures (bad model
            ID, throttling, permissions, etc.) — left uncaught so callers
            can log or message the user with specifics.
        ValueError: If the model's response can't be parsed into
            ``GarmentExtractionResult``.
    """
    client = _get_bedrock_client()
    image_bytes, _media_type = preprocess_image(image_path)

    user_text = "Catalog every distinct garment in this photo accurately."
    if user_caption:
        user_text = (
            f'User notes regarding this clothing item: "{user_caption}". '
            "Incorporate these details (especially patterns, cuts, or colors) into the metadata."
        )

    response = client.converse(
        modelId=model_id,
        system=[{"text": _SYSTEM_PROMPT}],
        messages=[
            {
                "role": "user",
                "content": [
                    {"image": {"format": "jpeg", "source": {"bytes": image_bytes}}},
                    {"text": user_text},
                ],
            }
        ],
        inferenceConfig={"maxTokens": 1024, "temperature": 0.2},
    )

    content_blocks = response["output"]["message"]["content"]
    text_output = "".join(block["text"] for block in content_blocks if "text" in block)

    try:
        payload = _extract_json(text_output)
        return GarmentExtractionResult.model_validate(payload)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError(
            f"Could not parse Bedrock response into GarmentExtractionResult: {exc}. "
            f"Raw response: {text_output!r}"
        ) from exc


def garments_are_identical(
    incoming: ExtractedGarment, saved: dict[str, Any], model_id: str = DEFAULT_MODEL_ID
) -> tuple[bool, str]:
    """Ask Bedrock for a high-precision identity decision from stored tags.

    This deliberately returns ``False`` if the model cannot be reached: a
    missed duplicate is much less harmful than collapsing two real garments.
    """
    client = _get_bedrock_client()
    saved_record = {
        "category": saved.get("category"),
        "sub_category": saved.get("sub_category"),
        "primary_color": saved.get("color"),
        "accent_colors": saved.get("accent_colors", []),
        "silhouette_fit": saved.get("silhouette_fit"),
        "fabric_weight": saved.get("fabric_weight"),
        "formality_tier": saved.get("formality_tier"),
        "estimated_brand": saved.get("brand"),
        "style_tags": saved.get("tags", []),
        "layering_role": saved.get("layering_role"),
    }
    response = client.converse(
        modelId=model_id,
        system=[{"text": _IDENTITY_PROMPT}],
        messages=[{
            "role": "user",
            "content": [{"text": json.dumps({
                "incoming": incoming.model_dump(), "saved": saved_record,
            })}],
        }],
        inferenceConfig={"maxTokens": 160, "temperature": 0},
    )
    text_output = "".join(
        block["text"] for block in response["output"]["message"]["content"]
        if "text" in block
    )
    payload = _extract_json(text_output)
    return bool(payload.get("identical")), str(payload.get("reason") or "matching metadata")


def refine_garment_metadata(
    image_path: str,
    current: GarmentExtractionResult,
    feedback: str,
    model_id: str = DEFAULT_MODEL_ID,
) -> GarmentExtractionResult:
    """Re-extract a pending capture using the owner's natural-language correction."""
    client = _get_bedrock_client()
    image_bytes, _media_type = preprocess_image(image_path)
    instruction = (
        "The owner says the previous catalog result needs correction. Reinspect "
        "the image and return a corrected result using the schema in the system "
        "prompt. Keep the same photo_type and exactly the same number of garments "
        "as the previous result. The owner's correction is authoritative: apply it "
        "even if the image is ambiguous, then revise related fields needed to "
        "keep the record internally consistent.\n\nPrevious result:\n"
        f"{current.model_dump_json()}\n\nOwner feedback:\n{feedback}"
    )
    response = client.converse(
        modelId=model_id,
        system=[{"text": _SYSTEM_PROMPT}],
        messages=[{"role": "user", "content": [
            {"image": {"format": "jpeg", "source": {"bytes": image_bytes}}},
            {"text": instruction},
        ]}],
        inferenceConfig={"maxTokens": 1024, "temperature": 0.1},
    )
    text_output = "".join(
        block["text"] for block in response["output"]["message"]["content"]
        if "text" in block
    )
    refined = GarmentExtractionResult.model_validate(_extract_json(text_output))
    if refined.photo_type != current.photo_type or len(refined.garments) != len(current.garments):
        raise ValueError("The correction did not preserve this capture's item count.")
    return refined
