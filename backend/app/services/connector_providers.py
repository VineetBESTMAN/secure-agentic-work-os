from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.message import EmailMessage
import base64
import json
from time import perf_counter
from typing import Any
from urllib.parse import quote
from uuid import uuid4

import httpx

from app.core.config import get_settings

GOOGLE_GMAIL_BASE = "https://gmail.googleapis.com/gmail/v1"
GOOGLE_CALENDAR_BASE = "https://www.googleapis.com/calendar/v3"
SLACK_API_BASE = "https://slack.com/api"
GITHUB_API_BASE = "https://api.github.com"
NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_API_VERSION = "2026-03-11"

PROVIDER_CAPABILITIES: dict[str, dict[str, list[str]]] = {
    "google": {
        "resources": ["gmail", "calendar"],
        "actions": ["send_email", "create_calendar_event"],
    },
    "slack": {
        "resources": ["messages"],
        "actions": ["send_slack_message"],
    },
    "github": {
        "resources": ["issues"],
        "actions": ["create_github_issue"],
    },
    "jira": {
        "resources": ["issues"],
        "actions": ["create_jira_issue"],
    },
    "notion": {
        "resources": ["pages"],
        "actions": ["create_notion_page"],
    },
}


@dataclass(frozen=True)
class ProviderSyncItem:
    external_id: str
    title: str
    content: str
    source_url: str | None = None
    updated_at: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)
    deleted: bool = False


@dataclass(frozen=True)
class ProviderSyncBatch:
    items: list[ProviderSyncItem]
    cursor: str | None


def provider_resources(provider: str) -> list[str]:
    return list(PROVIDER_CAPABILITIES.get(provider, {}).get("resources", []))


def provider_actions(provider: str) -> list[str]:
    return list(PROVIDER_CAPABILITIES.get(provider, {}).get("actions", []))


