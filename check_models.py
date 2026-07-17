import google.generativeai as genai
import os

# Load API key from environment variable
api_key = os.getenv("GOOGLE_API_KEY")
if not api_key:
    print("Error: GOOGLE_API_KEY environment variable not set")
    exit(1)

genai.configure(api_key=api_key)

print("利用可能なモデル一覧を調べています...\n")
try:
    for m in genai.list_models():
        if 'generateContent' in m.supported_generation_methods:
            print(m.name)
    print("\n確認完了！")
except Exception as e:
    print(f"エラーが発生しました: {e}")
