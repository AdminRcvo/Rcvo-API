from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import random
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from ingest_runtime import connect_fast, ensure_source, ingest_bytes
from rcvo_data import init_databases, utc_now

RETRYABLE = {408, 425, 429, 500, 502, 503, 504}

def _path_get(data: Any, path: str | None) -> Any:
    if not path:
        return data
    current = data
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current

def _set_target(query: dict[str, Any], body: dict[str, Any] | None, target: str, key: str, value: Any) -> None:
    if target == "json_body":
        if body is None:
            raise ValueError("pagination target=json_body requires json_body object")
        body[key] = value
    else:
        query[key] = value

def _secret_env(name: str) -> str:
    value = os.getenv(name)
    if value is None:
        raise RuntimeError(f"Required environment variable is missing: {name}")
    return value

def _oauth2_token(auth: dict[str, Any], timeout: float) -> str:
    token_url = auth["token_url"]
    client_id = _secret_env(auth["client_id_env"])
    client_secret = _secret_env(auth["client_secret_env"])
    fields = {"grant_type": "client_credentials"}
    if auth.get("scope"):
        fields["scope"] = auth["scope"]
    if auth.get("audience"):
        fields["audience"] = auth["audience"]
    req = urllib.request.Request(
        token_url,
        data=urllib.parse.urlencode(fields).encode(),
        method="POST",
        headers={
            "Authorization": "Basic " + base64.b64encode(
                f"{client_id}:{client_secret}".encode()
            ).decode(),
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    token = payload.get("access_token")
    if not token:
        raise RuntimeError("OAuth2 token response has no access_token")
    return str(token)

def _apply_auth(
    auth: dict[str, Any] | None,
    headers: dict[str, str],
    query: dict[str, Any],
    timeout: float,
    oauth_cache: dict[str, str],
) -> None:
    if not auth:
        return
    kind = auth.get("type", "none")
    if kind == "none":
        return
    if kind == "bearer_env":
        headers["Authorization"] = "Bearer " + _secret_env(auth["env"])
    elif kind == "header_env":
        headers[auth["header"]] = _secret_env(auth["env"])
    elif kind == "query_env":
        query[auth["param"]] = _secret_env(auth["env"])
    elif kind == "basic_env":
        user = _secret_env(auth["username_env"])
        password = _secret_env(auth["password_env"])
        headers["Authorization"] = "Basic " + base64.b64encode(
            f"{user}:{password}".encode()
        ).decode()
    elif kind == "oauth2_client_credentials":
        cache_key = json.dumps(auth, sort_keys=True)
        if cache_key not in oauth_cache:
            oauth_cache[cache_key] = _oauth2_token(auth, timeout)
        headers["Authorization"] = "Bearer " + oauth_cache[cache_key]
    else:
        raise ValueError(f"Unsupported auth type: {kind}")

def _request(
    *,
    url: str,
    method: str,
    headers: dict[str, str],
    query: dict[str, Any],
    json_body: dict[str, Any] | None,
    timeout: float,
    retry: dict[str, Any],
) -> tuple[bytes, dict[str, str], int, str]:
    encoded_query = urllib.parse.urlencode(
        [(k, str(v)) for k, v in query.items() if v is not None],
        doseq=True,
    )
    full_url = url
    if encoded_query:
        full_url += ("&" if "?" in full_url else "?") + encoded_query
    data = None
    req_headers = dict(headers)
    req_headers.setdefault("Accept-Encoding", "identity")
    if json_body is not None:
        data = json.dumps(json_body, separators=(",", ":")).encode()
        req_headers.setdefault("Content-Type", "application/json")
    attempts = int(retry.get("max_attempts", 7))
    base = float(retry.get("backoff_seconds", 1.0))
    max_backoff = float(retry.get("max_backoff_seconds", 60.0))
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        req = urllib.request.Request(full_url, data=data, method=method.upper(), headers=req_headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return response.read(), dict(response.headers.items()), int(response.status), response.geturl()
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code not in RETRYABLE or attempt >= attempts:
                raise
            retry_after = exc.headers.get("Retry-After")
            if retry_after and retry_after.isdigit():
                delay = float(retry_after)
            else:
                delay = min(max_backoff, base * (2 ** (attempt - 1)))
                delay *= 0.8 + random.random() * 0.4
            time.sleep(delay)
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            if attempt >= attempts:
                raise
            delay = min(max_backoff, base * (2 ** (attempt - 1)))
            delay *= 0.8 + random.random() * 0.4
            time.sleep(delay)
    raise RuntimeError(str(last_error))

def _load_checkpoint(raw_path: Path, source_id: int, key: str) -> dict[str, Any] | None:
    with connect_fast(raw_path) as conn:
        row = conn.execute(
            "SELECT value_json FROM raw_checkpoints WHERE source_id=? AND checkpoint_key=?",
            (source_id, key),
        ).fetchone()
    return json.loads(row[0]) if row else None

def _save_checkpoint(raw_path: Path, source_id: int, key: str, value: dict[str, Any]) -> None:
    with connect_fast(raw_path) as conn:
        conn.execute(
            """
            INSERT INTO raw_checkpoints(source_id,checkpoint_key,value_json,updated_at)
            VALUES (?,?,?,?)
            ON CONFLICT(source_id,checkpoint_key) DO UPDATE SET
                value_json=excluded.value_json,
                updated_at=excluded.updated_at
            """,
            (source_id, key, json.dumps(value, ensure_ascii=False, separators=(",", ":")), utc_now()),
        )

def _save_config(raw_path: Path, source_id: int, connector_key: str, config: dict[str, Any]) -> None:
    raw = json.dumps(config, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(raw.encode()).hexdigest()
    with connect_fast(raw_path) as conn:
        conn.execute(
            """
            INSERT INTO raw_connector_configs(
                source_id,connector_key,connector_kind,config_json,config_sha256,updated_at
            ) VALUES (?,?, 'http', ?, ?, ?)
            ON CONFLICT(source_id,connector_key) DO UPDATE SET
                config_json=excluded.config_json,
                config_sha256=excluded.config_sha256,
                updated_at=excluded.updated_at
            """,
            (source_id, connector_key, raw, digest, utc_now()),
        )

def _count_records(data: bytes, response_cfg: dict[str, Any], ingest_result: dict[str, Any]) -> int | None:
    if response_cfg.get("format", "json") == "json":
        try:
            parsed = json.loads(data.decode(response_cfg.get("encoding", "utf-8")))
            records = _path_get(parsed, response_cfg.get("records_path"))
            if isinstance(records, list):
                return len(records)
            return 1 if records is not None else 0
        except Exception:
            return None
    if not ingest_result.get("skipped"):
        return int(ingest_result.get("received", ingest_result.get("rows", 0)))
    return None

def run_http_connector(data_dir: Path, config: dict[str, Any], *, restart: bool = False) -> dict[str, Any]:
    raw_path, _ = init_databases(data_dir)
    source_key = config["source_key"]
    source_name = config.get("source_name")
    provider_url = config.get("provider_url") or config.get("url")
    connector_key = config.get("connector_key", "default")
    timeout = float(config.get("timeout_seconds", 60))
    method = config.get("method", "GET").upper()
    response_cfg = config.get("response", {})
    pagination = config.get("pagination", {"type": "none"})
    retry = config.get("retry", {})
    throttle = float(config.get("rate_limit", {}).get("min_interval_seconds", 0))
    base_query = copy.deepcopy(config.get("query", {}))
    base_body = copy.deepcopy(config.get("json_body"))
    if base_body is not None and not isinstance(base_body, dict):
        raise ValueError("json_body must be an object")
    if pagination.get("target") == "json_body" and base_body is None:
        base_body = {}

    with connect_fast(raw_path) as conn:
        source_id = ensure_source(
            conn, source_key, source_name, config.get("source_kind", "provider_api"),
            provider_url, "http_api", {"connector_key": connector_key},
        )
    _save_config(raw_path, source_id, connector_key, config)

    checkpoint_key = "http:" + connector_key
    state = {} if restart else (_load_checkpoint(raw_path, source_id, checkpoint_key) or {})
    ptype = pagination.get("type", "none")
    page = int(state.get("page", pagination.get("start", 1)))
    offset = int(state.get("offset", pagination.get("start", 0)))
    cursor = state.get("cursor", pagination.get("start_cursor"))
    next_url = state.get("next_url")
    max_pages = int(pagination.get("max_pages", 100000))
    run_uuid = str(uuid.uuid4())
    requests_made = total_rows = total_rejected = artifacts = 0
    oauth_cache: dict[str, str] = {}
    last_request_at = 0.0

    with connect_fast(raw_path) as conn:
        cur = conn.execute(
            "INSERT INTO raw_fetch_runs(run_uuid,source_id,connector_key,metadata_json) VALUES (?,?,?,?)",
            (run_uuid, source_id, connector_key, json.dumps({"pagination": ptype})),
        )
        run_id = int(cur.lastrowid)

    try:
        for request_index in range(1, max_pages + 1):
            if throttle:
                elapsed = time.monotonic() - last_request_at
                if elapsed < throttle:
                    time.sleep(throttle - elapsed)
            query = copy.deepcopy(base_query)
            body = copy.deepcopy(base_body) if base_body is not None else None
            target = pagination.get("target", "query")
            request_url = next_url or config["url"]

            if ptype == "page":
                _set_target(query, body, target, pagination.get("param", "page"), page)
                if pagination.get("page_size"):
                    _set_target(query, body, target, pagination.get("page_size_param", "per_page"), pagination["page_size"])
            elif ptype == "offset":
                _set_target(query, body, target, pagination.get("offset_param", "offset"), offset)
                _set_target(query, body, target, pagination.get("limit_param", "limit"), pagination.get("page_size", 100))
            elif ptype == "cursor" and cursor is not None:
                _set_target(query, body, target, pagination.get("cursor_param", "cursor"), cursor)

            headers = {str(k): str(v) for k, v in config.get("headers", {}).items()}
            _apply_auth(config.get("auth"), headers, query, timeout, oauth_cache)
            data, resp_headers, status, effective_url = _request(
                url=request_url, method=method, headers=headers, query=query,
                json_body=body, timeout=timeout, retry=retry,
            )
            last_request_at = time.monotonic()
            requests_made += 1

            ext = {
                "json": ".json", "jsonl": ".jsonl", "csv": ".csv", "tsv": ".tsv",
                "xml": ".xml", "html": ".html", "xlsx": ".xlsx", "xls": ".xls",
                "parquet": ".parquet",
            }.get(response_cfg.get("format", "json"), ".bin")
            result = ingest_bytes(
                data_dir, data, source_key=source_key, source_name=source_name,
                source_kind=config.get("source_kind", "provider_api"),
                provider_url=provider_url,
                original_filename=f"{connector_key}-{request_index:08d}{ext}",
                origin_uri=effective_url, acquisition_mode="http_api",
                etag=resp_headers.get("ETag"), last_modified=resp_headers.get("Last-Modified"),
                fmt=response_cfg.get("format", "auto"),
                records_path=response_cfg.get("records_path"),
                xml_record_tag=response_cfg.get("xml_record_tag"),
                sheet=response_cfg.get("sheet"), delimiter=response_cfg.get("delimiter"),
                batch_size=int(config.get("batch_size", 25000)),
                id_field=config.get("id_field"),
                hash_payload=bool(config.get("hash_payload", False)),
                metadata={"connector_key": connector_key, "request_index": request_index},
            )
            artifacts += 1
            total_rows += int(result.get("rows", 0))
            total_rejected += int(result.get("rejected", 0))
            count = _count_records(data, response_cfg, result)
            parsed_json = None
            if ptype in {"cursor", "next_url"}:
                try:
                    parsed_json = json.loads(data.decode(response_cfg.get("encoding", "utf-8")))
                except Exception:
                    parsed_json = None

            if ptype == "none":
                _save_checkpoint(raw_path, source_id, checkpoint_key, {"complete": True})
                break
            if ptype == "page":
                page += 1
                _save_checkpoint(raw_path, source_id, checkpoint_key, {"page": page})
                if count == 0:
                    break
                size = pagination.get("page_size")
                if pagination.get("stop_on_short_page", False) and size and count is not None and count < int(size):
                    break
            elif ptype == "offset":
                step = int(pagination.get("page_size", 100))
                offset += step
                _save_checkpoint(raw_path, source_id, checkpoint_key, {"offset": offset})
                if count == 0:
                    break
                if pagination.get("stop_on_short_page", True) and count is not None and count < step:
                    break
            elif ptype == "cursor":
                new_cursor = _path_get(parsed_json, pagination.get("next_cursor_path")) if parsed_json is not None else None
                if not new_cursor or new_cursor == cursor:
                    _save_checkpoint(raw_path, source_id, checkpoint_key, {"complete": True})
                    break
                cursor = new_cursor
                _save_checkpoint(raw_path, source_id, checkpoint_key, {"cursor": cursor})
            elif ptype == "next_url":
                candidate = _path_get(parsed_json, pagination.get("next_url_path")) if parsed_json is not None else None
                if not candidate:
                    _save_checkpoint(raw_path, source_id, checkpoint_key, {"complete": True})
                    break
                next_url = urllib.parse.urljoin(effective_url, str(candidate))
                _save_checkpoint(raw_path, source_id, checkpoint_key, {"next_url": next_url})
            else:
                raise ValueError(f"Unsupported pagination type: {ptype}")

        with connect_fast(raw_path) as conn:
            conn.execute(
                """
                UPDATE raw_fetch_runs
                SET status='complete', completed_at=?, requests_made=?,
                    artifacts_seen=?, records_persisted=?
                WHERE id=?
                """,
                (utc_now(), requests_made, artifacts, total_rows, run_id),
            )
        return {
            "run_uuid": run_uuid, "requests": requests_made, "artifacts": artifacts,
            "rows": total_rows, "rejected": total_rejected,
            "checkpoint": _load_checkpoint(raw_path, source_id, checkpoint_key),
        }
    except Exception as exc:
        with connect_fast(raw_path) as conn:
            conn.execute(
                """
                UPDATE raw_fetch_runs
                SET status=?, completed_at=?, requests_made=?,
                    artifacts_seen=?, records_persisted=?, error_message=?
                WHERE id=?
                """,
                (
                    "partial" if total_rows else "failed", utc_now(), requests_made,
                    artifacts, total_rows, str(exc)[:4000], run_id,
                ),
            )
        raise

def load_connector_config(path: Path) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8"))
    missing = [key for key in ("source_key", "url") if not config.get(key)]
    if missing:
        raise ValueError("Missing config fields: " + ", ".join(missing))
    scheme = urllib.parse.urlparse(config["url"]).scheme.lower()
    if scheme not in {"http", "https"}:
        raise ValueError("HTTP connector URL must use http or https")
    return config