async def probe_provider_access(
    *,
    provider: str,
    access_token: str,
    account_metadata: dict[str, object],
    expected_external_account_id: str | None,
) -> dict[str, object]:
    """Run read-only provider checks and return only non-content evidence."""
    timeout = get_settings().connector_request_timeout_seconds
    headers = _bearer_headers(access_token)
    checks: list[dict[str, object]] = []
    identity_match: bool | None = None

    async with httpx.AsyncClient(timeout=timeout) as client:
        if provider == "google":
            body = await _probe_endpoint(
                client, checks, "identity", "Google OAuth identity", "GET",
                "https://openidconnect.googleapis.com/v1/userinfo", headers=headers,
            )
            if body is not None:
                identity_match = str(body.get("sub") or "") == str(expected_external_account_id or "")
            await _probe_endpoint(
                client, checks, "gmail_read", "Gmail read access", "GET",
                f"{GOOGLE_GMAIL_BASE}/users/me/profile", headers=headers,
            )
            await _probe_endpoint(
                client, checks, "calendar_read", "Google Calendar access", "GET",
                f"{GOOGLE_CALENDAR_BASE}/calendars/primary", headers=headers,
            )
            await _probe_endpoint(
                client, checks, "drive_read", "Google Drive read access", "GET",
                "https://www.googleapis.com/drive/v3/files", headers=headers,
                params={"pageSize": 1, "fields": "files(id)"},
            )
        elif provider == "slack":
            body = await _probe_endpoint(
                client, checks, "identity", "Slack workspace identity", "POST",
                f"{SLACK_API_BASE}/auth.test", headers=headers, slack=True,
            )
            if body is not None:
                identity_match = str(body.get("team_id") or "") == str(expected_external_account_id or "")
            await _probe_endpoint(
                client, checks, "messages_read", "Slack conversation access", "GET",
                f"{SLACK_API_BASE}/conversations.list", headers=headers,
                params={"limit": 1, "types": "public_channel,private_channel"}, slack=True,
            )
        elif provider == "github":
            body = await _probe_endpoint(
                client, checks, "identity", "GitHub OAuth identity", "GET",
                f"{GITHUB_API_BASE}/user", headers=headers,
            )
            if body is not None:
                identity_match = str(body.get("id") or "") == str(expected_external_account_id or "")
            await _probe_endpoint(
                client, checks, "issues_read", "GitHub issue access", "GET",
                f"{GITHUB_API_BASE}/issues", headers=headers,
                params={"per_page": 1, "filter": "all"},
            )
        elif provider == "jira":
            body = await _probe_endpoint(
                client, checks, "identity", "Jira cloud identity", "GET",
                "https://api.atlassian.com/oauth/token/accessible-resources", headers=headers,
            )
            resources = body if isinstance(body, list) else []
            identity_match = any(
                str(resource.get("id") or "") == str(expected_external_account_id or "")
                for resource in resources if isinstance(resource, dict)
            ) if body is not None else None
            cloud_id = str(account_metadata.get("cloud_id") or "")
            await _probe_endpoint(
                client, checks, "user_read", "Jira user access", "GET",
                f"https://api.atlassian.com/ex/jira/{cloud_id}/rest/api/3/myself", headers=headers,
            )
            await _probe_endpoint(
                client, checks, "issues_read", "Jira issue access", "GET",
                f"https://api.atlassian.com/ex/jira/{cloud_id}/rest/api/3/search/jql", headers=headers,
                params={"maxResults": 1, "fields": "id"},
            )
        elif provider == "notion":
            notion_headers = {**headers, "Notion-Version": NOTION_API_VERSION}
            body = await _probe_endpoint(
                client, checks, "identity", "Notion integration identity", "GET",
                f"{NOTION_API_BASE}/users/me", headers=notion_headers,
            )
            bot_id = str(account_metadata.get("bot_id") or "")
            live_bot_id = str(body.get("id") or "") if isinstance(body, dict) else ""
            if live_bot_id and bot_id:
                identity_match = live_bot_id == bot_id
            elif live_bot_id and live_bot_id == str(expected_external_account_id or ""):
                identity_match = True
            await _probe_endpoint(
                client, checks, "pages_read", "Notion page access", "POST",
                f"{NOTION_API_BASE}/search", headers=notion_headers,
                json_body={"page_size": 1, "filter": {"property": "object", "value": "page"}},
            )
        else:
            raise ValueError("Unsupported connector provider.")

    return {"identity_match": identity_match, "checks": checks}


async def _probe_endpoint(
    client: httpx.AsyncClient,
    checks: list[dict[str, object]],
    key: str,
    label: str,
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    params: dict[str, object] | None = None,
    json_body: dict[str, object] | None = None,
    slack: bool = False,
) -> Any | None:
    started = perf_counter()
    body: Any | None = None
    status = "passed"
    message = "Provider accepted the read-only probe."
    http_status: int | None = None
    try:
        response = await client.request(
            method, url, headers=headers, params=params, json=json_body
        )
        http_status = response.status_code
        response.raise_for_status()
        body = response.json()
        if slack and (not isinstance(body, dict) or not body.get("ok")):
            status = "failed"
            message = "Slack rejected the read-only probe."
            body = None
    except httpx.HTTPStatusError as exc:
        http_status = exc.response.status_code
        status = "failed"
        message = f"Provider returned HTTP {http_status} for this read-only probe."
    except (httpx.HTTPError, ValueError, TypeError):
        status = "failed"
        message = "The read-only provider probe could not be completed."
        body = None
    latency_ms = round((perf_counter() - started) * 1000, 2)
    evidence: dict[str, object] = {"latency_ms": latency_ms}
    if http_status is not None:
        evidence["http_status"] = http_status
    checks.append(
        {"key": key, "label": label, "status": status, "message": message, "evidence": evidence}
    )
    return body


