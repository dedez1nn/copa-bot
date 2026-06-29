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

from services import copa as copa_svc
from services.copa import (
    EN_TO_PT, PT_TO_EN, CACHE_DIR, _load_fifa_matches, _get_fifa, _norm,
)

logger = logging.getLogger(__name__)

# Overrides de simulação (apenas para teste via /avancar): {MatchNumber: IdTeam vencedor}
_overrides: dict[int, str] = {}

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
    # Aplica overrides de simulação (vencedores forçados via /avancar)
    for num, winner_id in _overrides.items():
        if num in nodes:
            nodes[num]["winner_id"] = winner_id
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


def state_signature(nodes: dict[int, dict] | None = None) -> str:
    """Assinatura do estado do chaveamento (muda quando avança um confronto)."""
    if nodes is None:
        nodes = build_nodes()
    parts = []
    for num in sorted(nodes):
        n = nodes[num]
        h = n["home"]["id"] if n["home"] else "-"
        a = n["away"]["id"] if n["away"] else "-"
        parts.append(f"{num}:{h}:{a}:{n.get('winner_id') or '-'}:"
                     f"{(n['home'] or {}).get('score')}-{(n['away'] or {}).get('score')}")
    return "|".join(parts)


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

# Layout
_BOX_W = 198
_BOX_H = 50
_COL_GAP = 48
_LEAF_VGAP = 14
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

