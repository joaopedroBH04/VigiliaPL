"""
Sistema de alertas do VigiliaPL.
Notifica sobre proposicoes de alta relevancia por diferentes canais.

Canais suportados:
  - LOG  : escreve em arquivo de texto (padrao, sempre ativo)
  - EMAIL: placeholder para integracao futura com SMTP/SES
  - SLACK: placeholder para integracao futura com Webhook

Para ativar EMAIL ou SLACK, implemente os metodos _enviar_email()
e _enviar_slack() e adicione as credenciais via variaveis de ambiente.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path

from .models import AlertaEnviado, NivelRelevancia, Proposicao
from .storage import Storage

logger = logging.getLogger(__name__)


class Alerter:
    def __init__(
        self,
        storage: Storage,
        canais: list[str] | None = None,
        arquivo_alertas: str = "logs/alertas.txt",
    ):
        self.storage = storage
        self.canais = [c.upper() for c in (canais or ["LOG"])]
        self.arquivo_alertas = Path(arquivo_alertas)
        self.arquivo_alertas.parent.mkdir(parents=True, exist_ok=True)

    def processar_lote(self, proposicoes: list[Proposicao]) -> int:
        """
        Avalia lista de proposicoes e dispara alertas para as de Alta relevancia
        que ainda nao foram notificadas. Retorna quantos alertas foram enviados.
        """
        enviados = 0
        for prop in proposicoes:
            if prop.relevancia_nivel != NivelRelevancia.ALTA:
                continue
            if self.storage.ja_alertado(prop.chave_unica, "NOVA_PROPOSICAO"):
                continue

            self._disparar(prop)
            enviados += 1

        if enviados:
            logger.info("Alertas disparados: %d", enviados)
        return enviados

    def _disparar(self, prop: Proposicao) -> None:
        mensagem = self._formatar(prop)

        for canal in self.canais:
            if canal == "LOG":
                self._enviar_log(mensagem)
            elif canal == "EMAIL":
                self._enviar_email(mensagem, prop)
            elif canal == "SLACK":
                self._enviar_slack(mensagem, prop)

        alerta = AlertaEnviado(
            proposicao_chave=prop.chave_unica,
            nivel=prop.relevancia_nivel,
            tipo_alerta="NOVA_PROPOSICAO",
            canal=",".join(self.canais),
        )
        self.storage.registrar_alerta(alerta)
        logger.info(
            "ALERTA [%s] Score=%d | %s — %s",
            prop.relevancia_nivel.value,
            prop.relevancia_score,
            prop.sigla_completa,
            prop.ementa[:120],
        )

    def _formatar(self, prop: Proposicao) -> str:
        linha = "=" * 72
        keywords = ", ".join(prop.keywords_matched) if prop.keywords_matched else "—"
        autores = ", ".join(prop.autores[:3]) if prop.autores else "—"
        link = prop.url_inteiro_teor or "—"
        data_str = prop.data_apresentacao.isoformat() if prop.data_apresentacao else "—"

        return (
            f"\n{linha}\n"
            f"  ALERTA VIGILIA LEGISLATIVA — {datetime.now().strftime('%d/%m/%Y %H:%M')}\n"
            f"{linha}\n"
            f"  Proposicao : {prop.sigla_completa} ({prop.source.value})\n"
            f"  Relevancia : {prop.relevancia_nivel.value} (Score: {prop.relevancia_score})\n"
            f"  Ementa     : {prop.ementa}\n"
            f"  Situacao   : {prop.situacao_atual or '—'}\n"
            f"  Autores    : {autores}\n"
            f"  Keywords   : {keywords}\n"
            f"  Apresent.  : {data_str}\n"
            f"  Link       : {link}\n"
            f"{linha}\n"
        )

    def _enviar_log(self, mensagem: str) -> None:
        """Escreve alerta em arquivo de texto."""
        with open(self.arquivo_alertas, "a", encoding="utf-8") as f:
            f.write(mensagem)

    def _enviar_email(self, mensagem: str, prop: Proposicao) -> None:
        """
        Placeholder para envio de e-mail via SMTP/AWS SES.
        Variaveis de ambiente esperadas:
          SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, ALERT_EMAIL_TO
        """
        smtp_host = os.getenv("SMTP_HOST")
        if not smtp_host:
            logger.warning("EMAIL configurado mas SMTP_HOST nao definido. Pulando.")
            return
        # TODO: implementar envio via smtplib ou boto3 SES
        logger.info("EMAIL enviado para %s (placeholder)", os.getenv("ALERT_EMAIL_TO"))

    def _enviar_slack(self, mensagem: str, prop: Proposicao) -> None:
        """
        Placeholder para notificacao via Slack Webhook.
        Variavel de ambiente esperada: SLACK_WEBHOOK_URL
        """
        webhook = os.getenv("SLACK_WEBHOOK_URL")
        if not webhook:
            logger.warning("SLACK configurado mas SLACK_WEBHOOK_URL nao definido. Pulando.")
            return
        # TODO: implementar POST via httpx para webhook do Slack
        logger.info("SLACK notificado (placeholder)")