async def sync_provider_resource(
    *,
    provider: str,
    resource: str,
    access_token: str,
    cursor: str | None,
    account_metadata: dict[str, object],
) -> ProviderSyncBatch:
    if resource not in provider_resources(provider):
        raise ValueError(f"{resource} is not a supported {provider} sync resource.")
    timeout = get_settings().connector_request_timeout_seconds
    headers = _bearer_headers(access_token)
    async with httpx.AsyncClient(timeout=timeout) as client:
        if provider == "google" and resource == "gmail":
            return await _sync_gmail(client, headers, cursor)
        if provider == "google" and resource == "calendar":
            return await _sync_google_calendar(client, headers, cursor)
        if provider == "slack":
            return await _sync_slack(client, headers, cursor)
        if provider == "github":
            return await _sync_github(client, headers, cursor)
        if provider == "jira":
            return await _sync_jira(client, headers, cursor, account_metadata)
        if provider == "notion":
            return await _sync_notion(client, headers, cursor)
    raise ValueError(f"No sync adapter is available for {provider}/{resource}.")


def execute_provider_action(
    *,
    provider: str,
    action: str,
    access_token: str,
    account_metadata: dict[str, object],
    arguments: dict[str, object],
    idempotency_key: str,
) -> dict[str, object]:
    if action not in provider_actions(provider):
        raise ValueError(f"{action} is not a supported {provider} action.")
    timeout = get_settings().connector_request_timeout_seconds
    headers = _bearer_headers(access_token)
    with httpx.Client(timeout=timeout) as client:
        if provider == "google" and action == "send_email":
            return _send_gmail(client, headers, arguments, idempotency_key)
        if provider == "google" and action == "create_calendar_event":
            return _create_google_calendar_event(client, headers, arguments)
        if provider == "slack":
            return _send_slack_message(client, headers, arguments, idempotency_key)
        if provider == "github":
            return _create_github_issue(client, headers, arguments)
        if provider == "jira":
            return _create_jira_issue(client, headers, account_metadata, arguments)
        if provider == "notion":
            return _create_notion_page(client, headers, arguments)
    raise ValueError(f"No action adapter is available for {provider}/{action}.")


async def register_provider_webhook(
    *,
    provider: str,
    resource: str,
    target: str | None,
    callback_url: str,
    secret: str,
    access_token: str,
    account_metadata: dict[str, object],
) -> tuple[str | None, str, str | None]:
    """Register remotely where the provider supports API registration.

    Slack and Notion manage callback URLs at the app/integration level, so those
    subscriptions intentionally return manual mode with an actionable message.
    """
    timeout = get_settings().connector_request_timeout_seconds
    headers = _bearer_headers(access_token)
    async with httpx.AsyncClient(timeout=timeout) as client:
        if provider == "github":
            if not target or "/" not in target:
                raise ValueError("GitHub webhook registration needs target owner/repository.")
            response = await client.post(
                f"{GITHUB_API_BASE}/repos/{target.strip('/')}/hooks",
                headers=headers,
                json={
                    "name": "web",
                    "active": True,
                    "events": ["issues", "issue_comment", "push"],
                    "config": {
                        "url": callback_url,
                        "content_type": "json",
                        "secret": secret,
                        "insecure_ssl": "0",
                    },
                },
            )
            response.raise_for_status()
            body = response.json()
            return str(body.get("id") or ""), "remote", body.get("url")

        if provider == "google" and resource == "gmail":
            if not target:
                raise ValueError("Gmail watch registration needs a Google Pub/Sub topic target.")
            response = await client.post(
                f"{GOOGLE_GMAIL_BASE}/users/me/watch",
                headers=headers,
                json={"topicName": target, "labelIds": ["INBOX"]},
            )
            response.raise_for_status()
            body = response.json()
            return str(body.get("historyId") or ""), "remote", body.get("expiration")

        if provider == "google" and resource == "calendar":
            calendar_id = target or "primary"
            remote_id = f"workos-{uuid4().hex}"
            response = await client.post(
                f"{GOOGLE_CALENDAR_BASE}/calendars/{quote(calendar_id, safe='')}/events/watch",
                headers=headers,
                json={
                    "id": remote_id,
                    "type": "web_hook",
                    "address": callback_url,
                    "token": secret,
                },
            )
            response.raise_for_status()
            body = response.json()
            return str(body.get("id") or remote_id), "remote", body.get("expiration")

        if provider == "jira":
            cloud_id = _required_metadata(account_metadata, "cloud_id", "Jira cloud ID")
            response = await client.post(
                f"https://api.atlassian.com/ex/jira/{cloud_id}/rest/api/3/webhook",
                headers=headers,
                json={
                    "url": callback_url,
                    "webhooks": [
                        {
                            "jqlFilter": target or "project IS NOT EMPTY",
                            "events": ["jira:issue_created", "jira:issue_updated"],
                        }
                    ],
                },
            )
            response.raise_for_status()
            body = response.json()
            registered = body.get("webhookRegistrationResult") or []
            remote_id = registered[0].get("createdWebhookId") if registered else None
            return str(remote_id or ""), "remote", None

    message = (
        "Configure this callback URL and shared secret in the provider's app console."
    )
    return None, "manual", message


