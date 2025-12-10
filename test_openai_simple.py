#!/usr/bin/env python3
"""
Simple test to verify OpenAI API configuration is working correctly
after removing max_tokens parameter for GPT-5-mini compatibility.
"""
import os
import sys
from langchain_openai import ChatOpenAI

def test_openai_only():
    """Test if OpenAI API configuration works without max_tokens parameter"""
    
    # Get API key from environment
    openai_api_key = os.getenv('OPENAI_API_KEY', 'your-openai-api-key-here')
    
    if openai_api_key == 'your-openai-api-key-here':
        print("⚠️  WARNING: Using placeholder API key.")
        print("💡 To test with real API, set OPENAI_API_KEY environment variable:")
        print("   export OPENAI_API_KEY='your-actual-api-key'")
        print("   Then run: python test_openai_simple.py")
        return False
    
    try:
        print("🧪 Testing OpenAI GPT-5-mini configuration without max_tokens...")
        
        # This is the exact configuration from agent_langgraph_simple.py
        llm = ChatOpenAI(
            model="gpt-5-mini",
            openai_api_key=openai_api_key,
            temperature=0.0
        )
        
        # Test a supermarket-related message
        test_message = "Vou querer um pacote de sal e um arroz branco"
        
        print(f"📤 Sending message: '{test_message}'")
        response = llm.invoke(test_message)
        
        print("✅ SUCCESS: OpenAI API configuration is working!")
        print(f"📨 Response: {response.content}")
        return True
        
    except Exception as e:
        print(f"❌ ERROR: OpenAI API configuration failed!")
        print(f"📝 Error details: {str(e)}")
        
        # Check if it's the max_tokens error we were trying to fix
        if "max_tokens" in str(e):
            print("🔧 This is the max_tokens parameter error we were trying to fix.")
            print("💡 The error suggests we need to adjust the configuration further.")
        elif "gpt-5-mini" in str(e):
            print("🔧 There might be an issue with the GPT-5-mini model.")
            print("💡 Consider using gpt-4o-mini or gpt-3.5-turbo instead.")
        
        return False

if __name__ == "__main__":
    print("🚀 Testing OpenAI Configuration for WhatsApp Agent")
    print("=" * 60)
    
    success = test_openai_only()
    
    print("\n" + "=" * 60)
    if success:
        print("🎉 SUCCESS: The OpenAI API configuration is working correctly!")
        print("💬 Your WhatsApp agent should now be able to process messages")
        print("   without the max_tokens error.")
    else:
        print("⚠️  The test failed. Please check the error message above.")
        print("💡 If you're using a placeholder API key, the test will fail")
        print("   but this is expected behavior.")
        print("\n🔑 To test with a real API key:")
        print("   1. Set your OPENAI_API_KEY environment variable")
        print("   2. Run this test again")