"""
VigiliaPL — Runner Automatizado
================================
Ponto de entrada para execucao sem interacao com usuario.
Projetado para ser chamado por agendadores: cron, Task Scheduler,
AWS EventBridge, Airflow, etc.

Uso:
    python runner.py                  # execucao incremental (padrao)
    python runner.py --fonte camara   # so Camara
    python runner.py --fonte senado   # so Senado
    python runner.py --dias 7         # janela manual de 7 dias

Fluxo de execucao:
    1. Carrega configuracoes (settings.yaml + keywords.yaml)
    2. Determina janela de datas (checkpoint ou --dias)
    3. Coleta proposicoes das APIs
    4. Enriquece com temas e autores (top N por performance)
    5. Aplica scoring de relevancia
    6. Persiste no banco SQLite (idempotente)
    7. Dispara alertas para Alta relevancia nao notificada
    8. Atualiza checkpoint e registra log de execucao
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml

from src.alerter import Alerter
from src.fetchers import FetcherCamara, FetcherSenado
from src.models import FonteDados, LogExecucao
from src.scoring import ScoringEngine
from src.storage import Storage

# ─── Configuracao de logging ──────────────────────────────────────────

def configurar_logging(nivel: str = "INFO", arquivo: str = "logs/execucao.log") -> None:
    Path(arquivo).parent.mkdir(parents=True, exist_ok=True)
    fmt = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
    logging.basicConfig(
        level=getattr(logging, nivel.upper(), logging.INFO),
        format=fmt,
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(arquivo, encoding="utf-8"),
        ],
    )

logger = logging.getLogger("vigilia.runner")


# ─── Carregamento de config ───────────────────────────────────────────

def carregar_settings(caminho: str = "config/settings.yaml") -> dict:
    with open(caminho, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ─── Jobs por fonte ───────────────────────────────────────────────────

async def executar_camara(
    settings: dict,
    storage: Storage,
    scoring: ScoringEngine,
    alerter: Alerter,
    data_inicio: date,
    data_fim: date,
) -> LogExecucao:
    cfg = settings["coleta"]
    log = LogExecucao(fonte=FonteDados.CAMARA, inicio=datetime.now())
    log_id = storage.iniciar_log(log)

    logger.info("=== CAMARA: inicio | %s → %s ===", data_inicio, data_fim)

    try:
        async with FetcherCamara(rate_delay=cfg["rate_limit_delay"]) as fetcher:
            proposicoes = await fetcher.buscar_paginado(
                data_inicio=data_inicio,
                data_fim=data_fim,
                tipos=cfg.get("tipos_monitorados") or None,
                max_paginas=cfg["max_paginas"],
                itens=cfg["itens_por_pagina"],
            )

            log.total_coletado = len(proposicoes)

            # Enriquecer apenas as primeiras 30 para nao estourar rate limit
            logger.info("Enriquecendo top 30 com temas/autores...")
            for prop in proposicoes[:30]:
                await fetcher.enriquecer(prop)

        # Scoring
        proposicoes = scoring.classificar_lote(proposicoes)

        # Persistencia
        novas = storage.salvar_proposicoes(proposicoes)
        log.total_novos = novas
        log.total_relevantes = sum(
            1 for p in proposicoes if p.relevancia_score >= 1
        )

        # Alertas
        alerter.processar_lote(proposicoes)

        # Atualiza checkpoint
        storage.set_ultima_data(FonteDados.CAMARA, data_fim)

        log.finalizar("OK")
        logger.info(
            "=== CAMARA: fim OK | coletadas=%d novas=%d relevantes=%d ===",
            log.total_coletado, log.total_novos, log.total_relevantes,
        )

    except Exception as exc:
        log.finalizar("ERRO", erro=str(exc))
        logger.exception("Erro na execucao Camara: %s", exc)

    finally:
        storage.finalizar_log(log_id, log)

    return log


async def executar_senado(
    settings: dict,
    storage: Storage,
    scoring: ScoringEngine,
    alerter: Alerter,
    ano: int,
) -> LogExecucao:
    cfg = settings["coleta"]
    log = LogExecucao(fonte=FonteDados.SENADO, inicio=datetime.now())
    log_id = storage.iniciar_log(log)

    logger.info("=== SENADO: inicio | ano=%d ===", ano)

    try:
        async with FetcherSenado(rate_delay=cfg["rate_limit_delay"]) as fetcher:
            proposicoes = await fetcher.buscar_materias(ano=ano)

        log.total_coletado = len(proposicoes)

        proposicoes = scoring.classificar_lote(proposicoes)

        novas = storage.salvar_proposicoes(proposicoes)
        log.total_novos = novas
        log.total_relevantes = sum(
            1 for p in proposicoes if p.relevancia_score >= 1
        )

        alerter.processar_lote(proposicoes)
        storage.set_ultima_data(FonteDados.SENADO, date(ano, 12, 31))

        log.finalizar("OK")
        logger.info(
            "=== SENADO: fim OK | coletadas=%d novas=%d relevantes=%d ===",
            log.total_coletado, log.total_novos, log.total_relevantes,
        )

    except Exception as exc:
        log.finalizar("ERRO", erro=str(exc))
        logger.exception("Erro na execucao Senado: %s", exc)

    finally:
        storage.finalizar_log(log_id, log)

    return log


# ─── Entrada principal ────────────────────────────────────────────────

async def main(args: argparse.Namespace) -> None:
    settings = carregar_settings()

    configurar_logging(
        nivel=settings["logging"]["nivel"],
        arquivo=settings["logging"]["arquivo_log"],
    )

    logger.info("VigiliaPL iniciando | fonte=%s", args.fonte or "todas")

    storage = Storage(settings["banco"]["caminho_db"])
    scoring = ScoringEngine()
    alerter = Alerter(
        storage=storage,
        canais=settings["alertas"]["canais_ativos"],
        arquivo_alertas=settings["alertas"]["arquivo_alertas"],
    )

    hoje = date.today()

    # Determina janela de datas
    if args.dias:
        janela = args.dias
    else:
        janela = settings["coleta"]["janela_incremental_dias"]

    data_fim = hoje
    data_inicio = hoje - timedelta(days=janela)

    rodar_camara = args.fonte in (None, "camara")
    rodar_senado = args.fonte in (None, "senado")

    if rodar_camara:
        # Usa checkpoint se disponivel e nao foi passado --dias manualmente
        if not args.dias:
            ultima = storage.get_ultima_data(FonteDados.CAMARA)
            if ultima:
                data_inicio = ultima
                logger.info("Checkpoint Camara encontrado: coleta a partir de %s", ultima)

        await executar_camara(settings, storage, scoring, alerter, data_inicio, data_fim)

    if rodar_senado:
        await executar_senado(settings, storage, scoring, alerter, hoje.year)

    logger.info("VigiliaPL finalizado.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="VigiliaPL — Robô automatizado de monitoramento legislativo"
    )
    parser.add_argument(
        "--fonte",
        choices=["camara", "senado"],
        default=None,
        help="Fonte a coletar (padrao: todas)",
    )
    parser.add_argument(
        "--dias",
        type=int,
        default=None,
        help="Janela manual de dias para coleta (ignora checkpoint)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main(parse_args()))