async def _sync_gmail(
    client: httpx.AsyncClient, headers: dict[str, str], cursor: str | None
) -> ProviderSyncBatch:
    message_ids: list[str] = []
    next_cursor = cursor
    if cursor:
        response = await client.get(
            f"{GOOGLE_GMAIL_BASE}/users/me/history",
            headers=headers,
            params={
                "startHistoryId": cursor,
                "historyTypes": "messageAdded",
                "maxResults": get_settings().connector_sync_max_items,
            },
        )
        response.raise_for_status()
        body = response.json()
        for history in body.get("history", []):
            for added in history.get("messagesAdded", []):
                message_id = (added.get("message") or {}).get("id")
                if message_id:
                    message_ids.append(str(message_id))
        next_cursor = str(body.get("historyId") or cursor)
    else:
        response = await client.get(
            f"{GOOGLE_GMAIL_BASE}/users/me/messages",
            headers=headers,
            params={"maxResults": get_settings().connector_sync_max_items},
        )
        response.raise_for_status()
        message_ids = [
            str(item["id"])
            for item in response.json().get("messages", [])
            if item.get("id")
        ]
        profile = await client.get(
            f"{GOOGLE_GMAIL_BASE}/users/me/profile", headers=headers
        )
        profile.raise_for_status()
        next_cursor = str(profile.json().get("historyId") or "") or None

    items: list[ProviderSyncItem] = []
    for message_id in list(dict.fromkeys(message_ids))[: get_settings().connector_sync_max_items]:
        response = await client.get(
            f"{GOOGLE_GMAIL_BASE}/users/me/messages/{message_id}",
            headers=headers,
            params={"format": "full"},
        )
        response.raise_for_status()
        items.append(_gmail_item(response.json()))
    return ProviderSyncBatch(items=items, cursor=next_cursor)


async def _sync_google_calendar(
    client: httpx.AsyncClient, headers: dict[str, str], cursor: str | None
) -> ProviderSyncBatch:
    params: dict[str, object] = {
        "maxResults": get_settings().connector_sync_max_items,
        "singleEvents": True,
        "showDeleted": True,
    }
    if cursor:
        params["syncToken"] = cursor
    else:
        params["timeMin"] = datetime.now(timezone.utc).isoformat()
    response = await client.get(
        f"{GOOGLE_CALENDAR_BASE}/calendars/primary/events",
        headers=headers,
        params=params,
    )
    response.raise_for_status()
    body = response.json()
    items = []
    for event in body.get("items", []):
        event_id = event.get("id")
        if not event_id:
            continue
        start = (event.get("start") or {}).get("dateTime") or (event.get("start") or {}).get("date")
        end = (event.get("end") or {}).get("dateTime") or (event.get("end") or {}).get("date")
        title = event.get("summary") or "Untitled calendar event"
        content = "\n".join(
            part
            for part in (
                f"Event: {title}",
                f"Start: {start}" if start else "",
                f"End: {end}" if end else "",
                str(event.get("description") or ""),
                f"Location: {event.get('location')}" if event.get("location") else "",
            )
            if part
        )
        items.append(
            ProviderSyncItem(
                external_id=str(event_id),
                title=str(title),
                content=content,
                source_url=event.get("htmlLink"),
                updated_at=event.get("updated"),
                metadata={"status": event.get("status") or "confirmed"},
                deleted=event.get("status") == "cancelled",
            )
        )
    return ProviderSyncBatch(
        items=items,
        cursor=str(body.get("nextSyncToken") or cursor or "") or None,
    )


