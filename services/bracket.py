"""Chaveamento do mata-mata (R32 → Final) — dados + geração de imagem.

Fonte: FIFA API (mesmo endpoint /calendar/matches usado em services.copa).
A árvore inteira é derivável de cada partida do mata-mata:
  - MatchNumber (73–104): id estável do confronto no bracket
  - PlaceHolderA/B: quem alimenta a vaga ("1A", "3ABCDF" ou "W74")
  - Home/Away: seleção real quando conhecida (com bandeira via PictureUrl)
  - Winner: IdTeam do vencedor quando o jogo termina
"""

import io
import logging
import time
import unicodedata
from pathlib import Path

from services.copa import (
    EN_TO_PT, PT_TO_EN, CACHE_DIR, _load_fifa_matches, _get_fifa, _norm,
)

logger = logging.getLogger(__name__)

# IdStage → (sigla, ordem da rodada). 289291 (3º lugar) é excluído de propósito.
KO_STAGES: dict[int, tuple[str, int]] = {
    289287: ("R32", 1),
    289288: ("R16", 2),
    289289: ("QF", 3),
    289290: ("SF", 4),
    289292: ("F", 5),
}

ROUND_LABELS = {
    "R32": "16-avos",
    "R16": "Oitavas",
    "QF": "Quartas",
    "SF": "Semifinal",
    "F": "Final",
}

_FLAG_CACHE = CACHE_DIR / "flags"
_FLAG_URL = "https://api.fifa.com/api/v3/picture/flags-sq-4/{code}"


def _team(obj: dict | None) -> dict | None:
    """Extrai dados de uma seleção do objeto Home/Away (ou None se indefinida)."""
    if not obj or not obj.get("IdTeam"):
        return None
    raw = (obj.get("TeamName") or [{}])[0].get("Description", "?")
    en = PT_TO_EN.get(_norm(raw), raw.lower())
    pt = EN_TO_PT.get(en, raw)
    # Código de 3 letras vem no fim da PictureUrl (.../flags-{format}-{size}/RSA)
    code = ""
    pic = obj.get("PictureUrl") or ""
    if "/" in pic:
        code = pic.rsplit("/", 1)[-1]
    code = code or (obj.get("Abbreviation") or "")
    return {
        "id": obj.get("IdTeam"),
        "pt": pt,
        "en": en,
        "code": code,
        "score": obj.get("Score"),
    }


def _ph_label(ph: str | None) -> str:
    """Rótulo legível de um placeholder de vaga ainda indefinida."""
    if not ph:
        return "A definir"
    if ph.startswith("W") and ph[1:].isdigit():
        return f"Venc. {ph[1:]}"
    if len(ph) >= 2 and ph[0] in "123" and ph[1:].isalpha():
        pos, grp = ph[0], ph[1:].upper()
        if len(grp) == 1:
            return f"{pos}º Grupo {grp}"
        return f"3º ({'/'.join(grp)})"
    return ph


def build_nodes() -> dict[int, dict]:
    """Retorna {MatchNumber: nó} para todas as partidas do mata-mata."""
    raw = _load_fifa_matches()
    nodes: dict[int, dict] = {}
    for m in raw:
        try:
            stage = KO_STAGES.get(int(m.get("IdStage")))
        except (TypeError, ValueError):
            stage = None
        if not stage:
            continue
        num = m.get("MatchNumber")
        if num is None:
            continue
        rnd, order = stage
        home = _team(m.get("Home"))
        away = _team(m.get("Away"))
        winner_id = m.get("Winner")
        nodes[num] = {
            "num": num,
            "round": rnd,
            "order": order,
            "home": home,
            "away": away,
            "pha": m.get("PlaceHolderA"),
            "phb": m.get("PlaceHolderB"),
            "winner_id": winner_id,
            "h_pen": m.get("HomeTeamPenaltyScore"),
            "a_pen": m.get("AwayTeamPenaltyScore"),
            "status": m.get("MatchStatus"),
            "date_ts": _parse_ts(m.get("Date")),
        }
    return nodes


def _parse_ts(s: str | None) -> int:
    from datetime import datetime, timezone
    if not s:
        return 0
    try:
        return int(datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")
                   .replace(tzinfo=timezone.utc).timestamp())
    except Exception:
        return 0


def children(nodes: dict[int, dict], num: int) -> list[int]:
    """MatchNumbers que alimentam o confronto `num` (via 'W##' nos placeholders)."""
    node = nodes.get(num)
    if not node:
        return []
    kids = []
    for ph in (node["pha"], node["phb"]):
        if ph and ph.startswith("W") and ph[1:].isdigit():
            n = int(ph[1:])
            if n in nodes:
                kids.append(n)
    return kids


