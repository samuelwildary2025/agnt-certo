#!/usr/bin/env python3
"""
Test script to verify OpenAI API configuration is working correctly
after removing max_tokens parameter for GPT-5-mini compatibility.
"""
import os
import sys
from langchain_openai import ChatOpenAI

# Add the project directory to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def test_openai_configuration():
    """Test if OpenAI API configuration works without max_tokens parameter"""
    
    # Get API key from environment
    openai_api_key = os.getenv('OPENAI_API_KEY', 'your-openai-api-key-here')
    
    if openai_api_key == 'your-openai-api-key-here':
        print("⚠️  WARNING: Using placeholder API key. Please set OPENAI_API_KEY environment variable.")
        return False
    
    try:
        # Test the configuration that was causing issues
        print("🧪 Testing OpenAI configuration without max_tokens parameter...")
        
        # This is the configuration from agent_langgraph_simple.py
        llm = ChatOpenAI(
            model="gpt-5-mini",
            openai_api_key=openai_api_key,
            temperature=0.0
        )
        
        # Test a simple message
        test_message = "Olá, você pode me ajudar a encontrar produtos de supermercado?"
        
        print("📤 Sending test message to OpenAI...")
        response = llm.invoke(test_message)
        
        print("✅ SUCCESS: OpenAI API configuration is working!")
        print(f"📨 Response: {response.content[:100]}...")
        return True
        
    except Exception as e:
        print(f"❌ ERROR: OpenAI API configuration failed!")
        print(f"📝 Error details: {str(e)}")
        
        # Check if it's the max_tokens error we were trying to fix
        if "max_tokens" in str(e):
            print("🔧 This is the max_tokens parameter error we were trying to fix.")
            print("💡 The error suggests the model doesn't support max_tokens parameter.")
        
        return False

def test_agent_tools():
    """Test if we can import and use the agent tools"""
    try:
        print("\n🧪 Testing agent tools import...")
        
        # Try to import the tools module
        from tools import (
            push_message_to_buffer,
            get_buffer_length,
            pop_all_messages,
            set_agent_cooldown,
            is_agent_in_cooldown
        )
        
        print("✅ SUCCESS: Agent tools imported successfully!")
        
        # Test buffer functions
        print("📋 Testing Redis buffer functions...")
        push_message_to_buffer("test_user", "test message")
        buffer_length = get_buffer_length("test_user")
        print(f"📊 Buffer length: {buffer_length}")
        
        messages = pop_all_messages("test_user")
        print(f"📨 Retrieved messages: {messages}")
        
        return True
        
    except Exception as e:
        print(f"❌ ERROR: Agent tools import failed!")
        print(f"📝 Error details: {str(e)}")
        return False

if __name__ == "__main__":
    print("🚀 Testing WhatsApp Agent Configuration")
    print("=" * 50)
    
    # Test OpenAI configuration
    openai_success = test_openai_configuration()
    
    # Test agent tools (will fail if Redis is not running, but that's expected)
    tools_success = test_agent_tools()
    
    print("\n" + "=" * 50)
    print("📊 Test Results:")
    print(f"OpenAI Configuration: {'✅ PASS' if openai_success else '❌ FAIL'}")
    print(f"Agent Tools: {'✅ PASS' if tools_success else '❌ FAIL (Redis not running)'}")
    
    if openai_success:
        print("\n🎉 The OpenAI API configuration is working correctly!")
        print("💬 Your WhatsApp agent should now be able to process messages without the max_tokens error.")
    else:
        print("\n⚠️  Please check your OpenAI API key and configuration.")