async def _sync_slack(
    client: httpx.AsyncClient, headers: dict[str, str], cursor: str | None
) -> ProviderSyncBatch:
    channels_response = await client.get(
        f"{SLACK_API_BASE}/conversations.list",
        headers=headers,
        params={"types": "public_channel,private_channel", "limit": 50},
    )
    channels_response.raise_for_status()
    channels_body = channels_response.json()
    _require_slack_ok(channels_body)
    items: list[ProviderSyncItem] = []
    newest = cursor or "0"
    for channel in channels_body.get("channels", [])[:20]:
        if len(items) >= get_settings().connector_sync_max_items:
            break
        channel_id = channel.get("id")
        if not channel_id:
            continue
        response = await client.get(
            f"{SLACK_API_BASE}/conversations.history",
            headers=headers,
            params={
                "channel": channel_id,
                "oldest": cursor or "0",
                "inclusive": False,
                "limit": min(100, get_settings().connector_sync_max_items - len(items)),
            },
        )
        response.raise_for_status()
        body = response.json()
        _require_slack_ok(body)
        for message in body.get("messages", []):
            ts = str(message.get("ts") or "")
            if not ts:
                continue
            newest = max(newest, ts, key=lambda value: float(value or 0))
            text = str(message.get("text") or "")
            items.append(
                ProviderSyncItem(
                    external_id=f"{channel_id}:{ts}",
                    title=f"#{channel.get('name') or channel_id} message",
                    content=text or "Slack message without text content",
                    source_url=None,
                    updated_at=_slack_timestamp(ts),
                    metadata={
                        "channel_id": channel_id,
                        "channel_name": channel.get("name") or "",
                        "user": message.get("user") or "",
                    },
                    deleted=message.get("subtype") == "message_deleted",
                )
            )
    return ProviderSyncBatch(items=items, cursor=newest if newest != "0" else cursor)


async def _sync_github(
    client: httpx.AsyncClient, headers: dict[str, str], cursor: str | None
) -> ProviderSyncBatch:
    params: dict[str, object] = {
        "filter": "all",
        "state": "all",
        "sort": "updated",
        "direction": "asc",
        "per_page": min(100, get_settings().connector_sync_max_items),
    }
    if cursor:
        params["since"] = cursor
    response = await client.get(f"{GITHUB_API_BASE}/issues", headers=headers, params=params)
    response.raise_for_status()
    issues = response.json()
    items = []
    next_cursor = cursor
    for issue in issues:
        issue_id = issue.get("id")
        if not issue_id:
            continue
        updated_at = issue.get("updated_at")
        if updated_at and (not next_cursor or str(updated_at) > next_cursor):
            next_cursor = str(updated_at)
        items.append(
            ProviderSyncItem(
                external_id=str(issue_id),
                title=str(issue.get("title") or "Untitled GitHub issue"),
                content="\n".join(
                    part
                    for part in (
                        str(issue.get("title") or ""),
                        str(issue.get("body") or ""),
                        f"State: {issue.get('state')}" if issue.get("state") else "",
                    )
                    if part
                ),
                source_url=issue.get("html_url"),
                updated_at=updated_at,
                metadata={
                    "number": issue.get("number"),
                    "repository_url": issue.get("repository_url") or "",
                    "state": issue.get("state") or "",
                },
            )
        )
    return ProviderSyncBatch(items=items, cursor=next_cursor)


