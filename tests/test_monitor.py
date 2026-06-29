"""Testes do monitor ao vivo (VAR, disputa de pênaltis, prorrogação).

Sem rede: dirige `services.copa_monitor` com dados sintéticos da FIFA API e
verifica as mensagens que o bot enviaria. Rode com:

    PYTHONPATH=. python tests/test_monitor.py
"""

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import services.copa_monitor as cm

# ── captura de mensagens enviadas ──────────────────────────────────────────────
_sent: list[tuple[str | None, str | None]] = []


async def _fake_send_all(bot, channels, **kw):
    e = kw.get("embed")
    if e is not None:
        _sent.append((getattr(e, "title", None), getattr(e, "description", None)))


cm._send_all = _fake_send_all

M = {
    "home_pt": "Brasil", "away_pt": "Argentina",
    "home_en": "brazil", "away_en": "argentina",
    "fifa_id": "T", "stage_id": 1, "date_ts": 0, "status": "inprogress",
    "home_score": 0, "away_score": 0, "group": "", "stage": "Final",
}

PLAYERS = [
    {"IdPlayer": "10", "ShortName": [{"Locale": "pt-BR", "Description": "Neymar"}]},
    {"IdPlayer": "11", "ShortName": [{"Locale": "pt-BR", "Description": "Vini Jr"}]},
    {"IdPlayer": "20", "ShortName": [{"Locale": "pt-BR", "Description": "Messi"}]},
]


# ── construtores de payload ─────────────────────────────────────────────────────
def G(pid, minute, period=5, gtype=0):
    return {"IdPlayer": pid, "Minute": minute, "Period": period, "Type": gtype}


def B(pid, minute, card):
    return {"IdPlayer": pid, "Minute": minute, "Card": card}


def live(hg, ag, hb=None, ab=None, hs=None, as_=None, period=5):
    return {
        "Period": period, "MatchStatus": 3,
        "HomeTeam": {"IdTeam": "H", "Score": hs if hs is not None else len(hg),
                     "Goals": hg, "Bookings": hb or [], "Players": PLAYERS},
        "AwayTeam": {"IdTeam": "A", "Score": as_ if as_ is not None else len(ag),
                     "Goals": ag, "Bookings": ab or [], "Players": PLAYERS},
    }


def tl(events):
    return events


# ── harness ─────────────────────────────────────────────────────────────────────
def _fresh():
    cm._watch.pop("T", None)
    st = cm._state("T")
    st.update({"kicked_off": True, "primed": True, "last_period": 5,
               "2ht_sent": True, "ht_sent": True, "kickoff_notified": True,
               "home_team_id": "H", "away_team_id": "A"})
    return st


def _tick_live(st, data):
    cm._load_fifa_live = lambda mid, _d=data: _d
    asyncio.run(cm._check_fifa_live(None, [(1, 1)], M, st))


def _tick_timeline(st, events):
    cm._load_fifa_timeline = lambda m, _e=events: _e
    asyncio.run(cm._check_fifa_timeline(None, [(1, 1)], M, st))


def _texts():
    return [f"{t or ''} {d or ''}" for t, d in _sent]


_failures = 0


def check(name, cond):
    global _failures
    status = "PASS" if cond else "FAIL"
    if not cond:
        _failures += 1
    print(f"  [{status}] {name}")


def has(substr):
    return any(substr in txt for txt in _texts())


def count(substr):
    return sum(1 for txt in _texts() if substr in txt)


# ── testes de VAR ───────────────────────────────────────────────────────────────
def test_var_gol_anulado():
    _sent.clear(); st = _fresh()
    _tick_live(st, live([G("10", "10'")], []))
    _tick_live(st, live([], [], hs=0))
    check("A) gol anunciado", has("GOL! Neymar"))
    check("A) gol anulado pelo VAR", has("Gol anulado pelo VAR"))


def test_var_correcao_minuto():
    _sent.clear(); st = _fresh()
    _tick_live(st, live([G("10", "10'")], []))
    _tick_live(st, live([G("10", "11'")], []))
    check("B) gol anunciado uma vez", count("GOL! Neymar") == 1)
    check("B) sem anulação na correção", not has("anulado"))


def test_var_remocao_sem_queda():
    _sent.clear(); st = _fresh()
    _tick_live(st, live([G("10", "10'")], []))
    _tick_live(st, live([G("10", "10'"), G("11", "20'")], []))
    _tick_live(st, live([G("11", "20'")], []))
    check("C) sem anulação (placar não caiu)", not has("anulado"))
    check("C) segundo gol anunciado", has("GOL! Vini Jr"))