def _lookup_team(nodes: dict[int, dict], team_id: str) -> dict | None:
    """Localiza o objeto da seleção pelo IdTeam em qualquer nó (R32 traz todas)."""
    if not team_id:
        return None
    for nn in nodes.values():
        for t in (nn["home"], nn["away"]):
            if t and t["id"] == team_id:
                return t
    return None


def _code(team: dict) -> str:
    return (team.get("code") or team["pt"][:3]).title()


def _slot_abbrev(nodes: dict[int, dict], num: int, side: str) -> str:
    """Abreviação curta de um lado de um confronto.

    Time conhecido → sigla (ex.: "Bra"). Vaga vinda de um confronto cujos dois
    participantes já são conhecidos → par "Bra/Jpn". Caso contrário (profundo
    demais) → marcador compacto "V##".
    """
    node = nodes.get(num)
    if not node:
        return "?"
    team, _ = slot_text(nodes, num, side)  # resolve a cadeia de vencedores
    if team:
        return _code(team)
    ph = node["pha"] if side == "A" else node["phb"]
    if ph and ph.startswith("W") and ph[1:].isdigit():
        fn = int(ph[1:])
        if fn in nodes:
            ta, _ = slot_text(nodes, fn, "A")
            tb, _ = slot_text(nodes, fn, "B")
            if ta and tb:
                return f"{_code(ta)}/{_code(tb)}"
        return f"V{ph[1:]}"
    if ph and len(ph) >= 2 and ph[0] in "123" and ph[1:].isalpha():
        return f"{ph[0]}{ph[1].upper()}" if len(ph) == 2 else f"{ph[0]}º"
    return ph or "?"


def slot_text(nodes: dict[int, dict], num: int, side: str) -> tuple[dict | None, str]:
    """(team|None, rótulo) de um lado do confronto.

    side: 'A' (Home/PlaceHolderA) ou 'B' (Away/PlaceHolderB).
    Resolve 'W##' para o vencedor já conhecido (em qualquer profundidade).
    """
    node = nodes[num]
    team = node["home"] if side == "A" else node["away"]
    ph = node["pha"] if side == "A" else node["phb"]
    if team:
        return team, team["pt"]
    # vaga ainda indefinida — segue a cadeia de vencedores dos confrontos-pai
    while ph and ph.startswith("W") and ph[1:].isdigit():
        pn = nodes.get(int(ph[1:]))
        if not pn or not pn.get("winner_id"):
            break
        t = _lookup_team(nodes, pn["winner_id"])
        if t:
            return t, t["pt"]
        break
    # vaga indefinida alimentada por um confronto (W##): mostra os participantes dele
    if ph and ph.startswith("W") and ph[1:].isdigit():
        fn = int(ph[1:])
        if fn in nodes:
            a = _slot_abbrev(nodes, fn, "A")
            b = _slot_abbrev(nodes, fn, "B")
            return None, f"Venc {a} x {b}"
    return None, _ph_label(ph)


# ── Bandeiras ─────────────────────────────────────────────────────────────────

def _flag_png(code: str) -> bytes | None:
    if not code:
        return None
    _FLAG_CACHE.mkdir(parents=True, exist_ok=True)
    path = _FLAG_CACHE / f"{code}.png"
    if path.exists() and path.stat().st_size > 0:
        return path.read_bytes()
    import urllib.request
    url = _FLAG_URL.format(code=code)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = r.read()
        if data:
            path.write_bytes(data)
            return data
    except Exception as e:
        logger.warning("[bracket] falha ao baixar bandeira %s: %s", code, e)
    return None


# ── Renderização (Pillow) ─────────────────────────────────────────────────────

# Layout — duas metades empilhadas verticalmente (imagem em retrato).
# Colunas: R32, R16, QF, SF, Final (5 ao todo); as duas metades partilham as
# mesmas 4 primeiras colunas e convergem na Final (coluna 4), à direita.
_BOX_W = 190
_BOX_H = 50
_COL_GAP = 36
_LEAF_VGAP = 18
_HALF_GAP = 44          # espaço vertical entre a metade de cima e a de baixo
_MARGIN_X = 36
_TOP = 128
_BOTTOM = 36
_FLAG = 22

# Cores (tema escuro)
_BG = (14, 16, 23)
_PANEL = (28, 33, 49)
_PANEL_HI = (37, 44, 66)
_LINE = (64, 72, 96)
_WHITE = (236, 239, 246)
_GREY = (123, 131, 150)
_WIN = (74, 201, 126)
_LIVE = (235, 73, 63)
_GOLD = (255, 205, 70)