async def _sync_jira(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    cursor: str | None,
    metadata: dict[str, object],
) -> ProviderSyncBatch:
    cloud_id = _required_metadata(metadata, "cloud_id", "Jira cloud ID")
    jql = "ORDER BY updated ASC"
    if cursor:
        jql = f'updated >= "{cursor[:19].replace("T", " ")}" ORDER BY updated ASC'
    response = await client.get(
        f"https://api.atlassian.com/ex/jira/{cloud_id}/rest/api/3/search/jql",
        headers=headers,
        params={
            "jql": jql,
            "maxResults": min(100, get_settings().connector_sync_max_items),
            "fields": "summary,description,status,updated,project,issuetype",
        },
    )
    response.raise_for_status()
    items = []
    next_cursor = cursor
    for issue in response.json().get("issues", []):
        fields = issue.get("fields") or {}
        updated_at = fields.get("updated")
        if updated_at and (not next_cursor or str(updated_at) > next_cursor):
            next_cursor = str(updated_at)
        key = issue.get("key") or issue.get("id")
        if not key:
            continue
        base_url = str(metadata.get("site_url") or "").rstrip("/")
        items.append(
            ProviderSyncItem(
                external_id=str(issue.get("id") or key),
                title=f"{key}: {fields.get('summary') or 'Untitled Jira issue'}",
                content="\n".join(
                    part
                    for part in (
                        str(fields.get("summary") or ""),
                        _plain_text(fields.get("description")),
                        f"Status: {(fields.get('status') or {}).get('name', '')}",
                    )
                    if part
                ),
                source_url=f"{base_url}/browse/{key}" if base_url else None,
                updated_at=updated_at,
                metadata={"key": key, "status": (fields.get("status") or {}).get("name", "")},
            )
        )
    return ProviderSyncBatch(items=items, cursor=next_cursor)


async def _sync_notion(
    client: httpx.AsyncClient, headers: dict[str, str], cursor: str | None
) -> ProviderSyncBatch:
    notion_headers = {**headers, "Notion-Version": NOTION_API_VERSION}
    response = await client.post(
        f"{NOTION_API_BASE}/search",
        headers=notion_headers,
        json={
            "filter": {"property": "object", "value": "page"},
            "sort": {"direction": "ascending", "timestamp": "last_edited_time"},
            "page_size": min(100, get_settings().connector_sync_max_items),
        },
    )
    response.raise_for_status()
    items = []
    next_cursor = cursor
    for page in response.json().get("results", []):
        updated_at = page.get("last_edited_time")
        if cursor and updated_at and str(updated_at) <= cursor:
            continue
        if updated_at and (not next_cursor or str(updated_at) > next_cursor):
            next_cursor = str(updated_at)
        title = _notion_title(page) or "Untitled Notion page"
        items.append(
            ProviderSyncItem(
                external_id=str(page.get("id")),
                title=title,
                content=f"Notion page: {title}\nURL: {page.get('url') or ''}",
                source_url=page.get("url"),
                updated_at=updated_at,
                metadata={"archived": bool(page.get("archived"))},
                deleted=bool(page.get("archived") or page.get("in_trash")),
            )
        )
    return ProviderSyncBatch(items=items, cursor=next_cursor)


def _send_gmail(
    client: httpx.Client,
    headers: dict[str, str],
    arguments: dict[str, object],
    idempotency_key: str,
) -> dict[str, object]:
    message = EmailMessage()
    message["To"] = str(arguments["to"])
    message["Subject"] = str(arguments["subject"])
    message["Message-ID"] = f"<workos-{idempotency_key}@secure-work-os.local>"
    message.set_content(str(arguments.get("body") or ""))
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii").rstrip("=")
    response = client.post(
        f"{GOOGLE_GMAIL_BASE}/users/me/messages/send",
        headers=headers,
        json={"raw": raw},
    )
    response.raise_for_status()
    body = response.json()
    return {
        "delivery_mode": "provider",
        "provider": "google",
        "action": "send_email",
        "delivery_status": "sent",
        "external_id": str(body.get("id") or ""),
        "thread_id": str(body.get("threadId") or ""),
        "to": str(arguments["to"]),
        "subject": str(arguments["subject"]),
    }


