#!/usr/bin/env python3
import os
import sys

print("=== Environment Variable Test ===")
print(f"SLACK_BOT_TOKEN: {os.environ.get('SLACK_BOT_TOKEN', 'NOT_SET')}")
print(f"SLACK_APP_TOKEN: {os.environ.get('SLACK_APP_TOKEN', 'NOT_SET')}")

try:
    from slack_sdk.web import WebClient
    
    bot_token = os.environ.get('SLACK_BOT_TOKEN')
    if bot_token:
        print("\n=== Testing Bot Token ===")
        client = WebClient(token=bot_token)
        
        # Test auth
        response = client.auth_test()
        if response["ok"]:
            print(f"✅ Bot token is valid!")
            print(f"Bot User ID: {response['user_id']}")
            print(f"Team: {response['team']}")
        else:
            print(f"❌ Bot token test failed: {response}")
    else:
        print("❌ SLACK_BOT_TOKEN not found")
        
    app_token = os.environ.get('SLACK_APP_TOKEN')
    if app_token:
        print("\n=== Testing App Token ===")
        # Try to make a connections.open call
        try:
            response = client.apps_connections_open(app_token=app_token)
            if response["ok"]:
                print("✅ App token is valid!")
            else:
                print(f"❌ App token test failed: {response}")
        except Exception as e:
            print(f"❌ App token error: {e}")
    else:
        print("❌ SLACK_APP_TOKEN not found")
        
except ImportError:
    print("❌ slack_sdk not available")
except Exception as e:
    print(f"❌ Error testing tokens: {e}")
