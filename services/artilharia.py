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

_WIDTH = 820
_MARGIN = 30
_HEADER = 140
_BOTTOM = 30
_TOPN = 5
_ROW_H = (_WIDTH - _HEADER - _BOTTOM) // _TOPN   # garante imagem 1:1
_PHOTO = _ROW_H - 26
_BR_GREEN = (0, 156, 59)


def _rounded(im, radius: int):
    """Aplica cantos arredondados a uma imagem RGBA."""
    from PIL import Image, ImageDraw
    w, h = im.size
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1], radius=radius, fill=255)
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    out.paste(im, (0, 0), mask)
    return out


def _photo_img(png: bytes, size: int, border):
    """Foto solta (retângulo) com cantos levemente arredondados e borda fina."""
    from PIL import Image, ImageDraw
    ss = 4
    d = size * ss
    src = Image.open(io.BytesIO(png)).convert("RGBA").resize((d, d), Image.LANCZOS)
    img = _rounded(src, radius=d // 10)
    ImageDraw.Draw(img).rounded_rectangle([1, 1, d - 2, d - 2], radius=d // 10,
                                          outline=border, width=ss)
    return img.resize((size, size), Image.LANCZOS)


def _placeholder(size: int, border, initials: str):
    from PIL import Image, ImageDraw
    ss = 4
    d = size * ss
    img = Image.new("RGBA", (d, d), _PANEL_HI)
    dr = ImageDraw.Draw(img)
    f = _font(size * ss // 3, bold=True)
    tw = dr.textlength(initials, font=f)
    bb = dr.textbbox((0, 0), initials, font=f)
    dr.text(((d - tw) / 2, (d - (bb[3] - bb[1])) / 2 - bb[1]), initials, font=f, fill=_GREY)
    img = _rounded(img, radius=d // 10)
    ImageDraw.Draw(img).rounded_rectangle([1, 1, d - 2, d - 2], radius=d // 10,
                                          outline=border, width=ss)
    return img.resize((size, size), Image.LANCZOS)


def _initials(name: str) -> str:
    toks = [t for t in name.split() if t]
    if not toks:
        return "?"
    if len(toks) == 1:
        return toks[0][:2].upper()
    return (toks[0][0] + toks[-1][0]).upper()


def render_artilharia_png(scorers: list[dict], top: int = _TOPN) -> bytes:
    from PIL import Image, ImageDraw

    rows = scorers[:top]
    height = _HEADER + max(len(rows), 1) * _ROW_H + _BOTTOM
    width = max(_WIDTH, height)  # mantém 1:1 quando há menos artilheiros
    img = Image.new("RGB", (width, height), _BG)
    draw = ImageDraw.Draw(img)

    # cabeçalho
    draw.text((_MARGIN, 34), "Artilharia — Copa do Mundo 2026",
              font=_font(38, bold=True), fill=_GOLD)
    draw.text((_MARGIN, 86), "Top 5 artilheiros · gols via FIFA · fotos via API-Football",
              font=_font(18), fill=_GREY)

    f_rank = _font(30, bold=True)
    f_name = _font(36, bold=True)
    f_team = _font(22)
    f_gols = _font(46, bold=True)
    f_gl = _font(18)

    for i, s in enumerate(rows):
        y = _HEADER + i * _ROW_H
        team_en = PT_TO_EN.get(_norm(s["team"]), _norm(s["team"]))
        is_br = team_en == "brazil"
        border = _BR_GREEN if is_br else _LINE

        draw.rounded_rectangle([_MARGIN, y + 5, width - _MARGIN, y + _ROW_H - 5],
                               radius=14, fill=_PANEL if i % 2 == 0 else _PANEL_HI)
        if is_br:
            draw.rounded_rectangle([_MARGIN, y + 5, _MARGIN + 7, y + _ROW_H - 5],
                                   radius=3, fill=_BR_GREEN)

        cy = y + _ROW_H // 2
        # posição
        rtxt = f"{i+1}"
        rw = draw.textlength(rtxt, font=f_rank)
        draw.text((_MARGIN + 38 - rw / 2, cy - 18), rtxt, font=f_rank,
                  fill=_GOLD if i < 3 else _GREY)

        # foto solta
        px = _MARGIN + 70
        png = photos.player_photo(s["name"], s["team"])
        avatar = (_photo_img(png, _PHOTO, border) if png
                  else _placeholder(_PHOTO, border, _initials(s["name"])))
        img.paste(avatar, (px, cy - _PHOTO // 2), avatar)

        # nome + time
        tx = px + _PHOTO + 26
        draw.text((tx, cy - 30), s["name"], font=f_name, fill=_WIN if is_br else _WHITE)
        draw.text((tx, cy + 12), s["team"], font=f_team, fill=_GREY)

        # gols à direita
        gtxt = str(s["goals"])
        right = width - _MARGIN - 28
        gw = draw.textlength(gtxt, font=f_gols)
        lw = draw.textlength("gols", font=f_gl)
        draw.text((right - gw, cy - 32), gtxt, font=f_gols, fill=_GOLD)
        draw.text((right - lw, cy + 22), "gols", font=f_gl, fill=_GREY)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
