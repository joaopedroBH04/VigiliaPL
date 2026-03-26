"""
Coletores das APIs legislativas (Camara e Senado).
Todos os metodos sao assincronos. Sem nenhuma interacao com usuario.
Respeita rate limiting configurado em settings.yaml.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime
from typing import Any, Optional

import httpx

from .models import FonteDados, Proposicao

logger = logging.getLogger(__name__)

CAMARA_BASE = "https://dadosabertos.camara.leg.br/api/v2"
SENADO_BASE = "https://legis.senado.leg.br/dadosabertos"
HEADERS = {"Accept": "application/json"}


# ─── Utilidades compartilhadas ─────────────────────────────────────────

def _parse_date(v: str | None) -> date | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00")).date()
    except (ValueError, AttributeError):
        try:
            return date.fromisoformat(v[:10])
        except (ValueError, AttributeError):
            return None


def _parse_datetime(v: str | None) -> datetime | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


async def _get(
    client: httpx.AsyncClient,
    endpoint: str,
    params: dict | None = None,
    rate_delay: float = 0.8,
    max_retries: int = 3,
) -> dict[str, Any]:
    """GET generico com retry e exponential backoff."""
    for tentativa in range(max_retries):
        try:
            resp = await client.get(endpoint, params=params)
            if resp.status_code == 429:
                espera = 5 * (tentativa + 1)
                logger.warning("Rate limit atingido. Aguardando %ds...", espera)
                await asyncio.sleep(espera)
                continue
            resp.raise_for_status()
            await asyncio.sleep(rate_delay)
            return resp.json()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code >= 500 and tentativa < max_retries - 1:
                await asyncio.sleep(2 ** tentativa)
                continue
            logger.error("HTTP %s em %s", exc.response.status_code, endpoint)
            return {}
        except httpx.RequestError as exc:
            if tentativa < max_retries - 1:
                await asyncio.sleep(2)
                continue
            logger.error("Erro de conexao em %s: %s", endpoint, exc)
            return {}
    return {}


# ─── Camara dos Deputados ──────────────────────────────────────────────

class FetcherCamara:
    def __init__(self, rate_delay: float = 0.8, timeout: float = 30.0):
        self.rate_delay = rate_delay
        self._client: Optional[httpx.AsyncClient] = None
        self._timeout = timeout

    async def __aenter__(self):
        self._client = httpx.AsyncClient(
            base_url=CAMARA_BASE, headers=HEADERS,
            timeout=self._timeout, follow_redirects=True,
        )
        return self

    async def __aexit__(self, *_):
        if self._client:
            await self._client.aclose()

    async def _get(self, endpoint: str, params: dict | None = None) -> dict:
        return await _get(self._client, endpoint, params, self.rate_delay)

    async def buscar_proposicoes(
        self,
        data_inicio: date,
        data_fim: date,
        tipos: list[str] | None = None,
        pagina: int = 1,
        itens: int = 100,
    ) -> list[Proposicao]:
        params: dict[str, Any] = {
            "dataInicio": data_inicio.isoformat(),
            "dataFim": data_fim.isoformat(),
            "pagina": pagina,
            "itens": itens,
            "ordem": "DESC",
            "ordenarPor": "id",
        }
        if tipos:
            params["siglaTipo"] = tipos

        data = await self._get("/proposicoes", params)
        return [
            p for item in data.get("dados", [])
            if (p := self._parse(item)) is not None
        ]

    async def buscar_paginado(
        self,
        data_inicio: date,
        data_fim: date,
        tipos: list[str] | None = None,
        max_paginas: int = 5,
        itens: int = 100,
    ) -> list[Proposicao]:
        todas: list[Proposicao] = []
        for pagina in range(1, max_paginas + 1):
            logger.info("Camara: coletando pagina %d/%d...", pagina, max_paginas)
            lote = await self.buscar_proposicoes(
                data_inicio, data_fim, tipos, pagina, itens
            )
            todas.extend(lote)
            if len(lote) < itens:
                break
        logger.info("Camara: %d proposicoes coletadas no total.", len(todas))
        return todas

    async def enriquecer(self, prop: Proposicao) -> None:
        """Adiciona temas e autores a proposicao (chamadas extras na API)."""
        try:
            data = await self._get(f"/proposicoes/{prop.source_id}/temas")
            prop.tema_ids = [t.get("codTema", 0) for t in data.get("dados", [])]
            prop.temas = [t.get("tema", "") for t in data.get("dados", []) if t.get("tema")]
        except Exception as exc:
            logger.debug("Temas nao obtidos para %s: %s", prop.source_id, exc)

        try:
            data = await self._get(f"/proposicoes/{prop.source_id}/autores")
            prop.autores = [a.get("nome", "") for a in data.get("dados", []) if a.get("nome")]
        except Exception as exc:
            logger.debug("Autores nao obtidos para %s: %s", prop.source_id, exc)

    def _parse(self, item: dict) -> Optional[Proposicao]:
        try:
            return Proposicao(
                source_id=str(item["id"]),
                source=FonteDados.CAMARA,
                tipo=item.get("siglaTipo", ""),
                numero=item.get("numero", 0),
                ano=item.get("ano", 0),
                ementa=item.get("ementa", ""),
                data_apresentacao=_parse_date(item.get("dataApresentacao")),
                url_inteiro_teor=item.get("urlInteiroTeor"),
            )
        except (KeyError, Exception) as exc:
            logger.debug("Parse falhou: %s", exc)
            return None


# ─── Senado Federal ────────────────────────────────────────────────────

class FetcherSenado:
    def __init__(self, rate_delay: float = 1.0, timeout: float = 30.0):
        self.rate_delay = rate_delay
        self._client: Optional[httpx.AsyncClient] = None
        self._timeout = timeout

    async def __aenter__(self):
        self._client = httpx.AsyncClient(
            base_url=SENADO_BASE, headers=HEADERS,
            timeout=self._timeout, follow_redirects=True,
        )
        return self

    async def __aexit__(self, *_):
        if self._client:
            await self._client.aclose()

    async def _get(self, endpoint: str, params: dict | None = None) -> dict:
        return await _get(self._client, endpoint, params, self.rate_delay)

    async def buscar_materias(
        self, ano: int, tramitando: bool = True
    ) -> list[Proposicao]:
        params: dict[str, Any] = {"v": "7", "ano": ano}
        if tramitando:
            params["tramitando"] = "S"

        try:
            data = await self._get("/materia/pesquisa/lista", params)
        except Exception as exc:
            logger.error("Erro ao buscar materias do Senado: %s", exc)
            return []

        pesquisa = data.get("PesquisaBasicaMateria", {})
        materias = pesquisa.get("Materias", {}).get("Materia", [])
        if isinstance(materias, dict):
            materias = [materias]

        proposicoes = [
            p for m in materias
            if (p := self._parse(m)) is not None
        ]
        logger.info("Senado: %d materias coletadas.", len(proposicoes))
        return proposicoes

    def _parse(self, item: dict) -> Optional[Proposicao]:
        try:
            return Proposicao(
                source_id=str(item["CodigoMateria"]),
                source=FonteDados.SENADO,
                tipo=item.get("SiglaSubtipoMateria", ""),
                numero=int(item.get("NumeroMateria", 0)),
                ano=int(item.get("AnoMateria", 0)),
                ementa=item.get("EmentaMateria", ""),
                data_apresentacao=_parse_date(item.get("DataApresentacao")),
            )
        except (KeyError, Exception) as exc:
            logger.debug("Parse Senado falhou: %s", exc)
            return None
