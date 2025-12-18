#!/usr/bin/env python3
"""
Teste específico para GPT-5-mini com a configuração exata do agente
"""
import os
import sys

# Add the project directory to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def test_gpt5_mini_configuration():
    """Testa a configuração exata do GPT-5-mini usada no agente"""
    
    # Get API key from environment
    openai_api_key = os.getenv('OPENAI_API_KEY', 'your-openai-api-key-here')
    
    if openai_api_key == 'your-openai-api-key-here':
        print("⚠️  WARNING: Usando API key de placeholder.")
        print("💡 Para testar com API real, configure a variável de ambiente:")
        print("   export OPENAI_API_KEY='sua-api-key-real'")
        return False
    
    try:
        print("🧪 Testando configuração GPT-5-mini exata do agente...")
        print(f"📝 Modelo: gpt-5-mini")
        print(f"🌡️  Temperature: 0.0")
        
        # Importar a configuração exata do agente
        from langchain_openai import ChatOpenAI
        
        # Esta é a configuração exata que o agente usa
        llm = ChatOpenAI(
            model="gpt-5-mini",
            openai_api_key=openai_api_key,
            temperature=0.0
        )
        
        # Testar uma mensagem de supermercado em português
        test_message = "Vou querer um pacote de sal e um arroz branco"
        
        print(f"📤 Enviando mensagem: '{test_message}'")
        response = llm.invoke(test_message)
        
        print("✅ SUCESSO: GPT-5-mini está funcionando corretamente!")
        print(f"📨 Resposta: {response.content}")
        return True
        
    except Exception as e:
        print(f"❌ ERRO: Falha na configuração GPT-5-mini!")
        print(f"📝 Detalhes do erro: {str(e)}")
        
        # Verificar se é o erro de max_tokens que estava causando problemas
        if "max_tokens" in str(e):
            print("🔧 Este é o erro de max_tokens que estávamos tentando corrigir.")
            print("💡 O modelo GPT-5-mini não suporta o parâmetro max_tokens.")
        elif "gpt-5-mini" in str(e) and "does not exist" in str(e):
            print("🔧 O modelo GPT-5-mini pode não estar disponível para sua API key.")
            print("💡 Verifique se você tem acesso ao GPT-5-mini ou use gpt-4o-mini como alternativa.")
        
        return False

def test_agent_llm_function():
    """Testa a função _build_llm do agente diretamente"""
    
    try:
        print("\n🧪 Testando a função _build_llm do agente...")
        
        # Mock das configurações
        class MockSettings:
            llm_provider = "openai"
            llm_model = "gpt-5-mini"
            llm_temperature = 0.0
            openai_api_key = os.getenv('OPENAI_API_KEY', 'your-openai-api-key-here')
        
        # Substituir temporariamente as configurações
        import config.settings
        original_settings = config.settings.settings
        config.settings.settings = MockSettings()
        
        # Importar e testar a função do agente
        from agent_langgraph_simple import _build_llm
        
        llm = _build_llm()
        print("✅ Função _build_llm executou com sucesso!")
        
        # Testar com uma mensagem
        test_message = "Lista de compras: arroz, feijão, macarrão"
        response = llm.invoke(test_message)
        
        print("✅ LLM do agente está respondendo corretamente!")
        print(f"📨 Resposta: {response.content[:150]}...")
        
        # Restaurar configurações originais
        config.settings.settings = original_settings
        
        return True
        
    except Exception as e:
        print(f"❌ ERRO: Falha na função _build_llm!")
        print(f"📝 Detalhes do erro: {str(e)}")
        return False

if __name__ == "__main__":
    print("🚀 Teste Específico GPT-5-mini para WhatsApp Agent")
    print("=" * 60)
    
    # Testar configuração direta
    success1 = test_gpt5_mini_configuration()
    
    # Testar função do agente (se tiver API key real)
    if os.getenv('OPENAI_API_KEY') and os.getenv('OPENAI_API_KEY') != 'your-openai-api-key-here':
        success2 = test_agent_llm_function()
    else:
        print("\n⏭️  Pulando teste da função do agente (sem API key real)")
        success2 = None
    
    print("\n" + "=" * 60)
    print("📊 Resultados dos Testes:")
    print(f"Configuração GPT-5-mini: {'✅ PASS' if success1 else '❌ FAIL'}")
    if success2 is not None:
        print(f"Função _build_llm do agente: {'✅ PASS' if success2 else '❌ FAIL'}")
    
    if success1:
        print("\n🎉 SUCESSO: A configuração GPT-5-mini está correta!")
        print("💬 Seu agente de WhatsApp deve funcionar com GPT-5-mini.")
    else:
        print("\n⚠️  O teste falhou. Verifique a mensagem de erro acima.")
        print("💡 Se estiver usando API key de placeholder, isso é esperado.")