def _create_google_calendar_event(
    client: httpx.Client,
    headers: dict[str, str],
    arguments: dict[str, object],
) -> dict[str, object]:
    attendees = [{"email": email} for email in arguments.get("attendees", [])]
    payload: dict[str, object] = {
        "summary": arguments["summary"],
        "description": arguments.get("description") or "",
        "start": {
            "dateTime": arguments["start"],
            "timeZone": arguments.get("timezone") or "UTC",
        },
        "end": {
            "dateTime": arguments["end"],
            "timeZone": arguments.get("timezone") or "UTC",
        },
    }
    if attendees:
        payload["attendees"] = attendees
    response = client.post(
        f"{GOOGLE_CALENDAR_BASE}/calendars/primary/events",
        headers=headers,
        params={"sendUpdates": "all" if attendees else "none"},
        json=payload,
    )
    response.raise_for_status()
    body = response.json()
    return {
        "delivery_mode": "provider",
        "provider": "google",
        "action": "create_calendar_event",
        "status": body.get("status") or "confirmed",
        "external_id": str(body.get("id") or ""),
        "url": body.get("htmlLink") or "",
        "summary": arguments["summary"],
    }


def _send_slack_message(
    client: httpx.Client,
    headers: dict[str, str],
    arguments: dict[str, object],
    idempotency_key: str,
) -> dict[str, object]:
    response = client.post(
        f"{SLACK_API_BASE}/chat.postMessage",
        headers=headers,
        json={
            "channel": arguments["channel"],
            "text": arguments["text"],
            "client_msg_id": idempotency_key,
        },
    )
    response.raise_for_status()
    body = response.json()
    _require_slack_ok(body)
    return {
        "delivery_mode": "provider",
        "provider": "slack",
        "action": "send_slack_message",
        "status": "sent",
        "external_id": str(body.get("ts") or ""),
        "channel": str(body.get("channel") or arguments["channel"]),
    }


def _create_github_issue(
    client: httpx.Client,
    headers: dict[str, str],
    arguments: dict[str, object],
) -> dict[str, object]:
    repository = str(arguments["repository"]).strip("/")
    if repository.count("/") != 1:
        raise ValueError("repository must use owner/name format.")
    payload: dict[str, object] = {
        "title": arguments["title"],
        "body": arguments.get("body") or "",
    }
    labels = arguments.get("labels") or []
    if labels:
        payload["labels"] = labels
    response = client.post(
        f"{GITHUB_API_BASE}/repos/{repository}/issues", headers=headers, json=payload
    )
    response.raise_for_status()
    body = response.json()
    return {
        "delivery_mode": "provider",
        "provider": "github",
        "action": "create_github_issue",
        "status": body.get("state") or "open",
        "external_id": str(body.get("id") or body.get("number") or ""),
        "number": body.get("number"),
        "url": body.get("html_url") or "",
    }


def _create_jira_issue(
    client: httpx.Client,
    headers: dict[str, str],
    metadata: dict[str, object],
    arguments: dict[str, object],
) -> dict[str, object]:
    cloud_id = _required_metadata(metadata, "cloud_id", "Jira cloud ID")
    description = str(arguments.get("description") or "")
    response = client.post(
        f"https://api.atlassian.com/ex/jira/{cloud_id}/rest/api/3/issue",
        headers=headers,
        json={
            "fields": {
                "project": {"key": arguments["project_key"]},
                "summary": arguments["summary"],
                "issuetype": {"name": arguments.get("issue_type") or "Task"},
                "description": {
                    "type": "doc",
                    "version": 1,
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [{"type": "text", "text": description or "Created by Secure Work OS"}],
                        }
                    ],
                },
            }
        },
    )
    response.raise_for_status()
    body = response.json()
    site_url = str(metadata.get("site_url") or "").rstrip("/")
    key = str(body.get("key") or "")
    return {
        "delivery_mode": "provider",
        "provider": "jira",
        "action": "create_jira_issue",
        "status": "created",
        "external_id": str(body.get("id") or key),
        "key": key,
        "url": f"{site_url}/browse/{key}" if site_url and key else "",
    }


