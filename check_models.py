from __future__ import annotations

import os
import sys

from google import genai


def main() -> int:
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        print("Error: GEMINI_API_KEY or GOOGLE_API_KEY environment variable is not set")
        return 1

    client = genai.Client(api_key=api_key)
    print("利用可能なGeminiモデル一覧を確認しています...\n")
    try:
        for model in client.models.list():
            name = getattr(model, "name", "")
            actions = getattr(model, "supported_actions", None)
            if actions:
                print("{0} | {1}".format(name, actions))
            else:
                print(name)
        print("\n確認完了")
        return 0
    except Exception as exc:
        print("エラーが発生しました: {0}".format(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
