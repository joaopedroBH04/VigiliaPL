"""
Camada de armazenamento SQLite do VigiliaPL.
Responsavel por:
  - Persistir proposicoes com idempotencia (sem duplicatas)
  - Guardar checkpoint da ultima execucao por fonte
  - Registrar log de cada execucao do robo
  - Registrar alertas disparados
"""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Generator, Optional

from .models import AlertaEnviado, FonteDados, LogExecucao, NivelRelevancia, Proposicao

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS proposicoes (
    chave_unica         TEXT PRIMARY KEY,
    source_id           TEXT NOT NULL,
    source              TEXT NOT NULL,
    tipo                TEXT,
    numero              INTEGER,
    ano                 INTEGER,
    ementa              TEXT,
    situacao_atual      TEXT,
    relevancia_score    INTEGER DEFAULT 0,
    relevancia_nivel    TEXT DEFAULT 'Irrelevante',
    keywords_matched    TEXT,   -- JSON array
    autores             TEXT,   -- JSON array
    temas               TEXT,   -- JSON array
    url_inteiro_teor    TEXT,
    data_apresentacao   TEXT,
    primeira_vez_visto  TEXT NOT NULL,
    ultima_atualizacao  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS checkpoint (
    fonte       TEXT PRIMARY KEY,
    ultima_data TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS execucoes_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    fonte           TEXT NOT NULL,
    inicio          TEXT NOT NULL,
    fim             TEXT,
    status          TEXT NOT NULL,
    total_coletado  INTEGER DEFAULT 0,
    total_novos     INTEGER DEFAULT 0,
    total_relevantes INTEGER DEFAULT 0,
    erro_msg        TEXT
);

CREATE TABLE IF NOT EXISTS alertas_enviados (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    proposicao_chave    TEXT NOT NULL,
    nivel               TEXT NOT NULL,
    tipo_alerta         TEXT NOT NULL,
    enviado_em          TEXT NOT NULL,
    canal               TEXT NOT NULL
);
"""


class Storage:
    def __init__(self, db_path: str | Path = "data/vigilia.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.executescript(SCHEMA)
        logger.debug("Banco inicializado em %s", self.db_path)

    @contextmanager
    def _conn(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ─── Checkpoint ──────────────────────────────────────────────────

    def get_ultima_data(self, fonte: FonteDados) -> Optional[date]:
        """Retorna a data do ultimo item processado por esta fonte."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT ultima_data FROM checkpoint WHERE fonte = ?",
                (fonte.value,),
            ).fetchone()
        if row:
            return date.fromisoformat(row["ultima_data"])
        return None

    def set_ultima_data(self, fonte: FonteDados, data: date) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO checkpoint (fonte, ultima_data)
                VALUES (?, ?)
                ON CONFLICT(fonte) DO UPDATE SET ultima_data = excluded.ultima_data
                """,
                (fonte.value, data.isoformat()),
            )
        logger.debug("Checkpoint %s atualizado para %s", fonte.value, data)

    # ─── Proposicoes ─────────────────────────────────────────────────

    def salvar_proposicoes(self, proposicoes: list[Proposicao]) -> int:
        """Insere ou atualiza proposicoes. Retorna quantas eram novas."""
        agora = datetime.now().isoformat()
        novas = 0

        with self._conn() as conn:
            for p in proposicoes:
                existia = conn.execute(
                    "SELECT 1 FROM proposicoes WHERE chave_unica = ?",
                    (p.chave_unica,),
                ).fetchone()

                conn.execute(
                    """
                    INSERT INTO proposicoes (
                        chave_unica, source_id, source, tipo, numero, ano,
                        ementa, situacao_atual, relevancia_score, relevancia_nivel,
                        keywords_matched, autores, temas, url_inteiro_teor,
                        data_apresentacao, primeira_vez_visto, ultima_atualizacao
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(chave_unica) DO UPDATE SET
                        situacao_atual      = excluded.situacao_atual,
                        relevancia_score    = excluded.relevancia_score,
                        relevancia_nivel    = excluded.relevancia_nivel,
                        keywords_matched    = excluded.keywords_matched,
                        autores             = excluded.autores,
                        temas               = excluded.temas,
                        ultima_atualizacao  = excluded.ultima_atualizacao
                    """,
                    (
                        p.chave_unica,
                        p.source_id,
                        p.source.value,
                        p.tipo,
                        p.numero,
                        p.ano,
                        p.ementa,
                        p.situacao_atual,
                        p.relevancia_score,
                        p.relevancia_nivel.value,
                        json.dumps(p.keywords_matched, ensure_ascii=False),
                        json.dumps(p.autores, ensure_ascii=False),
                        json.dumps(p.temas, ensure_ascii=False),
                        p.url_inteiro_teor,
                        p.data_apresentacao.isoformat() if p.data_apresentacao else None,
                        agora if not existia else None,  # primeira_vez_visto so na insercao
                        agora,
                    ),
                )
                if not existia:
                    novas += 1

        logger.info("Salvas %d proposicoes (%d novas).", len(proposicoes), novas)
        return novas

    def ja_alertado(self, chave: str, tipo_alerta: str) -> bool:
        """Verifica se ja foi enviado alerta deste tipo para esta proposicao."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM alertas_enviados WHERE proposicao_chave = ? AND tipo_alerta = ?",
                (chave, tipo_alerta),
            ).fetchone()
        return row is not None

    def registrar_alerta(self, alerta: AlertaEnviado) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO alertas_enviados
                    (proposicao_chave, nivel, tipo_alerta, enviado_em, canal)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    alerta.proposicao_chave,
                    alerta.nivel.value,
                    alerta.tipo_alerta,
                    alerta.enviado_em.isoformat(),
                    alerta.canal,
                ),
            )

    def get_proposicoes_alta_relevancia(
        self, limite: int = 50
    ) -> list[dict]:
        """Retorna as proposicoes de alta relevancia mais recentes."""
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM proposicoes
                WHERE relevancia_nivel = 'Alta'
                ORDER BY ultima_atualizacao DESC
                LIMIT ?
                """,
                (limite,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ─── Log de execucao ─────────────────────────────────────────────

    def iniciar_log(self, log: LogExecucao) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO execucoes_log (fonte, inicio, status)
                VALUES (?, ?, ?)
                """,
                (log.fonte.value, log.inicio.isoformat(), log.status),
            )
        return cur.lastrowid

    def finalizar_log(self, log_id: int, log: LogExecucao) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE execucoes_log
                SET fim = ?, status = ?, total_coletado = ?,
                    total_novos = ?, total_relevantes = ?, erro_msg = ?
                WHERE id = ?
                """,
                (
                    log.fim.isoformat() if log.fim else None,
                    log.status,
                    log.total_coletado,
                    log.total_novos,
                    log.total_relevantes,
                    log.erro_msg,
                    log_id,
                ),
            )
