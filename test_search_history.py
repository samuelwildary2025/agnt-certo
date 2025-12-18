#!/usr/bin/env python3
"""
Teste da nova função search_message_history
"""
import os
import sys
sys.path.append('.')

from tools.time_tool import search_message_history

def test_search_history():
    """Testa a função de busca de histórico"""
    print("🧪 Testando search_message_history...")
    
    # Teste 1: Busca geral
    print("\n1️⃣ Teste: Buscar todas as mensagens recentes")
    resultado = search_message_history("5511999998888")
    print(f"Resultado:\n{resultado}")
    
    # Teste 2: Busca com palavra-chave
    print("\n2️⃣ Teste: Buscar mensagens sobre 'arroz'")
    resultado = search_message_history("5511999998888", "arroz")
    print(f"Resultado:\n{resultado}")
    
    # Teste 3: Busca com palavra-chave inexistente
    print("\n3️⃣ Teste: Buscar mensagens sobre 'computador'")
    resultado = search_message_history("5511999998888", "computador")
    print(f"Resultado:\n{resultado}")

if __name__ == "__main__":
    test_search_history()