"""
Modelos de dados do VigiliaPL.
Define as estruturas usadas em todo o pipeline automatizado.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class NivelRelevancia(str, Enum):
    ALTA = "Alta"
    MEDIA = "Media"
    BAIXA = "Baixa"
    IRRELEVANTE = "Irrelevante"


class FonteDados(str, Enum):
    CAMARA = "CAMARA"
    SENADO = "SENADO"


class Proposicao(BaseModel):
    source_id: str
    source: FonteDados
    tipo: str
    numero: int
    ano: int
    ementa: str = ""
    ementa_detalhada: Optional[str] = None
    situacao_atual: Optional[str] = None
    data_apresentacao: Optional[date] = None
    data_ultima_atualizacao: Optional[datetime] = None
    url_inteiro_teor: Optional[str] = None
    autores: list[str] = Field(default_factory=list)
    temas: list[str] = Field(default_factory=list)
    tema_ids: list[int] = Field(default_factory=list)

    # Preenchidos pelo scoring engine
    keywords_matched: list[str] = Field(default_factory=list)
    exclusion_matched: list[str] = Field(default_factory=list)
    relevancia_score: int = 0
    relevancia_nivel: NivelRelevancia = NivelRelevancia.IRRELEVANTE

    @property
    def chave_unica(self) -> str:
        """Chave de idempotencia: fonte + id original."""
        return f"{self.source.value}:{self.source_id}"

    @property
    def sigla_completa(self) -> str:
        return f"{self.tipo} {self.numero}/{self.ano}"


class LogExecucao(BaseModel):
    fonte: FonteDados
    inicio: datetime
    fim: Optional[datetime] = None
    status: str = "RUNNING"  # RUNNING | OK | ERRO
    total_coletado: int = 0
    total_novos: int = 0
    total_relevantes: int = 0
    erro_msg: Optional[str] = None

    def finalizar(self, status: str = "OK", erro: Optional[str] = None):
        self.fim = datetime.now()
        self.status = status
        self.erro_msg = erro


class AlertaEnviado(BaseModel):
    proposicao_chave: str
    nivel: NivelRelevancia
    tipo_alerta: str  # NOVA_PROPOSICAO | ATUALIZACAO | PAUTA
    enviado_em: datetime = Field(default_factory=datetime.now)
    canal: str = "LOG"  # LOG | EMAIL | SLACK
