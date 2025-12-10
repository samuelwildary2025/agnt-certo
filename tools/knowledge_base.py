import os
import json
import psycopg2
from typing import List, Dict
from openai import OpenAI
from config.settings import settings
from config.logger import setup_logger

logger = setup_logger(__name__)

# Cliente OpenAI para gerar embeddings da CONSULTA
client = OpenAI(api_key=settings.openai_api_key)

def get_embedding(text: str) -> List[float]:
    """Gera o embedding para o texto usando o modelo da OpenAI."""
    text = text.replace("\n", " ")
    return client.embeddings.create(input=[text], model="text-embedding-3-small").data[0].embedding

def retrieve_knowledge(query: str, match_threshold: float = 0.5, match_count: int = 5) -> str:
    """
    Busca informações relevantes na Base de Conhecimento (Supabase) usando busca vetorial.
    Retorna uma string formatada para ser injetada no prompt.
    """
    if not query:
        return ""

    try:
        # 1. Gerar embedding da consulta (necessário para comparar com o banco)
        query_embedding = get_embedding(query)
        
        # 2. Conectar ao banco
        conn = psycopg2.connect(settings.postgres_connection_string)
        cur = conn.cursor()
        
        # 3. Chamar a função RPC match_knowledge
        embedding_str = str(query_embedding)
        cur.callproc('match_knowledge', (embedding_str, match_threshold, match_count))
        
        results = cur.fetchall()
        
        cur.close()
        conn.close()
        
        if not results:
            return ""
            
        # 4. Formatar resultados
        formatted_context = []
        for row in results:
            content = row[1]
            formatted_context.append(f"- {content}")
            
        context_str = "\n".join(formatted_context)
        logger.info(f"🧠 Conhecimento recuperado (Vetor): {len(results)} itens para '{query[:20]}...'")
        
        return context_str

    except Exception as e:
        logger.error(f"Erro ao buscar conhecimento: {e}")
        return ""
