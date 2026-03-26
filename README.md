# VigiliaPL — Monitor Legislativo Automatizado

Robô de monitoramento parlamentar da **Abrasel** para o setor de alimentação fora do lar. Coleta, classifica e alerta sobre proposições legislativas relevantes nas APIs da Câmara dos Deputados e do Senado Federal — sem nenhuma interação manual.

Projetado para rodar como job agendado (cron, Task Scheduler, AWS EventBridge, Airflow).

## Como funciona

```
Agendador (cron/EventBridge)
        │
        ▼
  runner.py
        │
        ├── FetcherCamara  →  API Câmara (proposições + temas + autores)
        ├── FetcherSenado  →  API Senado (matérias legislativas)
        │
        ├── ScoringEngine  →  classifica por relevância ao setor A&B
        │
        ├── Storage        →  SQLite local (idempotente, sem duplicatas)
        │
        └── Alerter        →  notifica Alta relevância (LOG / EMAIL / SLACK)
```

## Motor de Scoring

| Tipo | Peso | Exemplos |
|------|------|---------|
| Palavras primárias | 3 pts | restaurante, bar, lanchonete, food truck, delivery, garçom, Abrasel |
| Palavras secundárias | 1 pt | ANVISA, vigilância sanitária, reforma tributária, lei seca |
| Temas da API Câmara | 2 pts | Indústria/Comércio (40), Trabalho (46), Tributação (47), Turismo (49) |

**Classificação:** Score ≥ 5 = Alta | 3–4 = Média | 1–2 = Baixa | 0 = Irrelevante

Alertas automáticos são disparados apenas para **Alta relevância** e apenas uma vez por proposição.

## Estrutura do Projeto

```
VigiliaPL/
├── runner.py              # Ponto de entrada — orquestra o pipeline completo
├── requirements.txt
├── config/
│   ├── settings.yaml      # Configurações de execução (janela, rate limit, canais)
│   └── keywords.yaml      # Taxonomia de palavras-chave (editável sem código)
├── src/
│   ├── models.py          # Schemas Pydantic (Proposicao, LogExecucao, Alerta)
│   ├── fetchers.py        # Coletores assíncronos: Câmara e Senado
│   ├── scoring.py         # Motor de relevância com regex e pesos configuráveis
│   ├── storage.py         # SQLite: persistência, checkpoint, idempotência
│   └── alerter.py         # Sistema de alertas: LOG, EMAIL (placeholder), SLACK (placeholder)
├── data/                  # Banco SQLite gerado em runtime (ignorado pelo git)
└── logs/                  # Logs de execução e arquivo de alertas (ignorado pelo git)
```

## Instalação

```bash
git clone https://github.com/joaopedroBH04/VigiliaPL.git
cd VigiliaPL
pip install -r requirements.txt
```

## Uso

```bash
# Execução incremental (usa checkpoint da última execução)
python runner.py

# Coletar apenas a Câmara
python runner.py --fonte camara

# Coletar apenas o Senado
python runner.py --fonte senado

# Janela manual de 7 dias (ignora checkpoint)
python runner.py --dias 7
```

## Agendamento

**Linux/Mac — crontab** (execução diária às 06h00):
```
0 6 * * 1-5 /usr/bin/python3 /caminho/VigiliaPL/runner.py >> /caminho/VigiliaPL/logs/cron.log 2>&1
```

**Windows — Task Scheduler:**
```
Trigger : Diário às 06:00
Ação    : python.exe "C:\caminho\VigiliaPL\runner.py"
```

**AWS EventBridge (produção):**
```
Schedule : cron(0 9 ? * MON-FRI *)  # 06h Brasilia = 09h UTC
Target   : ECS Task com a imagem Docker do runner
```

## Configuração de Alertas

Edite `config/settings.yaml` para ativar canais adicionais:

```yaml
alertas:
  canais_ativos:
    - LOG     # sempre ativo — grava em logs/alertas.txt
    - EMAIL   # requer SMTP_HOST, SMTP_USER, SMTP_PASS, ALERT_EMAIL_TO
    - SLACK   # requer SLACK_WEBHOOK_URL
```

As credenciais são lidas de **variáveis de ambiente** — nunca hardcoded.

## Idempotência

O sistema é totalmente seguro para reexecução:
- Proposições já existentes são **atualizadas**, não duplicadas (`ON CONFLICT DO UPDATE`)
- Alertas já enviados são **ignorados** (`ja_alertado()` checa o histórico)
- Checkpoint por fonte garante que a próxima execução pega só o que é novo

## Banco de Dados

SQLite local em `data/vigilia.db` com 4 tabelas:

| Tabela | Conteúdo |
|--------|---------|
| `proposicoes` | Proposições com score, keywords, situação |
| `checkpoint` | Última data processada por fonte |
| `execucoes_log` | Histórico de cada run com status e métricas |
| `alertas_enviados` | Histórico de alertas para evitar duplicatas |

## APIs Utilizadas

| Fonte | Base URL |
|-------|---------|
| Câmara dos Deputados | `https://dadosabertos.camara.leg.br/api/v2` |
| Senado Federal | `https://legis.senado.leg.br/dadosabertos` |

Ambas são APIs públicas e gratuitas. O sistema respeita o rate limiting configurado em `settings.yaml`.

---

**Abrasel** — Associação Brasileira de Bares e Restaurantes
Autor: João Amaral — Estagiário de Dados
