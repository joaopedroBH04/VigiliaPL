"""
Motor de relevancia do VigiliaPL.
Classifica proposicoes por relevancia ao setor de alimentacao fora do lar.
Configuravel via keywords.yaml sem necessidade de alterar codigo.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any

import yaml

from .models import NivelRelevancia, Proposicao


def _normalizar(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return texto.lower().strip()


def _carregar_yaml(caminho: Path) -> dict[str, Any]:
    with open(caminho, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


class ScoringEngine:
    def __init__(self, keywords_path: Path | None = None):
        if keywords_path is None:
            keywords_path = Path(__file__).parent.parent / "config" / "keywords.yaml"

        cfg = _carregar_yaml(keywords_path)

        self.primarias: list[str] = []
        for termos in cfg.get("primarias", {}).values():
            self.primarias.extend(_normalizar(t) for t in termos)

        self.secundarias: list[str] = []
        for termos in cfg.get("secundarias", {}).values():
            self.secundarias.extend(_normalizar(t) for t in termos)

        self.temas_monitorados: set[int] = {
            int(c) for c in cfg.get("temas_camara", {})
        }
        self.exclusao: list[str] = [
            _normalizar(t) for t in cfg.get("exclusao", [])
        ]

        s = cfg.get("scoring", {})
        self.peso_primaria = s.get("peso_primaria", 3)
        self.peso_secundaria = s.get("peso_secundaria", 1)
        self.peso_tema = s.get("peso_tema", 2)
        self.limiar_alta = s.get("limiar_alta", 5)
        self.limiar_media = s.get("limiar_media", 3)
        self.limiar_baixa = s.get("limiar_baixa", 1)

        self._rx_primarias = self._compilar(self.primarias)
        self._rx_secundarias = self._compilar(self.secundarias)
        self._rx_exclusao = self._compilar(self.exclusao)

    def _compilar(self, termos: list[str]) -> list[tuple[str, re.Pattern]]:
        return [
            (t, re.compile(rf"\b{re.escape(t)}\w*", re.IGNORECASE))
            for t in termos
        ]

    def _match(self, texto: str, padroes: list[tuple[str, re.Pattern]]) -> list[str]:
        return [t for t, rx in padroes if rx.search(texto)]

    def classificar(self, prop: Proposicao) -> Proposicao:
        texto = _normalizar(f"{prop.ementa} {prop.ementa_detalhada or ''}")

        primarias_found = self._match(texto, self._rx_primarias)
        secundarias_found = self._match(texto, self._rx_secundarias)
        exclusao_found = self._match(texto, self._rx_exclusao)

        score = (
            len(primarias_found) * self.peso_primaria
            + len(secundarias_found) * self.peso_secundaria
            + sum(
                self.peso_tema
                for tid in prop.tema_ids
                if tid in self.temas_monitorados
            )
        )

        # Rebaixa se so tiver termos de exclusao sem contexto positivo
        if exclusao_found and not primarias_found and not secundarias_found:
            score = max(0, score - 2)

        if score >= self.limiar_alta:
            nivel = NivelRelevancia.ALTA
        elif score >= self.limiar_media:
            nivel = NivelRelevancia.MEDIA
        elif score >= self.limiar_baixa:
            nivel = NivelRelevancia.BAIXA
        else:
            nivel = NivelRelevancia.IRRELEVANTE

        prop.relevancia_score = score
        prop.relevancia_nivel = nivel
        prop.keywords_matched = (
            [f"[P]{t}" for t in primarias_found]
            + [f"[S]{t}" for t in secundarias_found]
        )
        prop.exclusion_matched = exclusao_found
        return prop

    def classificar_lote(self, proposicoes: list[Proposicao]) -> list[Proposicao]:
        for p in proposicoes:
            self.classificar(p)
        return sorted(proposicoes, key=lambda x: x.relevancia_score, reverse=True)
