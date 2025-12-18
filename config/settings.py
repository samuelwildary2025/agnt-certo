"""
Configurações do Agente de Supermercado
Carrega variáveis de ambiente usando Pydantic Settings
"""
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional


class Settings(BaseSettings):
    """Configurações da aplicação carregadas do .env"""
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )
    
    # OpenAI
    openai_api_key: str
    llm_model: str = "gpt-5-mini"
    llm_temperature: float = 0.0
    llm_provider: str = "openai"
    moonshot_api_key: Optional[str] = None
    moonshot_api_url: str = "https://api.moonshot.ai/anthropic"
    
    # Postgres
    postgres_connection_string: str
    postgres_table_name: str = "memoria"
    postgres_message_limit: int = 8
    
    # Redis
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
    smart_responder_url: str = "https://gmhpegzldsuibmmvqbxs.supabase.co/functions/v1/smart-responder"
    smart_responder_token: str = "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImdtaHBlZ3psZHN1aWJtbXZxYnhzIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NjI4NDE4MzQsImV4cCI6MjA3ODQxNzgzNH0.1V-CGTIw89BgMe0nc83aGNi7NwnI6gYD4kCx6IW0U70"
    smart_responder_auth: str = ""
    smart_responder_apikey: str = ""
    pre_resolver_enabled: bool = False
    
    # WhatsApp / UAZ API
    # WHATSAPP_API_URL mantido para compatibilidade, mas UAZ_API_URL tem prioridade
    whatsapp_api_url: Optional[str] = None 
    uaz_api_url: Optional[str] = None      # <--- NOVA VARIÁVEL ESPECÍFICA
    
    whatsapp_token: str
    whatsapp_method: str = "POST"
    whatsapp_agent_number: str | None = None
    
    # Human Takeover - Tempo de pausa quando atendente humano assume (em segundos)
    human_takeover_ttl: int = 900  # 15 minutos padrão
    
    # Servidor
    server_host: str = "0.0.0.0"
    server_port: int = 8000
    debug_mode: bool = False

    # Logging
    log_level: str = "INFO"
    log_file: str = "logs/agente.log"
    
    agent_prompt_path: str | None = "prompts/agent_system_optimized.md"


# Instância global de configurações
settings = Settings()