def test_var_vermelho_revertido():
    _sent.clear(); st = _fresh()
    _tick_live(st, live([], [], hb=[B("10", "30'", 2)]))
    _tick_live(st, live([], [], hb=[]))
    check("D) vermelho anunciado", has("🟥 **Neymar**"))
    check("D) vermelho revertido", has("Vermelho revertido pelo VAR"))


def test_var_vermelho_para_amarelo():
    _sent.clear(); st = _fresh()
    _tick_live(st, live([], [], hb=[B("10", "30'", 2)]))
    _tick_live(st, live([], [], hb=[B("10", "30'", 1)]))
    check("E) vermelho revisto para amarelo", has("revisto para amarelo pelo VAR"))


def test_var_gol_confirmado():
    _sent.clear(); st = _fresh()
    _tick_live(st, live([G("10", "10'")], []))
    for k in st["pending_goals"]:
        st["pending_goals"][k]["ts"] = time.time() - (cm.VAR_WINDOW_SECS + 100)
    _tick_live(st, live([G("10", "10'")], []))
    check("F) gol confirmado sem anular", count("GOL! Neymar") == 1 and not has("anulado"))


# ── testes de prorrogação ───────────────────────────────────────────────────────
def test_prorrogacao():
    _sent.clear(); st = _fresh()
    _tick_live(st, live([], [], period=7))
    _tick_live(st, live([], [], period=9))
    check("prorrogação 1º tempo", has("Vamos à Prorrogação"))
    check("prorrogação 2º tempo", has("2º Tempo da Prorrogação"))


# ── testes da disputa de pênaltis ───────────────────────────────────────────────
def test_disputa_abertura_e_gols_nao_duplicados():
    _sent.clear(); st = _fresh()
    # entra na disputa; pênaltis convertidos chegam como Goals Period 11
    _tick_live(st, live([G("10", "", period=11, gtype=1)], [], hs=1, as_=1, period=11))
    check("abertura da disputa", has("DECISION DE PENAL"))
    check("pênalti convertido NÃO vira 'GOL!' normal", not has("GOL!"))


def test_disputa_timeline_placar():
    _sent.clear(); st = _fresh()
    st["last_period"] = 11
    st["shootout_announced"] = True
    events = tl([
        {"EventId": "e1", "Type": 41, "Period": 11, "IdTeam": "A", "IdPlayer": "20", "MatchMinute": ""},
        {"EventId": "e2", "Type": 60, "Period": 11, "IdTeam": "H", "IdPlayer": "10", "MatchMinute": ""},
        {"EventId": "e3", "Type": 41, "Period": 11, "IdTeam": "H", "IdPlayer": "11", "MatchMinute": ""},
    ])
    _tick_timeline(st, events)
    check("convertido com bola verde 🟢", has("🟢") and has("converteu"))
    check("perdido com ❌", has("❌") and has("perdeu"))
    check("placar parcial 0-1", has("0 — 1"))
    check("placar final 1-1", has("1 — 1"))
    check("contadores corretos", st["shootout_home"] == 1 and st["shootout_away"] == 1)


def test_disputa_embed_final():
    home = {"IdTeam": "H", "Score": 1,
            "Goals": [G("10", "54'", period=5), G("11", "", period=11, gtype=1)],
            "Bookings": [], "Players": PLAYERS}
    away = {"IdTeam": "A", "Score": 1,
            "Goals": [G("20", "42'", period=3),
                      G("20", "", period=11, gtype=1), G("11", "", period=11, gtype=1)],
            "Bookings": [], "Players": PLAYERS}
    data = {"HomeTeam": home, "AwayTeam": away}
    emb = cm.build_final_embed(M, 1, 1, data)
    fields = {f.name: f.value for f in emb.fields}
    pen = fields.get("🥅 Decisão por Pênaltis", "")
    check("campo de pênaltis presente", any("Decisão por Pênaltis" in k for k in fields))
    check("placar da disputa 1-2", "1 — 2" in pen)
    check("classificado correto (Argentina)", "Argentina" in pen and "classifica" in pen)
    gols = fields.get("⚽ Gols", "")
    check("gols do jogo sem os pênaltis (Period 11)", "54'" in gols and "42'" in gols
          and gols.count("⚽") == 2)


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        print(f"\n### {t.__name__}")
        t()
    print(f"\n{'='*40}")
    if _failures:
        print(f"FALHOU: {_failures} verificação(ões)")
        sys.exit(1)
    print("OK: todos os testes passaram")


if __name__ == "__main__":
    main()