def _create_notion_page(
    client: httpx.Client,
    headers: dict[str, str],
    arguments: dict[str, object],
) -> dict[str, object]:
    notion_headers = {**headers, "Notion-Version": NOTION_API_VERSION}
    parent_type = str(arguments.get("parent_type") or "page_id")
    if parent_type not in {"page_id", "database_id"}:
        raise ValueError("parent_type must be page_id or database_id.")
    title = str(arguments["title"])
    content = str(arguments.get("content") or "")
    payload: dict[str, object] = {
        "parent": {parent_type: arguments["parent_id"]},
        "properties": {
            "title": {"type": "title", "title": [{"type": "text", "text": {"content": title}}]}
        },
    }
    if content:
        payload["children"] = [
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {"rich_text": [{"type": "text", "text": {"content": content}}]},
            }
        ]
    response = client.post(f"{NOTION_API_BASE}/pages", headers=notion_headers, json=payload)
    response.raise_for_status()
    body = response.json()
    return {
        "delivery_mode": "provider",
        "provider": "notion",
        "action": "create_notion_page",
        "status": "created",
        "external_id": str(body.get("id") or ""),
        "url": body.get("url") or "",
    }


def _gmail_item(message: dict[str, Any]) -> ProviderSyncItem:
    headers = {
        str(header.get("name") or "").lower(): str(header.get("value") or "")
        for header in (message.get("payload") or {}).get("headers", [])
    }
    subject = headers.get("subject") or "Untitled email"
    body_text = _gmail_body(message.get("payload") or {}) or str(message.get("snippet") or "")
    content = "\n".join(
        part
        for part in (
            f"Subject: {subject}",
            f"From: {headers.get('from')}" if headers.get("from") else "",
            f"To: {headers.get('to')}" if headers.get("to") else "",
            body_text,
        )
        if part
    )
    internal_date = message.get("internalDate")
    updated_at = None
    if str(internal_date or "").isdigit():
        updated_at = datetime.fromtimestamp(
            int(internal_date) / 1000, tz=timezone.utc
        ).isoformat()
    return ProviderSyncItem(
        external_id=str(message.get("id")),
        title=subject,
        content=content,
        source_url=f"https://mail.google.com/mail/u/0/#all/{message.get('id')}",
        updated_at=updated_at,
        metadata={
            "thread_id": message.get("threadId") or "",
            "labels": message.get("labelIds") or [],
            "from": headers.get("from") or "",
        },
    )


def _gmail_body(payload: dict[str, Any]) -> str:
    mime_type = str(payload.get("mimeType") or "")
    data = (payload.get("body") or {}).get("data")
    if data and mime_type in {"text/plain", "text/html"}:
        try:
            padded = str(data) + "=" * (-len(str(data)) % 4)
            return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")
        except (ValueError, UnicodeDecodeError):
            return ""
    for part in payload.get("parts", []):
        text = _gmail_body(part)
        if text:
            return text
    return ""


def _notion_title(page: dict[str, Any]) -> str:
    for prop in (page.get("properties") or {}).values():
        if prop.get("type") != "title":
            continue
        return "".join(
            str(item.get("plain_text") or (item.get("text") or {}).get("content") or "")
            for item in prop.get("title", [])
        ).strip()
    return ""


def _plain_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _required_metadata(metadata: dict[str, object], key: str, label: str) -> str:
    value = str(metadata.get(key) or "").strip()
    if not value:
        raise ValueError(f"The connected account is missing {label} metadata. Reconnect it.")
    return value


def _bearer_headers(token: str) -> dict[str, str]:
    return {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": "Secure-Agentic-Work-OS/1.0",
    }


def _require_slack_ok(body: dict[str, Any]) -> None:
    if not body.get("ok"):
        raise ValueError(f"Slack API rejected the request: {body.get('error') or 'unknown_error'}")


def _slack_timestamp(value: str) -> str | None:
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
    except (TypeError, ValueError):
        return None
