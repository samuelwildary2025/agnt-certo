# 🤖 Agente de Supermercado em Python

Implementação completa em Python de um agente de IA para atendimento automatizado de supermercado via WhatsApp, originalmente desenvolvido em n8n.

## 📋 Índice

- [Visão Geral](#visão-geral)
- [Funcionalidades](#funcionalidades)
- [Arquitetura](#arquitetura)
- [Requisitos](#requisitos)
- [Instalação](#instalação)
- [Configuração](#configuração)
- [Uso](#uso)
- [Estrutura do Projeto](#estrutura-do-projeto)
- [Ferramentas do Agente](#ferramentas-do-agente)
- [API Endpoints](#api-endpoints)
- [Testes](#testes)
- [Deployment](#deployment)
- [Troubleshooting](#troubleshooting)

## 🎯 Visão Geral

Este projeto implementa um agente de conversação inteligente usando **LangChain** e **OpenAI GPT** para automatizar o atendimento ao cliente de um supermercado. O agente é capaz de:

- Consultar estoque e preços de produtos
- Criar e gerenciar pedidos
- Responder dúvidas usando uma base de conhecimento (RAG)
- Manter contexto de conversação com memória persistente
- Integrar com WhatsApp via API

## ✨ Funcionalidades

### 🛠️ Ferramentas do Agente

1. **estoque_tool** - Consulta de produtos e preços
2. **pedidos_tool** - Criação de novos pedidos
3. **alterar_tool** - Modificação de pedidos existentes
4. **confirme_tool** - Verificação de pedidos ativos (Redis)
5. **time_tool** - Consulta de data/hora atual
6. **ean_tool** - Base de conhecimento (RAG com Supabase + Cohere)

### 🧠 Recursos Avançados

- **Memória de Conversação**: Histórico persistente no PostgreSQL
- **RAG (Retrieval-Augmented Generation)**: Base de conhecimento com embeddings e reranking
- **Controle de Estado**: Redis para gerenciar pedidos ativos
- **Logging Estruturado**: Logs em JSON para análise e debugging
- **API RESTful**: FastAPI para integração com WhatsApp
- **Processamento Assíncrono**: Background tasks para respostas rápidas

## 🏗️ Arquitetura

```
┌─────────────┐
│  WhatsApp   │
└──────┬──────┘
       │ Webhook
       ▼
┌─────────────────────────────────┐
│      FastAPI Server             │
│  (server.py)                    │
└──────┬──────────────────────────┘
       │
       ▼
┌─────────────────────────────────┐
│   LangChain Agent               │
│   (agent.py)                    │
│                                 │
│  ┌─────────────────────────┐   │
│  │  GPT-4o-mini            │   │
│  └─────────────────────────┘   │
│                                 │
│  ┌─────────────────────────┐   │
│  │  7 Tools                │   │
│  │  - HTTP (API)           │   │
│  │  - Redis (Estado)       │   │
│  │  - Time (Horário)       │   │
│  │  - RAG (Conhecimento)   │   │
│  └─────────────────────────┘   │
└──────┬──────────────────────────┘
       │
       ▼
┌─────────────────────────────────┐
│  Serviços Externos              │
│  - PostgreSQL (Memória)         │
│  - Redis (Estado)               │
│  - Supabase (Vector Store)      │
│  - Cohere (Reranker)            │
│  - API Supermercado             │
└─────────────────────────────────┘
```

## 📦 Requisitos

### Software

- Python 3.11+
- PostgreSQL 12+
- Redis 6+
- Acesso à internet para APIs externas

### Serviços Externos

- **OpenAI API** - Para o modelo GPT
- **Supabase** - Para vector store (base de conhecimento)
- **Cohere** - Para reranking de documentos
- **API do Supermercado** - Sistema de gestão de produtos/pedidos
- **UAZ API** - Para integração com WhatsApp

## 🚀 Instalação

### 1. Clone o repositório

```bash
git clone <seu-repositorio>
cd agente_supermercado_python
```

### 2. Crie um ambiente virtual

```bash
python3.11 -m venv venv
source venv/bin/activate  # Linux/Mac
# ou
venv\Scripts\activate  # Windows
```

### 3. Instale as dependências

```bash
pip install -r requirements.txt
```

### 4. Configure as variáveis de ambiente

```bash
cp .env.example .env
# Edite o arquivo .env com suas credenciais
nano .env
```

## ⚙️ Configuração

### Arquivo .env

Preencha todas as variáveis no arquivo `.env`:

```env
# OpenAI
OPENAI_API_KEY=sk-proj-...
LLM_MODEL=gpt-4o-mini
LLM_TEMPERATURE=0

# Supabase (Base de Conhecimento)
SUPABASE_URL=https://seu-projeto.supabase.co
SUPABASE_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
SUPABASE_TABLE_NAME=documents
SUPABASE_QUERY_NAME=match_documents

# Cohere (Reranker)
COHERE_API_KEY=...

# Postgres (Memória)
POSTGRES_CONNECTION_STRING=postgresql://user:password@host:5432/database
POSTGRES_TABLE_NAME=basemercadaokLkGG

# Redis (Estado)
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_PASSWORD=
REDIS_DB=0

# API do Supermercado
SUPERMERCADO_BASE_URL=https://wildhub-wildhub-sistema-supermercado.5mos1l.easypanel.host/api
SUPERMERCADO_AUTH_TOKEN=Bearer seu_token_aqui

# WhatsApp API
WHATSAPP_API_URL=https://wildhub.uazapi.com
WHATSAPP_TOKEN=seu_token_whatsapp

# Servidor
SERVER_HOST=0.0.0.0
SERVER_PORT=8000
DEBUG_MODE=False

# Logging
LOG_LEVEL=INFO
LOG_FILE=logs/agente.log
```

### Configuração do PostgreSQL

Crie a tabela de memória de conversação:

```sql
CREATE TABLE IF NOT EXISTS basemercadaokLkGG (
    id SERIAL PRIMARY KEY,
    session_id TEXT NOT NULL,
    message JSONB NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_session_id ON basemercadaokLkGG(session_id);
```

### Configuração do Supabase

1. Crie um projeto no [Supabase](https://supabase.com)
2. Crie uma tabela `documents` com a extensão `pgvector`
3. Configure a função RPC `match_documents` para busca semântica

Exemplo de schema:

```sql
-- Habilitar extensão pgvector
CREATE EXTENSION IF NOT EXISTS vector;

-- Criar tabela de documentos
CREATE TABLE documents (
    id BIGSERIAL PRIMARY KEY,
    content TEXT,
    metadata JSONB,
    embedding VECTOR(1536)
);

-- Criar índice para busca vetorial
CREATE INDEX ON documents USING ivfflat (embedding vector_cosine_ops);

-- Função de busca
CREATE OR REPLACE FUNCTION match_documents(
    query_embedding VECTOR(1536),
    match_threshold FLOAT,
    match_count INT
)
RETURNS TABLE (
    id BIGINT,
    content TEXT,
    metadata JSONB,
    similarity FLOAT
)
LANGUAGE SQL STABLE
AS $$
    SELECT
        documents.id,
        documents.content,
        documents.metadata,
        1 - (documents.embedding <=> query_embedding) AS similarity
    FROM documents
    WHERE 1 - (documents.embedding <=> query_embedding) > match_threshold
    ORDER BY similarity DESC
    LIMIT match_count;
$$;
```

## 💻 Uso

### Modo Servidor (Produção)

Inicie o servidor FastAPI:

```bash
python server.py
```

O servidor estará disponível em `http://localhost:8000`

### Modo Teste (Desenvolvimento)

Teste o agente localmente sem servidor web:

```bash
# Teste interativo (conversação)
python test_agent.py

# Teste de ferramentas individuais
python test_agent.py --tools
```

### Usando Docker (Recomendado)

```bash
# Build da imagem
docker build -t agente-supermercado .

# Executar container
docker run -d \
  --name agente-supermercado \
  -p 8000:8000 \
  --env-file .env \
  agente-supermercado
```

## 📁 Estrutura do Projeto

```
agente_supermercado_python/
├── config/
│   ├── __init__.py
│   ├── settings.py          # Configurações (Pydantic Settings)
│   └── logger.py            # Sistema de logging
├── tools/
│   ├── __init__.py
│   ├── http_tools.py        # Ferramentas HTTP (estoque, pedidos, alterar)
│   ├── redis_tools.py       # Ferramentas Redis (set, confirme)
│   ├── time_tool.py         # Ferramenta de tempo
│   └── kb_tools.py          # Base de conhecimento (RAG)
├── logs/
│   └── agente.log           # Logs da aplicação
├── tests/
│   └── test_*.py            # Testes unitários
├── agent.py                 # Agente LangChain principal
├── server.py                # Servidor FastAPI
├── test_agent.py            # Script de teste
├── requirements.txt         # Dependências Python
├── .env.example             # Exemplo de variáveis de ambiente
├── .env                     # Variáveis de ambiente (não versionar!)
├── Dockerfile               # Container Docker
├── docker-compose.yml       # Orquestração Docker
├── README.md                # Este arquivo
└── ANALISE_WORKFLOW.md      # Análise do workflow original (n8n)
```

## 🔧 Ferramentas do Agente

### 1. estoque_tool

Consulta produtos no sistema do supermercado.

**Exemplo de uso pelo agente:**
```python
estoque_tool("https://api.supermercado.com/produtos/consulta?nome=arroz")
```

### 2. pedidos_tool

Cria um novo pedido.

**Exemplo de uso pelo agente:**
```python
pedidos_tool('{"cliente": "João", "telefone": "5511999998888", "itens": [...], "total": 50.00}')
```

### 3. alterar_tool

Modifica um pedido existente.

**Exemplo de uso pelo agente:**
```python
alterar_tool("5511999998888", '{"status": "cancelado"}')
```

### 4. confirme_tool

Verifica se existe pedido ativo.

**Exemplo de uso pelo agente:**
```python
confirme_tool("5511999998888")
```

### 5. time_tool

Retorna data e hora atual.

**Exemplo de uso pelo agente:**
```python
time_tool()
```

### 6. ean_tool

Consulta a base de conhecimento.

**Exemplo de uso pelo agente:**
```python
ean_tool("política de devolução")
```

## 🌐 API Endpoints

### GET /

Verificação de saúde do serviço.

**Resposta:**
```json
{
  "status": "online",
  "service": "Agente de Supermercado",
  "version": "1.0.0",
  "timestamp": "2024-01-01T12:00:00"
}
```

### GET /health

Health check detalhado.

### POST /webhook/whatsapp

Webhook para receber mensagens do WhatsApp.

**Payload esperado:**
```json
{
  "body": {
    "message": {
      "from": "5511999998888",
      "text": {"body": "Olá!"},
      "messageid": "..."
    },
    "chat": {"wa_id": "5511999998888"},
    "data": {"messageType": "textMessage"}
  }
}
```

### POST /message

Endpoint direto para testes (sem WhatsApp).

**Request:**
```json
{
  "telefone": "5511999998888",
  "mensagem": "Olá, quero fazer um pedido",
  "message_type": "text"
}
```

**Response:**
```json
{
  "success": true,
  "response": "Olá! Claro, posso ajudá-lo com seu pedido...",
  "telefone": "5511999998888",
  "timestamp": "2024-01-01T12:00:00",
  "error": null
}
```

## 🧪 Testes

### Testes Manuais

```bash
# Teste interativo
python test_agent.py

# Teste de ferramentas
python test_agent.py --tools
```

### Testes com cURL

```bash
# Testar endpoint de mensagem
curl -X POST http://localhost:8000/message \
  -H "Content-Type: application/json" \
  -d '{
    "telefone": "5511999998888",
    "mensagem": "Olá, quero consultar o estoque de arroz"
  }'
```

### Testes Unitários

```bash
# Executar todos os testes
pytest tests/

# Executar com cobertura
pytest --cov=. tests/
```

## 🚢 Deployment

### Usando Docker Compose

```yaml
# docker-compose.yml
version: '3.8'

services:
  agente:
    build: .
    ports:
      - "8000:8000"
    env_file:
      - .env
    depends_on:
      - postgres
      - redis
    restart: unless-stopped

  postgres:
    image: postgres:15
    environment:
      POSTGRES_DB: agente_db
      POSTGRES_USER: agente_user
      POSTGRES_PASSWORD: senha_segura
    volumes:
      - postgres_data:/var/lib/postgresql/data

  redis:
    image: redis:7-alpine
    volumes:
      - redis_data:/data

volumes:
  postgres_data:
  redis_data:
```

Execute:
```bash
docker-compose up -d
```

### Deploy em Cloud

#### Railway / Render / Fly.io

1. Conecte seu repositório
2. Configure as variáveis de ambiente
3. Deploy automático

#### AWS / GCP / Azure

Use o Dockerfile fornecido para criar uma imagem e deploy em:
- AWS ECS / Fargate
- Google Cloud Run
- Azure Container Instances

## 🐛 Troubleshooting

### Erro: "Conexão com Redis não estabelecida"

**Solução:**
- Verifique se o Redis está rodando: `redis-cli ping`
- Confirme as credenciais no `.env`
- Teste a conexão: `redis-cli -h localhost -p 6379`

### Erro: "Base de conhecimento não inicializada"

**Solução:**
- Verifique as credenciais do Supabase
- Confirme que a tabela `documents` existe
- Teste a conexão com o Supabase

### Erro: "Timeout ao consultar estoque"

**Solução:**
- Verifique se a API do supermercado está acessível
- Confirme o token de autenticação
- Aumente o timeout em `tools/http_tools.py`

### Redeploy no Easypanel (build do código correto)

Para garantir que o container está rodando com o código atualizado (sem cache e sem referências antigas), execute no host do Easypanel, dentro da pasta que contém o `docker-compose.yml` do serviço:

1. Derrubar serviços:
   - `docker compose down`
2. Rebuild sem cache:
   - `docker compose build --no-cache`
3. Subir novamente (forçando recriação):
   - `docker compose up -d --force-recreate`
4. Listar containers e identificar o do agente:
   - `docker ps -a` (container esperado: `agente-supermercado`)
5. Confirmar que o `agent.py` novo entrou (sem referências a proxies):
   - `docker exec agente-supermercado grep -n "proxies" /app/agent.py` (deve retornar vazio)
6. Confirmar versões das libs:
   - `docker exec agente-supermercado sh -lc 'pip show openai langchain-openai | grep -i "Version"'`
   - Esperado: `openai >= 1.0` (atual: `1.10.0`) e `langchain-openai >= 0.0.5` (atual: `0.0.5`)

Se o passo 5 ainda mostrar `proxies=`:
- Verifique se o `docker-compose.yml` está usando `build: .` e não uma `image:` pré-construída.
- Confirme que você está no diretório correto do código que o Easypanel usa para build.
- Opcional: rode `docker compose build --pull --no-cache` e `docker compose up -d --force-recreate`.


### Logs não aparecem

**Solução:**
- Verifique se a pasta `logs/` existe
- Confirme permissões de escrita
- Ajuste `LOG_LEVEL` no `.env`

## 📝 Licença

Este projeto é proprietário. Todos os direitos reservados.

## 👥 Contribuindo

Para contribuir:
1. Fork o projeto
2. Crie uma branch para sua feature (`git checkout -b feature/nova-funcionalidade`)
3. Commit suas mudanças (`git commit -m 'Adiciona nova funcionalidade'`)
4. Push para a branch (`git push origin feature/nova-funcionalidade`)
5. Abra um Pull Request

## 📞 Suporte

Para dúvidas ou problemas:
- Abra uma issue no GitHub
- Entre em contato: suporte@exemplo.com

## 🎉 Agradecimentos

- LangChain pela excelente framework de agentes
- OpenAI pelo modelo GPT
- Supabase e Cohere pelas ferramentas de RAG

---

**Desenvolvido com ❤️ para automatizar o atendimento ao cliente**
