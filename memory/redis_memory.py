import json
import time
from typing import List, Optional
from langchain_core.chat_history import BaseChatMessageHistory
from langchain_core.messages import (
    BaseMessage,
    HumanMessage,
    AIMessage,
    SystemMessage,
    messages_from_dict,
    message_to_dict
)
from config.settings import settings
import redis

class RedisChatMessageHistory(BaseChatMessageHistory):
    """
    Histórico de chat baseado em Redis com TTL estrito (Sessão).
    
    Lógica:
    - Armazena todas as mensagens da sessão atual em uma lista Redis.
    - TTL de 15 minutos (900s): renovado a cada interação.
    - Se o TTL expirar, a memória é apagada automaticamente (fim da sessão).
    """
    
    def __init__(self, session_id: str, ttl: int = 900):
        self.session_id = session_id
        self.key = f"session:memory:{session_id}"
        self.ttl = ttl
        
        # Conexão Redis
        self.redis_client = redis.from_url(settings.redis_url, decode_responses=True)

    @property
    def messages(self) -> List[BaseMessage]:
        """Recupera todas as mensagens da sessão atual do Redis."""
        try:
            # Ler lista completa
            raw_messages = self.redis_client.lrange(self.key, 0, -1)
            if not raw_messages:
                return []
            
            # Converter JSON -> Dict -> Messages
            messages_dicts = [json.loads(m) for m in raw_messages]
            return messages_from_dict(messages_dicts)
            
        except Exception as e:
            print(f"❌ Erro ao ler memória Redis para {self.session_id}: {e}")
            return []

    def add_message(self, message: BaseMessage) -> None:
        """Adiciona uma mensagem à sessão e renova o TTL."""
        try:
            # Converter Message -> Dict -> JSON
            msg_dict = message_to_dict(message)
            
            # CRITICAL: Remove thinking blocks to avoid Claude/Anthropic signature issues
            # When extended thinking is enabled, Claude returns thinking blocks with a
            # 'signature' field. If this field is missing when sending history back to
            # the API, it causes HTTP 400: "thinking.signature: Field required"
            # Since we're using Grok as main model, we can safely strip these blocks.
            if isinstance(msg_dict.get("data", {}).get("content"), list):
                msg_dict["data"]["content"] = [
                    block for block in msg_dict["data"]["content"]
                    if not isinstance(block, dict) or block.get("type") != "thinking"
                ]
            
            msg_json = json.dumps(msg_dict)
            
            # Pipeline para atomicidade
            pipe = self.redis_client.pipeline()
            pipe.rpush(self.key, msg_json)
            pipe.expire(self.key, self.ttl) # Renova TTL (15min)
            pipe.execute()
            
        except Exception as e:
            print(f"❌ Erro ao salvar mensagem no Redis para {self.session_id}: {e}")

    def clear(self) -> None:
        """Limpa a memória da sessão explicitamente."""
        try:
            self.redis_client.delete(self.key)
        except Exception as e:
            print(f"❌ Erro ao limpar memória Redis para {self.session_id}: {e}")
