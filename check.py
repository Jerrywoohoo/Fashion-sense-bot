"""Probe all Bedrock models and report SCP permissions."""

from __future__ import annotations

import os
import time

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from app.extractor import check_aws_credentials_configured

PROBE_MODELS = [
    # Amazon Nova Models
    "amazon.nova-micro-v1:0",
    "amazon.nova-lite-v1:0",
    "amazon.nova-pro-v1:0",
    "amazon.nova-premier-v1:0",
    # Anthropic Claude Profiles
    "us.anthropic.claude-3-haiku-20240307-v1:0",
    "us.anthropic.claude-3-sonnet-20240229-v1:0",
    "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    "us.anthropic.claude-opus-4-6-v1",
]


def main() -> None:
    region = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
    if not check_aws_credentials_configured():
        print("AWS credentials not configured in environment.")
        return

    client = boto3.Session(
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
        aws_session_token=os.getenv("AWS_SESSION_TOKEN") or None,
        region_name=region,
    ).client("bedrock-runtime")

    print(f"{'Model ID':<48} | {'Status':<10} | {'Details'}")
    print("-" * 80)

    for model_id in PROBE_MODELS:
        started = time.perf_counter()
        try:
            client.converse(
                modelId=model_id,
                messages=[{"role": "user", "content": [{"text": "1"}]}],
                inferenceConfig={"maxTokens": 1, "temperature": 0},
            )
            elapsed_ms = (time.perf_counter() - started) * 1000
            print(f"{model_id:<48} | \033[92mALLOWED\033[0m    | {elapsed_ms:.0f} ms")
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "ClientError")
            msg = exc.response.get("Error", {}).get("Message", "")
            if "explicit deny in a service control policy" in msg.lower():
                detail = "Blocked by Organization SCP"
            elif code == "AccessDeniedException":
                detail = "Model access not enabled / No IAM permission"
            elif code == "ResourceNotFoundException":
                detail = "Model ID not found in this region"
            else:
                detail = code
            print(f"{model_id:<48} | \033[91mDENIED\033[0m     | {detail}")
        except BotoCoreError as exc:
            print(f"{model_id:<48} | \033[91mERROR\033[0m      | {exc}")


if __name__ == "__main__":
    main()