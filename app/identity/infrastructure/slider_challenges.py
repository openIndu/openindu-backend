"""SQLAlchemy persistence and PIL rendering for slider challenges."""
import base64
import io
import random
from datetime import datetime

from sqlalchemy.orm import Session

from app.models.slider_challenge import SliderChallenge


class SqlSliderChallengeRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def purge_expired(self, now: datetime) -> None:
        self.db.query(SliderChallenge).filter(SliderChallenge.expires_at < now).delete()
        self.db.commit()

    def create(self, x: int, y: int, tolerance: int, expires_at: datetime, ip: str | None) -> SliderChallenge:
        challenge = SliderChallenge(x=x, y=y, tolerance=tolerance, expires_at=expires_at, ip_address=ip)
        self.db.add(challenge)
        self.db.commit()
        self.db.refresh(challenge)
        return challenge

    def find(self, challenge_id: int) -> SliderChallenge | None:
        return self.db.query(SliderChallenge).filter(SliderChallenge.id == challenge_id).first()

    def save(self, challenge: SliderChallenge) -> None:
        self.db.commit()

    def find_ticket(self, ticket: str, now: datetime) -> SliderChallenge | None:
        return (
            self.db.query(SliderChallenge)
            .filter(
                SliderChallenge.ticket == ticket,
                SliderChallenge.verified.is_(True),
                SliderChallenge.ticket_expires_at > now,
            )
            .first()
        )


class PilChallengeRenderer:
    """Render the puzzle background (with the notch cut out) and the piece
    sprite as PNG data URIs. Lazy import keeps the unit-test import path
    dependency-light."""

    def render(self, x: int, y: int) -> tuple[str, str]:
        from PIL import Image, ImageChops, ImageDraw, ImageFilter

        from app.identity.domain.captcha import (
            SLIDER_HEIGHT,
            SLIDER_KNOB,
            SLIDER_PIECE,
            SLIDER_WIDTH,
        )

        piece_w = SLIDER_PIECE + SLIDER_KNOB
        # Puzzle-piece silhouette on an L-mode mask (white = opaque). The body
        # sits inset by KNOB px so all four jigsaw features fit on the canvas:
        # an outward knob on top, an outward knob on the left, an inward notch
        # on the right and an inward notch on the bottom.
        mask = Image.new("L", (piece_w, piece_w), 0)
        draw = ImageDraw.Draw(mask)
        draw.rounded_rectangle(
            (SLIDER_KNOB, SLIDER_KNOB, SLIDER_KNOB + SLIDER_PIECE, SLIDER_KNOB + SLIDER_PIECE), radius=6, fill=255
        )
        cx = SLIDER_KNOB + SLIDER_PIECE // 2
        cy = SLIDER_KNOB + SLIDER_PIECE // 2
        draw.pieslice((cx - SLIDER_KNOB, 0, cx + SLIDER_KNOB, 2 * SLIDER_KNOB), 180, 360, fill=255)
        draw.pieslice((0, cy - SLIDER_KNOB, 2 * SLIDER_KNOB, cy + SLIDER_KNOB), 90, 270, fill=255)
        draw.pieslice(
            (SLIDER_KNOB + SLIDER_PIECE - SLIDER_KNOB, cy - SLIDER_KNOB,
             SLIDER_KNOB + SLIDER_PIECE + SLIDER_KNOB, cy + SLIDER_KNOB),
            0,
            360,
            fill=0,
        )
        draw.pieslice(
            (cx - SLIDER_KNOB, SLIDER_KNOB + SLIDER_PIECE - SLIDER_KNOB,
             cx + SLIDER_KNOB, SLIDER_KNOB + SLIDER_PIECE + SLIDER_KNOB),
            0,
            360,
            fill=0,
        )
        # A 1px ring just *inside* the silhouette marks edges on both the
        # notch and the sprite. Erosion runs on a padded copy: filters clip
        # their kernel at the canvas border, which would drop the ring
        # exactly where the silhouette touches the canvas (knob apex, edges).
        pad = 2
        padded = Image.new("L", (piece_w + 2 * pad, piece_w + 2 * pad), 0)
        padded.paste(mask, (pad, pad))
        eroded = padded.filter(ImageFilter.MinFilter(3)).crop((pad, pad, pad + piece_w, pad + piece_w))
        outline_mask = ImageChops.subtract(mask, eroded)

        # Procedural background: vertical gradient plus translucent shapes, so
        # no image assets ship with the app and every challenge looks different.
        background = Image.new("RGB", (SLIDER_WIDTH, SLIDER_HEIGHT))
        top = (random.randint(40, 120), random.randint(80, 160), random.randint(140, 220))
        bottom = (random.randint(120, 220), random.randint(40, 110), random.randint(60, 160))
        bg_draw = ImageDraw.Draw(background)
        for row in range(SLIDER_HEIGHT):
            ratio = row / SLIDER_HEIGHT
            color = tuple(int(t + (b - t) * ratio) for t, b in zip(top, bottom))
            bg_draw.line((0, row, SLIDER_WIDTH, row), fill=color)
        shape_draw = ImageDraw.Draw(background, "RGBA")
        for _ in range(7):
            cx, cy = random.randint(0, SLIDER_WIDTH), random.randint(0, SLIDER_HEIGHT)
            radius = random.randint(8, 30)
            shade = (255, 255, 255, 26) if random.random() < 0.5 else (0, 0, 0, 22)
            shape_draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=shade)
        background = background.filter(ImageFilter.GaussianBlur(0.8)).convert("RGBA")

        # The piece must carry the *untouched* background pixels, so crop it
        # before the notch is carved, then stack shadow + body + white outline.
        body = background.crop((x, y, x + piece_w, y + piece_w)).convert("RGBA")
        body.putalpha(mask)
        sprite = Image.new("RGBA", (piece_w, piece_w), (0, 0, 0, 0))
        shadow_alpha = mask.filter(ImageFilter.GaussianBlur(2)).point(lambda v: int(v * 0.45))
        shadow_layer = Image.new("RGBA", (piece_w, piece_w), (0, 0, 0, 255))
        shadow_layer.putalpha(shadow_alpha)
        sprite.alpha_composite(shadow_layer, dest=(1, 2))
        sprite.alpha_composite(body)
        outline_layer = Image.new("RGBA", (piece_w, piece_w), (255, 255, 255, 255))
        outline_layer.putalpha(outline_mask)
        sprite.alpha_composite(outline_layer)

        # Carve the notch: darken it and mark the edge in white so the drop
        # target is clearly visible against the busy background.
        overlay = Image.new("RGBA", background.size, (0, 0, 0, 0))
        hole_layer = Image.new("RGBA", (piece_w, piece_w), (12, 14, 20, 255))
        hole_layer.putalpha(mask.point(lambda v: 160 if v else 0))
        overlay.alpha_composite(hole_layer, dest=(x, y))
        ring_layer = Image.new("RGBA", (piece_w, piece_w), (255, 255, 255, 255))
        ring_layer.putalpha(outline_mask.point(lambda v: 220 if v else 0))
        overlay.alpha_composite(ring_layer, dest=(x, y))
        background = Image.alpha_composite(background, overlay)

        def to_uri(image) -> str:
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()

        return to_uri(background.convert("RGB")), to_uri(sprite)
