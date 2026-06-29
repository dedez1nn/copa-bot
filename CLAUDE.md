# Copa 2026 Discord Bot

Bot de acompanhamento da Copa do Mundo 2026 com monitoramento ao vivo via FIFA API.

## Arquitetura

```
main.py              — entry point, registra cogs, sincroniza slash commands
cogs/copa.py         — comandos públicos da Copa + loop de monitoramento
cogs/selfbot_trap.py — detecção e kick automático de selfbots
cogs/fenrir.py       — configuração de canal único de comandos (/canal-fenrir)
cogs/dev.py          — comandos de teste de embed (admin only, dados falsos)
services/copa.py     — acesso à FIFA API, cache em disco, parsing de partidas
services/copa_monitor.py — lógica ao vivo: gols, cartões, VAR, escalações, períodos
services/bracket.py  — chaveamento do mata-mata (R32→Final): dados + imagem PNG (Pillow)
services/db.py       — Motor (MongoDB async); coleções: copa_channels,
                       selfbot_trap_channels, selfbot_log_channels, command_channels
services/gate.py     — cache em memória de canal permitido por guild;
                       allowed() bloqueia comandos fora do canal configurado
```

## FIFA API — endpoints usados

| Endpoint | Uso |
|---|---|
| `GET /calendar/matches?idCompetition=17&idSeason=285023&count=200` | Lista completa de partidas (cache 1h) |
| `GET /live/football/{IdMatch}` | Dados ao vivo: placar, gols, cartões, escalações (sem cache) |

`MatchDay` retorna `null` — rodadas são detectadas por clusterização de datas.

**Chaveamento**: derivado do mesmo `/calendar/matches`. As partidas do mata-mata
(`IdStage` R32=289287, R16=289288, QF=289289, SF=289290, Final=289292) trazem
`MatchNumber` (73–104), `PlaceHolderA/B` (`"1A"`, `"3ABCDF"` ou `"W74"` = venc. do jogo 74),
`Home/Away` (preenchidos quando conhecidos, com `PictureUrl` da bandeira) e `Winner`
(`IdTeam` do vencedor). A árvore inteira é montada a partir desses campos.

## Lógicas importantes

**Detecção de rodada** (`get_jogos_rodada`): agrupa datas de jogo com gap ≤ 1 dia
em clusters; retorna o cluster mais próximo de "hoje".

**Monitor ao vivo** (loop de 10s): para cada partida `inprogress`:
- Priming na primeira detecção: carrega estado atual para não re-notificar eventos passados
- Gol detectado → entra em `pending_goals` por 10min aguardando VAR
- Gol removido da API com placar diferente → anulação confirmada; mesmo placar → dado corrigido, ignora
- Cartão vermelho → mesmo padrão de pending para VAR
- Transição de período (3→4) → notifica fim/início de tempo

**Escalação**: busca 1h antes do jogo; intervalo de polling: 10s (Brasil) / 60s (outros).
Envia apenas uma vez por partida (`lineup_sent = True`).

**Resumo diário**: dispara uma vez por dia às 09:00 BRT via verificação no loop de 10s
com guard `_daily_sent_date`.

**Chaveamento pós-jogo**: ao fim de cada partida o monitor agenda o envio da imagem do
mata-mata para ~1h depois (`BRACKET_DELAY_SECS`). `_check_due_brackets` só posta se a
assinatura do chaveamento (`bracket.state_signature`) mudou desde o último envio — evita
repetir a mesma imagem na fase de grupos.

**Gate de canal** (`services/gate.py`): carregado no boot por `FenrirCog.cog_load`.
Comandos públicos chamam `gate.allowed(interaction)` antes de `defer()`.
Comandos admin (`default_permissions(administrator=True)`) são isentos.

## Variáveis de ambiente

| Variável | Descrição |
|---|---|
| `DISCORD_BOT_TOKEN` | Token do bot |
| `DISCORD_APP_ID` | Application ID |
| `MONGO_URI` | URI completo de conexão MongoDB |
| `MONGO_INITDB_DATABASE` | Nome do banco (padrão: `copa_discord`) |
