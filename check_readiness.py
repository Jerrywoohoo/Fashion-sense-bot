"""Check AWS Bedrock readiness without starting the Telegram bot.

Run after configuring AWS credentials in ``.env``:
    python check_readiness.py
"""
from __future__ import annotations

import os
import time

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from app.extractor import DEFAULT_MODEL_ID, check_aws_credentials_configured


def main() -> None:
    """Print credential status and make one minimal Bedrock Converse request."""
    region = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
    configured = check_aws_credentials_configured()
    print(f"Region: {region}")
    print(f"Environment credentials configured: {'yes' if configured else 'no'}")
    if not configured:
        print("Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY in .env, then rerun.")
        return

    try:
        client = boto3.Session(
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
            aws_session_token=os.getenv("AWS_SESSION_TOKEN") or None,
            region_name=region,
        ).client("bedrock-runtime")
        started = time.perf_counter()
        response = client.converse(
            modelId=DEFAULT_MODEL_ID,
            messages=[{"role": "user", "content": [{"text": "Reply with the word ready."}]}],
            inferenceConfig={"maxTokens": 16, "temperature": 0},
        )
        elapsed_ms = (time.perf_counter() - started) * 1000
    except (BotoCoreError, ClientError) as exc:
        print(f"Bedrock readiness: FAILED\n{type(exc).__name__}: {exc}")
        return

    usage = response.get("usage", {})
    print("Bedrock readiness: READY")
    print(f"Model: {DEFAULT_MODEL_ID}")
    print(f"Latency: {elapsed_ms:.0f} ms")
    print(
        "Token usage: "
        f"input={usage.get('inputTokens', 'unknown')}, "
        f"output={usage.get('outputTokens', 'unknown')}, "
        f"total={usage.get('totalTokens', 'unknown')}"
    )


if __name__ == "__main__":
    main()
