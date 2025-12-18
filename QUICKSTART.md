# 🚀 Guia de Início Rápido

Este guia irá ajudá-lo a colocar o agente de supermercado em funcionamento em menos de 10 minutos.

## ⚡ Pré-requisitos Mínimos

Antes de começar, você precisa ter:

- [ ] Python 3.11 ou superior instalado
- [ ] Chave de API da OpenAI
- [ ] PostgreSQL rodando (ou use Docker)
- [ ] Redis rodando (ou use Docker)

## 📝 Passo a Passo

### 1. Clone e Entre no Diretório

```bash
cd agente_supermercado_python
```

### 2. Crie o Ambiente Virtual

```bash
python3.11 -m venv venv
source venv/bin/activate  # Linux/Mac
# ou
venv\Scripts\activate  # Windows
```

### 3. Instale as Dependências

```bash
pip install -r requirements.txt
```

### 4. Configure as Variáveis de Ambiente

Copie o arquivo de exemplo:

```bash
cp .env.example .env
```

Edite o arquivo `.env` e preencha **no mínimo** estas variáveis:

```env
# OBRIGATÓRIO
OPENAI_API_KEY=sk-proj-sua-chave-aqui
POSTGRES_CONNECTION_STRING=postgresql://user:pass@localhost:5432/db
REDIS_HOST=localhost

# OPCIONAL (para funcionalidades avançadas)
SUPABASE_URL=...
SUPABASE_KEY=...
COHERE_API_KEY=...
SUPERMERCADO_BASE_URL=...
SUPERMERCADO_AUTH_TOKEN=...
```

### 5. Inicie os Serviços (Opção A: Docker)

**Mais fácil e recomendado:**

```bash
docker-compose up -d postgres redis
```

Isso irá iniciar PostgreSQL e Redis automaticamente.

### 5. Inicie os Serviços (Opção B: Manual)

**Se preferir instalar manualmente:**

**PostgreSQL:**
```bash
# Ubuntu/Debian
sudo apt install postgresql
sudo systemctl start postgresql

# Mac
brew install postgresql
brew services start postgresql

# Criar banco de dados
psql -U postgres -c "CREATE DATABASE agente_db;"
psql -U postgres -d agente_db -f init.sql
```

**Redis:**
```bash
# Ubuntu/Debian
sudo apt install redis-server
sudo systemctl start redis

# Mac
brew install redis
brew services start redis
```

### 6. Teste o Agente

Execute o script de teste:

```bash
python test_agent.py
```

Você verá um prompt interativo:

```
🤖 TESTE DO AGENTE DE SUPERMERCADO
============================================================
Telefone de teste: 5511999998888
Digite 'sair' para encerrar o teste
============================================================

Você: Olá!
```

Digite uma mensagem e veja o agente responder!

### 7. Inicie o Servidor (Opcional)

Se quiser integrar com WhatsApp:

```bash
python server.py
```

O servidor estará disponível em `http://localhost:8000`

## 🎯 Testando Funcionalidades

### Teste 1: Saudação

```
Você: Olá, bom dia!
```

### Teste 2: Consulta de Horário

```
Você: Que horas são?
```

### Teste 3: Consulta de Produto (requer API configurada)

```
Você: Vocês têm arroz em estoque?
```

### Teste 4: Base de Conhecimento (requer Supabase configurado)

```
Você: Qual é a política de devolução?
```

## 🐳 Usando Docker (Mais Fácil)

Se você tem Docker instalado, pode iniciar tudo com um comando:

```bash
docker-compose up -d
```

Isso irá:
- ✅ Criar o banco PostgreSQL
- ✅ Iniciar o Redis
- ✅ Construir e iniciar o agente
- ✅ Configurar a rede entre os serviços

Acesse: `http://localhost:8000`

## ❓ Problemas Comuns

### Erro: "Module not found"

**Solução:**
```bash
pip install -r requirements.txt
```

### Erro: "Connection refused" (PostgreSQL)

**Solução:**
```bash
# Verifique se o PostgreSQL está rodando
sudo systemctl status postgresql

# Ou inicie com Docker
docker-compose up -d postgres
```

### Erro: "Connection refused" (Redis)

**Solução:**
```bash
# Verifique se o Redis está rodando
redis-cli ping

# Ou inicie com Docker
docker-compose up -d redis
```

### Erro: "OpenAI API key not found"

**Solução:**
Verifique se o arquivo `.env` existe e contém:
```env
OPENAI_API_KEY=sk-proj-...
```

## 📚 Próximos Passos

Agora que o agente está funcionando:

1. **Configure a Base de Conhecimento** (Supabase + Cohere) para respostas mais inteligentes
2. **Integre com WhatsApp** usando a UAZ API
3. **Configure a API do Supermercado** para consultas reais de estoque
4. **Personalize o Prompt** do agente em `agent.py`
5. **Adicione novas ferramentas** conforme necessário

## 🆘 Precisa de Ajuda?

- Leia o [README.md](README.md) completo
- Consulte a [ANALISE_WORKFLOW.md](ANALISE_WORKFLOW.md) para entender a arquitetura
- Verifique os logs em `logs/agente.log`

---

**Pronto! Seu agente de supermercado está funcionando! 🎉**
