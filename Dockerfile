# Dockerfile para Agente de Supermercado
FROM python:3.11-slim

# Metadados
LABEL maintainer="seu-email@exemplo.com"
LABEL description="Agente de IA para atendimento automatizado de supermercado"

# Variáveis de ambiente
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Diretório de trabalho
WORKDIR /app

# Cache-buster para build (permite invalidar camadas controladamente)
ARG BUILDTIME=dev

# Instalar dependências do sistema
RUN apt-get update && apt-get install -y \
    gcc \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Copiar requirements e instalar dependências Python
COPY requirements.txt .
RUN echo "[Build] BUILDTIME=${BUILDTIME}" && pip install --no-cache-dir -r requirements.txt

# Copiar código da aplicação
COPY . .

# Anotar tempo de build na imagem
LABEL build_time=${BUILDTIME}

# Diretório de logs será criado em runtime pelo logger (config/logger.py)

# Expor porta
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:8000/health')"

# Comando de execução
CMD ["python", "server.py"]