_ASSETS = Path(__file__).resolve().parents[1] / "assets"
_ASSETS_FONTS = _ASSETS / "fonts"
_BG_IMAGE = _ASSETS / "bg_chaveamento.jpg"   # fundo padrão (se existir)

# A fonte empacotada vem primeiro para garantir acentos em qualquer ambiente
# (o Discloud não tem o DejaVu instalado nos caminhos do sistema).
_FONT_PATHS = [
    str(_ASSETS_FONTS / "DejaVuSans.ttf"),
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
]
_FONT_BOLD_PATHS = [
    str(_ASSETS_FONTS / "DejaVuSans-Bold.ttf"),
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
]


def _font(size: int, bold: bool = False):
    from PIL import ImageFont
    for p in (_FONT_BOLD_PATHS if bold else _FONT_PATHS):
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def _col_index(order: int) -> int:
    """Coluna 0..3 a partir da ordem da rodada (R32→0 … SF→3). Final é a coluna 4."""
    return order - 1


def _trunc(draw, text: str, font, max_w: int) -> str:
    if draw.textlength(text, font=font) <= max_w:
        return text
    while text and draw.textlength(text + "…", font=font) > max_w:
        text = text[:-1]
    return text + "…"


def _make_canvas(width: int, height: int, bg_path: str | None,
                 blur: float, darken: float):
    """Canvas base: imagem de fundo (cover + desfoque + escurecimento) ou cor sólida."""
    from PIL import Image, ImageFilter

    if bg_path and Path(bg_path).exists():
        try:
            bg = Image.open(bg_path).convert("RGB")
            # cover: escala para preencher o canvas e recorta o excesso (centralizado)
            scale = max(width / bg.width, height / bg.height)
            nw, nh = round(bg.width * scale), round(bg.height * scale)
            bg = bg.resize((nw, nh), Image.LANCZOS)
            left, top = (nw - width) // 2, (nh - height) // 2
            bg = bg.crop((left, top, left + width, top + height))
            if blur > 0:
                bg = bg.filter(ImageFilter.GaussianBlur(blur))
            if darken > 0:
                bg = Image.blend(bg, Image.new("RGB", (width, height), (0, 0, 0)),
                                 min(max(darken, 0.0), 1.0))
            return bg
        except Exception as e:
            logger.warning("[bracket] falha ao usar fundo %s: %s", bg_path, e)
    return Image.new("RGB", (width, height), _BG)


