"""Renderização do layout de artilharia (com fotos dos jogadores) via Pillow.

A lógica de dados continua na FIFA API (services.copa.get_scorers); aqui só
montamos a imagem, buscando a foto de cada artilheiro em services.photos.
"""

import io
import logging

from services import photos
from services.bracket import (
    _font, _BG, _PANEL, _PANEL_HI, _LINE, _WHITE, _GREY, _GOLD, _WIN,
)
from services.copa import PT_TO_EN, _norm

logger = logging.getLogger(__name__)

_MARGIN = 28
_TOP = 104
_ROW_H = 78
_PHOTO = 58
_BOTTOM = 24
_WIDTH = 760
_BR_GREEN = (0, 156, 59)


def _circle(png: bytes, size: int, ring) -> "object":
    """Foto recortada em círculo (anti-aliasing 4x) com anel colorido."""
    from PIL import Image, ImageDraw
    ss = 4
    d = size * ss
    src = Image.open(io.BytesIO(png)).convert("RGBA").resize((d, d), Image.LANCZOS)
    mask = Image.new("L", (d, d), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, d, d], fill=255)
    out = Image.new("RGBA", (d, d), (0, 0, 0, 0))
    out.paste(src, (0, 0), mask)
    w = max(2, size // 14) * ss
    ImageDraw.Draw(out).ellipse([w // 2, w // 2, d - w // 2, d - w // 2], outline=ring, width=w)
    return out.resize((size, size), Image.LANCZOS)


def _placeholder(size: int, ring, initials: str) -> "object":
    from PIL import Image, ImageDraw
    ss = 4
    d = size * ss
    out = Image.new("RGBA", (d, d), (0, 0, 0, 0))
    dr = ImageDraw.Draw(out)
    dr.ellipse([0, 0, d, d], fill=_PANEL_HI)
    w = max(2, size // 14) * ss
    dr.ellipse([w // 2, w // 2, d - w // 2, d - w // 2], outline=ring, width=w)
    f = _font(size * ss // 2, bold=True)
    tw = dr.textlength(initials, font=f)
    bb = dr.textbbox((0, 0), initials, font=f)
    dr.text(((d - tw) / 2, (d - (bb[3] - bb[1])) / 2 - bb[1]), initials, font=f, fill=_GREY)
    return out.resize((size, size), Image.LANCZOS)


def _initials(name: str) -> str:
    toks = [t for t in name.split() if t]
    if not toks:
        return "?"
    if len(toks) == 1:
        return toks[0][:2].upper()
    return (toks[0][0] + toks[-1][0]).upper()


def render_artilharia_png(scorers: list[dict], top: int = 20) -> bytes:
    from PIL import Image, ImageDraw

    rows = scorers[:top]
    height = _TOP + max(len(rows), 1) * _ROW_H + _BOTTOM
    img = Image.new("RGB", (_WIDTH, height), _BG)
    draw = ImageDraw.Draw(img)

    # cabeçalho
    draw.text((_MARGIN, 28), "Artilharia — Copa do Mundo 2026",
              font=_font(34, bold=True), fill=_GOLD)
    draw.text((_MARGIN, 72), "Top artilheiros · gols via FIFA · fotos via API-Football",
              font=_font(16), fill=_GREY)

    f_rank = _font(22, bold=True)
    f_name = _font(25, bold=True)
    f_team = _font(17)
    f_gols = _font(30, bold=True)
    f_gl = _font(15)

    for i, s in enumerate(rows):
        y = _TOP + i * _ROW_H
        team_en = PT_TO_EN.get(_norm(s["team"]), _norm(s["team"]))
        is_br = team_en == "brazil"
        ring = _BR_GREEN if is_br else _LINE

        # fundo da linha (alternado) + acento do Brasil
        if i % 2 == 0:
            draw.rounded_rectangle([_MARGIN, y + 4, _WIDTH - _MARGIN, y + _ROW_H - 4],
                                   radius=10, fill=_PANEL)
        if is_br:
            draw.rounded_rectangle([_MARGIN, y + 4, _MARGIN + 6, y + _ROW_H - 4],
                                   radius=3, fill=_BR_GREEN)

        cy = y + _ROW_H // 2
        # posição
        draw.text((_MARGIN + 18, cy - 13), f"{i+1}", font=f_rank,
                  fill=_GOLD if i < 3 else _GREY)

        # foto circular
        px = _MARGIN + 64
        png = photos.player_photo(s["name"], s["team"])
        avatar = (_circle(png, _PHOTO, ring) if png
                  else _placeholder(_PHOTO, ring, _initials(s["name"])))
        img.paste(avatar, (px, cy - _PHOTO // 2), avatar)

        # nome + time
        tx = px + _PHOTO + 18
        name_color = _WIN if is_br else _WHITE
        draw.text((tx, cy - 24), s["name"], font=f_name, fill=name_color)
        draw.text((tx, cy + 6), s["team"], font=f_team, fill=_GREY)

        # gols à direita (número + rótulo, ambos alinhados à direita)
        gtxt = str(s["goals"])
        right = _WIDTH - _MARGIN - 24
        gw = draw.textlength(gtxt, font=f_gols)
        lw = draw.textlength("gols", font=f_gl)
        draw.text((right - gw, cy - 20), gtxt, font=f_gols, fill=_GOLD)
        draw.text((right - lw, cy + 16), "gols", font=f_gl, fill=_GREY)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
