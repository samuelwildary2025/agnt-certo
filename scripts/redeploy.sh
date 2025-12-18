#!/usr/bin/env bash
set -euo pipefail

# Redeploy padronizado usando Docker Compose (compatível com docker-compose e docker compose)
# Uso: dentro da pasta onde está o docker-compose.yml

if command -v docker-compose >/dev/null 2>&1; then
  COMPOSE_CMD="docker-compose"
else
  COMPOSE_CMD="docker compose"
fi

echo "[1/6] Derrubando serviços..."
$COMPOSE_CMD down || true

echo "[2/6] Rebuild sem cache com cache-buster..."
$COMPOSE_CMD build --no-cache --pull --build-arg BUILDTIME="$(date +%s)"

echo "[3/6] Subindo serviços..."
$COMPOSE_CMD up -d --force-recreate

echo "[4/6] Listando containers..."
docker ps -a

# Detectar container do agente (pelo container_name definido no compose)
AGENTE_CONTAINER="$(docker ps -aqf name=agente-supermercado || true)"
if [[ -z "${AGENTE_CONTAINER}" ]]; then
  echo "[WARN] Não foi possível detectar o container 'agente-supermercado'."
  echo "       Verifique o nome com 'docker ps -a' e rode manualmente os checks abaixo."
else
  echo "[INFO] Container detectado: ${AGENTE_CONTAINER}"
fi

echo "[5/6] Verificando ausência de 'proxies' em /app/agent.py..."
if [[ -n "${AGENTE_CONTAINER}" ]]; then
  docker exec "${AGENTE_CONTAINER}" grep -n "proxies" /app/agent.py || echo "[OK] Sem referências a 'proxies' no agent.py"
else
  echo "[SKIP] Check de 'proxies' pulado por não detectar container."
fi

echo "[6/7] Confirmando versões das libs openai e langchain-openai..."
if [[ -n "${AGENTE_CONTAINER}" ]]; then
  docker exec "${AGENTE_CONTAINER}" sh -lc 'pip show openai langchain-openai | grep -i "Version"'
else
  echo "[SKIP] Check de versões pulado por não detectar container."
fi

echo "[7/8] Conferindo versão do httpx..."
if [[ -n "${AGENTE_CONTAINER}" ]]; then
  docker exec "${AGENTE_CONTAINER}" sh -lc 'pip show httpx | grep -i "Version" || echo "httpx não instalado via pip (pode ser dependência transitiva)"'
else
  echo "[SKIP] Check de httpx pulado por não detectar container."
fi

echo "[8/8] Conferindo versão do psycopg (driver Postgres)..."
if [[ -n "${AGENTE_CONTAINER}" ]]; then
  docker exec "${AGENTE_CONTAINER}" sh -lc 'python -c "import psycopg; print(psycopg.__version__)" 2>/dev/null || echo "psycopg ausente (instale via requirements)"'
else
  echo "[SKIP] Check de psycopg pulado por não detectar container."
fi

echo "[DONE] Redeploy concluído."