def render_bracket_png(bg_path: str | None = None, bg_blur: float = 12,
                       bg_dark: float = 0.6) -> bytes:
    """Renderiza o chaveamento do mata-mata (R32→Final) e retorna PNG em bytes.

    bg_path: imagem de fundo. Se None, usa o fundo empacotado (_BG_IMAGE) quando
    existir; caso contrário, fundo sólido escuro. bg_blur/bg_dark controlam o
    desfoque (px) e o escurecimento (0–1) aplicados ao fundo para manter o
    chaveamento legível.
    """
    from PIL import Image, ImageDraw

    if bg_path is None and _BG_IMAGE.exists():
        bg_path = str(_BG_IMAGE)

    nodes = build_nodes()
    if 104 not in nodes:
        raise RuntimeError("dados do mata-mata indisponíveis na API")

    # Duas metades empilhadas: a de cima (lado esquerdo, 101) começa no topo;
    # a de baixo (lado direito, 102) começa abaixo dela, separada por _HALF_GAP.
    # Ambas fluem da esquerda (R32) para a direita (SF) e convergem na Final.
    pos: dict[int, tuple[int, int]] = {}
    slot = {"L": 0, "R": 0}
    leaf_h = _BOX_H + _LEAF_VGAP

    def place(num: int, side: str, base: float) -> float:
        node = nodes[num]
        kids = children(nodes, num)
        x = _MARGIN_X + _col_index(node["order"]) * (_BOX_W + _COL_GAP)
        if not kids:
            s = slot[side]
            slot[side] += 1
            y = base + s * leaf_h + _BOX_H / 2
        else:
            ys = [place(k, side, base) for k in kids]
            y = sum(ys) / len(ys)
        pos[num] = (x, y)
        return y

    yl = place(101, "L", _TOP)
    n_left = slot["L"]
    r_base = _TOP + n_left * leaf_h + _HALF_GAP
    yr = place(102, "R", r_base)
    # Final na coluna 4 (à direita), no meio vertical entre as duas semifinais
    fx = _MARGIN_X + 4 * (_BOX_W + _COL_GAP)
    pos[104] = (fx, (yl + yr) / 2)

    total_leaves = n_left + slot["R"]
    width = _MARGIN_X * 2 + 5 * _BOX_W + 4 * _COL_GAP
    height = int(_TOP + total_leaves * leaf_h + _HALF_GAP - _LEAF_VGAP + _BOTTOM)

    img = _make_canvas(width, height, bg_path, bg_blur, bg_dark)
    draw = ImageDraw.Draw(img)

    f_title = _font(36, bold=True)
    f_sub = _font(17)
    f_head = _font(18, bold=True)
    f_name = _font(15)
    f_name_b = _font(15, bold=True)
    f_score = _font(16, bold=True)
    f_ph = _font(13)

    # Título
    draw.text((_MARGIN_X, 26), "Chaveamento — Copa do Mundo 2026", font=f_title, fill=_GOLD)
    draw.text((_MARGIN_X, 68), "Mata-mata · atualizado via FIFA", font=f_sub, fill=_GREY)

    # Cabeçalhos de coluna (compartilhados pelas duas metades)
    col_rounds = ["R32", "R16", "QF", "SF", "F"]
    for i, rnd in enumerate(col_rounds):
        cx = _MARGIN_X + i * (_BOX_W + _COL_GAP)
        label = ROUND_LABELS[rnd]
        w = draw.textlength(label, font=f_head)
        draw.text((cx + (_BOX_W - w) / 2, _TOP - 30), label, font=f_head, fill=_WHITE)

    # Conectores (desenhados antes das caixas): direita do filho → esquerda do pai
    for num, node in nodes.items():
        kids = children(nodes, num)
        if not kids:
            continue
        tx, ty = pos[num]            # borda esquerda do pai
        for k in kids:
            kx, ky = pos[k]
            cxr = kx + _BOX_W        # borda direita do filho
            midx = (cxr + tx) / 2
            draw.line([(cxr, ky), (midx, ky), (midx, ty), (tx, ty)], fill=_LINE, width=2)

    # Caixas
    for num, node in nodes.items():
        _draw_box(img, draw, node, nodes, pos[num],
                  fonts=(f_name, f_name_b, f_score, f_ph))

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _draw_box(img, draw, node, nodes, xy, fonts):
    from PIL import Image
    f_name, f_name_b, f_score, f_ph = fonts
    x, yc = xy
    x = int(x); yc = int(yc)
    top = yc - _BOX_H // 2
    is_live = node.get("status") == 3
    bg = _PANEL_HI if is_live else _PANEL
    border = _LIVE if is_live else _LINE
    draw.rounded_rectangle([x, top, x + _BOX_W, top + _BOX_H], radius=8,
                           fill=bg, outline=border, width=2 if is_live else 1)
    # linha divisória entre os dois lados
    midy = top + _BOX_H // 2
    draw.line([(x + 6, midy), (x + _BOX_W - 6, midy)], fill=_BG, width=1)

    for i, sd in enumerate(("A", "B")):
        team, label = slot_text(nodes, node["num"], sd)
        row_top = top + i * (_BOX_H // 2)
        ry = row_top + (_BOX_H // 2) // 2
        is_winner = bool(team and node.get("winner_id") and team["id"] == node["winner_id"])

        tx = x + 8
        if team and team.get("code"):
            png = _flag_png(team["code"])
            if png:
                try:
                    fl = Image.open(io.BytesIO(png)).convert("RGBA").resize((_FLAG, _FLAG))
                    img.paste(fl, (tx, ry - _FLAG // 2), fl)
                except Exception:
                    pass
            tx += _FLAG + 8
        else:
            tx += 2

        # placar à direita
        score_txt = ""
        if team and team.get("score") is not None and node.get("status") in (0, 3):
            score_txt = str(team["score"])
            pen = node["h_pen"] if sd == "A" else node["a_pen"]
            if pen is not None:
                score_txt += f" ({pen})"
        name_color = _WIN if is_winner else (_WHITE if team else _GREY)
        name_font = f_name_b if is_winner else (f_name if team else f_ph)

        score_w = draw.textlength(score_txt, font=f_score) if score_txt else 0
        max_name_w = (x + _BOX_W - 10) - tx - (score_w + 8 if score_txt else 0)
        name = _trunc(draw, label, name_font, int(max_name_w))
        # centraliza verticalmente o texto
        bbox = draw.textbbox((0, 0), name, font=name_font)
        th = bbox[3] - bbox[1]
        draw.text((tx, ry - th // 2 - bbox[1]), name, font=name_font, fill=name_color)
        if score_txt:
            sw = draw.textlength(score_txt, font=f_score)
            draw.text((x + _BOX_W - 10 - sw, ry - 8), score_txt, font=f_score,
                      fill=_WIN if is_winner else _WHITE)
