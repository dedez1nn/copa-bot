"""Testes do gatilho de fim de rodada (chaveamento + artilharia).

Sem rede/Discord: dirige `CopaCog._check_end_of_day_bracket` com jogos sintéticos
e verifica quando o envio é armado/disparado. Foco no bug de jogos que terminam
após a meia-noite BRT (prorrogação/pênaltis). Rode com:

    PYTHONPATH=. python tests/test_eod_bracket.py
"""

import asyncio
import sys
import types
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Stub do `motor` (driver MongoDB) — não usado neste teste, só importado por services.db.
if "motor.motor_asyncio" not in sys.modules:
    _m = types.ModuleType("motor")
    _ma = types.ModuleType("motor.motor_asyncio")
    _ma.AsyncIOMotorClient = object
    _ma.AsyncIOMotorDatabase = object
    _m.motor_asyncio = _ma
    sys.modules["motor"] = _m
    sys.modules["motor.motor_asyncio"] = _ma

import services.copa as copa_svc
from cogs.copa import CopaCog, BRACKET_EOD_DELAY_SECS

BRT = copa_svc.BRT


def _ts(y, mo, d, h, mi=0) -> float:
    return datetime(y, mo, d, h, mi, tzinfo=BRT).timestamp()


def _make_cog():
    cog = CopaCog(bot=None)
    cog._monitor_channels = [(1, 1)]
    sent: list[bool] = []

    async def _fake_send():
        sent.append(True)

    cog._send_eod_bracket = _fake_send  # type: ignore[assignment]
    return cog, sent


def _run(cog, now: float):
    """Executa o check com `time.time()` fixado em `now`."""
    import cogs.copa as cc
    orig = cc.time.time
    cc.time.time = lambda: now
    try:
        asyncio.run(cog._check_end_of_day_bracket())
    finally:
        cc.time.time = orig


def _patch_jogos(jogos: list[dict]):
    copa_svc.get_jogos_rodada = lambda: jogos  # type: ignore[assignment]


def _check(cond: bool, msg: str):
    print(("  ✓ " if cond else "  ✗ FALHOU: ") + msg)
    if not cond:
        raise SystemExit(1)


# ── Cenário 1: jogo começa de noite e termina após a meia-noite ────────────────
def test_midnight_rollover():
    print("test_midnight_rollover")
    # Jogo de 29/06 começou 22:00, terminou ~00:54 de 30/06.
    jogos = [
        {"date_ts": _ts(2026, 6, 29, 18, 0), "status": "finished"},
        {"date_ts": _ts(2026, 6, 29, 22, 0), "status": "finished"},
        # Jogo futuro de 02/07 (dentro da janela da rodada), ainda não começou.
        {"date_ts": _ts(2026, 7, 2, 16, 0), "status": "notstarted"},
    ]
    _patch_jogos(jogos)
    cog, sent = _make_cog()

    now = _ts(2026, 6, 30, 0, 54)  # já passou da meia-noite
    _run(cog, now)
    _check(cog._eod_armed_date == "2026-06-29",
           "armou para 2026-06-29 (data de início), não para o relógio (30/06)")
    _check(abs(cog._eod_due_ts - (now + BRACKET_EOD_DELAY_SECS)) < 1,
           "due 1h após o disparo")

    # Antes do prazo: não envia.
    _run(cog, now + 1800)
    _check(not sent, "não envia antes de 1h")

    # Após o prazo: envia uma vez e marca a data.
    _run(cog, now + BRACKET_EOD_DELAY_SECS + 1)
    _check(sent == [True], "enviou uma vez após 1h")
    _check("2026-06-29" in cog._eod_sent_dates and cog._eod_armed_date == "",
           "data marcada e desarmada")

    # Re-armar/re-enviar a mesma data não deve ocorrer.
    _run(cog, now + BRACKET_EOD_DELAY_SECS + 60)
    _check(sent == [True] and cog._eod_armed_date == "",
           "não rearma data já enviada")


# ── Cenário 2: dia em andamento não arma ───────────────────────────────────────
def test_dia_em_andamento_nao_arma():
    print("test_dia_em_andamento_nao_arma")
    jogos = [
        {"date_ts": _ts(2026, 6, 30, 13, 0), "status": "finished"},
        {"date_ts": _ts(2026, 6, 30, 16, 0), "status": "inprogress"},
    ]
    _patch_jogos(jogos)
    cog, sent = _make_cog()
    _run(cog, _ts(2026, 6, 30, 17, 0))
    _check(cog._eod_armed_date == "", "não arma com jogo ainda em andamento")
    _check(not sent, "nada enviado")


# ── Cenário 3: todos do dia encerrados (mesmo dia) ─────────────────────────────
def test_mesmo_dia_arma():
    print("test_mesmo_dia_arma")
    jogos = [
        {"date_ts": _ts(2026, 6, 30, 13, 0), "status": "finished"},
        {"date_ts": _ts(2026, 6, 30, 16, 0), "status": "finished"},
    ]
    _patch_jogos(jogos)
    cog, sent = _make_cog()
    _run(cog, _ts(2026, 6, 30, 18, 30))
    _check(cog._eod_armed_date == "2026-06-30", "armou para o dia corrente")


# ── Cenário 4: dia futuro ainda não iniciado é ignorado ────────────────────────
def test_dia_futuro_ignorado():
    print("test_dia_futuro_ignorado")
    jogos = [
        {"date_ts": _ts(2026, 6, 29, 16, 0), "status": "finished"},
        {"date_ts": _ts(2026, 7, 1, 16, 0), "status": "notstarted"},
    ]
    _patch_jogos(jogos)
    cog, sent = _make_cog()
    # 30/06 de manhã: 29/06 já terminou; arma para 29/06 (mais recente já iniciado).
    _run(cog, _ts(2026, 6, 30, 9, 0))
    _check(cog._eod_armed_date == "2026-06-29",
           "ignora dia futuro e arma a data iniciada mais recente")


if __name__ == "__main__":
    test_midnight_rollover()
    test_dia_em_andamento_nao_arma()
    test_mesmo_dia_arma()
    test_dia_futuro_ignorado()
    print("\nTodos os testes passaram ✅")
