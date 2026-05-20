import json

import httpx


FEISHU_API_BASE_URL = "https://open.feishu.cn"


def get_tenant_access_token(
    *,
    client: httpx.Client,
    app_id: str,
    app_secret: str,
    api_base_url: str = FEISHU_API_BASE_URL,
) -> str:
    response = client.post(
        f"{api_base_url}/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": app_id, "app_secret": app_secret},
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") not in {0, None}:
        raise RuntimeError(f"Feishu token request failed: {payload}")
    token = payload.get("tenant_access_token")
    if not token:
        raise RuntimeError("Feishu token response did not include tenant_access_token")
    return str(token)


def reply_text_message(
    *,
    client: httpx.Client,
    tenant_access_token: str,
    message_id: str,
    text: str,
    api_base_url: str = FEISHU_API_BASE_URL,
) -> None:
    response = client.post(
        f"{api_base_url}/open-apis/im/v1/messages/{message_id}/reply",
        headers={"Authorization": f"Bearer {tenant_access_token}"},
        json={
            "msg_type": "text",
            "content": json.dumps({"text": text}),
        },
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") not in {0, None}:
        raise RuntimeError(f"Feishu reply failed: {payload}")