_FONT_PATHS = [
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
]
_FONT_BOLD_PATHS = [
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


def _col_index(order: int, side: str) -> int:
    """Coluna 0..8 a partir da ordem da rodada e do lado (L/R/C)."""
    if side == "C":          # Final
        return 4
    if side == "L":
        return order - 1     # R32→0, R16→1, QF→2, SF→3
    return 9 - order         # R32→8, R16→7, QF→6, SF→5


def _assign_sides(nodes: dict[int, dict]) -> dict[int, str]:
    sides: dict[int, str] = {104: "C"}

    def walk(num, side):
        sides[num] = side
        for k in children(nodes, num):
            walk(k, side)

    for k in children(nodes, 101):
        walk(k, "L")
    sides[101] = "L"
    for k in children(nodes, 102):
        walk(k, "R")
    sides[102] = "R"
    return sides


def _trunc(draw, text: str, font, max_w: int) -> str:
    if draw.textlength(text, font=font) <= max_w:
        return text
    while text and draw.textlength(text + "…", font=font) > max_w:
        text = text[:-1]
    return text + "…"


def render_bracket_png() -> bytes:
    """Renderiza o chaveamento do mata-mata (R32→Final) e retorna PNG em bytes."""
    from PIL import Image, ImageDraw

    nodes = build_nodes()
    if 104 not in nodes:
        raise RuntimeError("dados do mata-mata indisponíveis na API")

    sides = _assign_sides(nodes)

    # Posições verticais: percorre cada lado atribuindo slots às folhas (R32).
    pos: dict[int, tuple[int, int]] = {}
    slot = {"L": 0, "R": 0}
    leaf_h = _BOX_H + _LEAF_VGAP

    def place(num: int, side: str) -> float:
        node = nodes[num]
        kids = children(nodes, num)
        x = _MARGIN_X + _col_index(node["order"], side) * (_BOX_W + _COL_GAP)
        if not kids:
            s = slot[side]
            slot[side] += 1
            y = _TOP + s * leaf_h + _BOX_H / 2
        else:
            ys = [place(k, side) for k in kids]
            y = sum(ys) / len(ys)
        pos[num] = (x, y)
        return y

    yl = place(101, "L")
    yr = place(102, "R")
    # Final no centro, entre as duas semifinais
    fx = _MARGIN_X + _col_index(5, "C") * (_BOX_W + _COL_GAP)
    pos[104] = (fx, (yl + yr) / 2)

    total_leaves = max(slot["L"], slot["R"])
    width = _MARGIN_X * 2 + 9 * _BOX_W + 8 * _COL_GAP
    height = int(_TOP + total_leaves * leaf_h - _LEAF_VGAP + _BOTTOM)

    img = Image.new("RGB", (width, height), _BG)
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

    # Cabeçalhos de coluna
    col_rounds = [("R32", "L"), ("R16", "L"), ("QF", "L"), ("SF", "L"),
                  ("F", "C"), ("SF", "R"), ("QF", "R"), ("R16", "R"), ("R32", "R")]
    for i, (rnd, _s) in enumerate(col_rounds):
        cx = _MARGIN_X + i * (_BOX_W + _COL_GAP)
        label = ROUND_LABELS[rnd]
        w = draw.textlength(label, font=f_head)
        draw.text((cx + (_BOX_W - w) / 2, _TOP - 30), label, font=f_head, fill=_WHITE)

    # Conectores (desenhados antes das caixas)
    def edge_right(num):
        x, y = pos[num]
        return x + _BOX_W, y

    def edge_left(num):
        x, y = pos[num]
        return x, y

    for num, node in nodes.items():
        kids = children(nodes, num)
        if not kids:
            continue
        side = sides[num]
        px, py = pos[num]
        if side == "L" or (side == "C"):
            # Para Final, trata cada filho conforme o lado dele
            pass
        for k in kids:
            kside = sides[k]
            if kside == "L":
                cxr, cyr = edge_right(k)
                tx, ty = edge_left(num)
                midx = (cxr + tx) / 2
                draw.line([(cxr, cyr), (midx, cyr), (midx, ty), (tx, ty)], fill=_LINE, width=2)
            else:  # R
                cxl, cyl = edge_left(k)
                tx, ty = edge_right(num)
                midx = (cxl + tx) / 2
                draw.line([(cxl, cyl), (midx, cyl), (midx, ty), (tx, ty)], fill=_LINE, width=2)

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



# ── Simulação de avanço (teste) ───────────────────────────────────────────────

def _find_team_id(nodes: dict[int, dict], query: str) -> tuple[str, str] | None:
    """Resolve um nome de seleção para (IdTeam, nome_pt) varrendo o chaveamento."""
    en = copa_svc._resolve(query)
    qn = _norm(query)
    for node in nodes.values():
        for team in (node["home"], node["away"]):
            if not team:
                continue
            if team["en"] == en or _norm(team["pt"]) == _norm(EN_TO_PT.get(en, en)):
                return team["id"], team["pt"]
    # fallback: casamento parcial por nome
    for node in nodes.values():
        for team in (node["home"], node["away"]):
            if team and (qn in _norm(team["pt"]) or _norm(team["pt"]) in qn):
                return team["id"], team["pt"]
    return None


def _current_match(nodes: dict[int, dict], team_id: str) -> int | None:
    """Confronto indefinido mais próximo (rodada mais baixa) em que a seleção está."""
    for order in (1, 2, 3, 4, 5):
        for num, node in nodes.items():
            if node["order"] != order or node.get("winner_id"):
                continue
            for sd in ("A", "B"):
                team, _ = slot_text(nodes, num, sd)
                if team and team["id"] == team_id:
                    return num
    return None


def advance_team(query: str) -> tuple[bool, str]:
    """Avança uma seleção uma rodada no chaveamento simulado (override em memória)."""
    nodes = build_nodes()
    found = _find_team_id(nodes, query)
    if not found:
        return False, f"❌ Seleção **{query}** não encontrada no chaveamento."
    team_id, pt = found

    # Já é campeã?
    final = nodes.get(104)
    if final and final.get("winner_id") == team_id:
        return False, f"🏆 **{pt}** já é campeã da simulação. Use `/avancar-reset` para recomeçar."

    num = _current_match(nodes, team_id)
    if num is None:
        return False, (f"🚫 **{pt}** não está mais no chaveamento "
                       f"(foi eliminada ou ainda não se classificou).")

    _overrides[num] = team_id
    node = nodes[num]
    next_label = "campeã 🏆" if node["order"] == 5 else ROUND_LABELS.get(
        {1: "R16", 2: "QF", 3: "SF", 4: "F"}.get(node["order"]), "próxima fase")
    if node["order"] == 5:
        return True, f"🏆 **{pt}** venceu a final e é a **campeã** da simulação!"
    return True, f"✅ **{pt}** avançou para **{next_label}** (venceu o confronto #{num})."


def reset_overrides() -> None:
    """Limpa a simulação de avanço, voltando ao estado real da API."""
    _overrides.clear()
