"""
Configurações do Agente de Supermercado
Carrega variáveis de ambiente usando Pydantic Settings
"""
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, field_validator
from typing import Optional


class Settings(BaseSettings):
    """Configurações da aplicação carregadas do .env"""
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )
    
    # LLM Provider (openai ou google)
    openai_api_key: Optional[str] = None
    openai_embedding_api_key: Optional[str] = None # Chave específica para embeddings (OpenAI)
    google_api_key: Optional[str] = None
    llm_model: Optional[str] = None
    llm_temperature: float = 0.0  # Zero para respostas determinísticas
    llm_provider: str = "google"   # Mantido padrão mas pode ser sobrescrito pelo env
    gemini_audio_model: str = "gemini-1.5-flash" # Modelo padrão para áudio, configurável no env
    openai_api_base: Optional[str] = None # Para usar Grok (xAI) ou outros compatíveis
    moonshot_api_key: Optional[str] = None
    moonshot_api_url: str = "https://api.moonshot.ai/anthropic"
    
    # Postgres
    postgres_connection_string: str
    postgres_table_name: str = "memoria"
    postgres_products_table_name: str = "produtos-sp-queiroz"  # Nova variável para tabela de produtos
    postgres_message_limit: int = 5
    
    # Banco Vetorial de Produtos (Postgres - pgvector)
    vector_db_connection_string: Optional[str] = None
    vector_search_mode: str = "exact"
    vector_search_fallback: bool = True
    vector_search_term_mappings: bool = False
    
    # Redis
    redis_url_override: Optional[str] = Field(default=None, alias="REDIS_URL")
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_password: Optional[str] = None
    redis_db: int = 0
    
    # API do Supermercado
    supermercado_base_url: str
    supermercado_auth_token: str

    # Consulta de EAN (estoque/preço)
    estoque_ean_base_url: str = "http://45.178.95.233:5001/api/Produto/GetProdutosEAN"

    # EAN Smart Responder (Supabase Functions)
    smart_responder_url: Optional[str] = None
    smart_responder_token: Optional[str] = None
    smart_responder_auth: str = ""
    smart_responder_apikey: str = ""
    pre_resolver_enabled: bool = False
    
    # ============================================
    # WhatsApp API - UAZAPI
    # ============================================
    # Documentação: https://docs.uazapi.com/
    uazapi_base_url: Optional[str] = None  # Ex: https://aimerc.uazapi.com
    uazapi_token: Optional[str] = None     # Token da instância
    
    # Human Takeover - Tempo de pausa quando atendente humano assume (em segundos)
    human_takeover_ttl: int = 2400  # 40 minutos padrão
    
    # Queue Workers (ARQ)
    workers_max_jobs: int = 15  # Aumentado de 5 para 15 (suportado pela nova chave com billing)
    worker_retry_attempts: int = 3  # Tentativas de retry em caso de falha
    
    # Servidor
    server_host: str = "0.0.0.0"
    server_port: int = 8000
    debug_mode: bool = False

    # Logging
    log_level: str = "INFO"
    log_file: str = "logs/agente.log"
    
    agent_prompt_path: Optional[str] = "prompts/vendedor.md"

    product_context_path: Optional[str] = "prompts/product_context.json"
    term_translations_path: str = "prompts/term_translations.json"

    @field_validator(
        "openai_api_base",
        "supermercado_base_url",
        "estoque_ean_base_url",
        "uazapi_base_url",
        "smart_responder_url",
        "smart_responder_token",
        "supermercado_auth_token",
        "redis_url_override",
        mode="before",
    )
    @classmethod
    def _strip_wrapping_chars(cls, v):
        if v is None:
            return None
        s = str(v).strip()
        if len(s) >= 2 and ((s[0] == s[-1] == "`") or (s[0] == s[-1] == '"') or (s[0] == s[-1] == "'")):
            s = s[1:-1].strip()
        return s


    @property
    def redis_url(self) -> str:
        """Monta a URL de conexão do Redis baseada nas variáveis"""
        if self.redis_url_override:
            return self.redis_url_override
        auth = f":{self.redis_password}@" if self.redis_password else ""
        return f"redis://{auth}{self.redis_host}:{self.redis_port}/{self.redis_db}"

# Instância global de configurações
settings = Settings()
