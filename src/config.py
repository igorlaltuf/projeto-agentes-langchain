"""Configuração central: modelos, caminhos e modo offline."""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

RAIZ = Path(__file__).resolve().parent.parent
DATA_DIR = RAIZ / "data"
FIXTURES_DIR = RAIZ / "fixtures"
INDICES_DIR = RAIZ / "indices"

ANO_CEAP = 2025
CSV_CEAP = DATA_DIR / f"ceap_{ANO_CEAP}.csv"
SQLITE_CEAP = DATA_DIR / "ceap.db"
INDICE_ATO_MESA = INDICES_DIR / "ato_mesa"

MODO_OFFLINE = os.getenv("MODO_OFFLINE", "false").lower() == "true"

MODELO_AGENTE = os.getenv("MODELO_AGENTE", "gpt-4o-mini")
MODELO_JUIZ = os.getenv("MODELO_JUIZ", "gpt-4o")
MODELO_EMBEDDINGS = os.getenv("MODELO_EMBEDDINGS", "text-embedding-3-small")

API_CAMARA = "https://dadosabertos.camara.leg.br/api/v2"
API_BRASIL = "https://brasilapi.com.br/api"

HOJE = date.today().isoformat()
