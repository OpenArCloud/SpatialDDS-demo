import json
import os
import time
from typing import Dict, Optional, Tuple
from urllib.parse import urlparse, quote
from urllib.request import urlopen

_CACHE: Dict[str, Tuple[float, Optional[Dict], Dict[str, str]]] = {}


def _cache_get(manifest_uri: str) -> Optional[Tuple[Optional[Dict], Dict[str, str]]]:
    cached = _CACHE.get(manifest_uri)
    if not cached:
        return None
    expiry, data, status = cached
    if time.time() > expiry:
        _CACHE.pop(manifest_uri, None)
        return None
    return data, status


def _cache_put(manifest_uri: str, data: Optional[Dict], status: Dict[str, str], ttl_sec: int) -> None:
    _CACHE[manifest_uri] = (time.time() + ttl_sec, data, status)


def _resolve_local(manifest_uri: str) -> Tuple[Optional[Dict], Dict[str, str]]:
    # Mapping: spatialdds://vps.example.com/zone:sf-downtown/manifest:vps
    # -> manifests/v1.4/vps_sf_downtown.json
    mapping = {
        "spatialdds://vps.example.com/zone:sf-downtown/manifest:vps": (
            "manifests/v1.4/vps_sf_downtown.json"
        )
    }
    local_path = mapping.get(manifest_uri)
    if not local_path:
        return None, {"mode": "LOCAL_MISSING", "path": ""}

    with open(local_path, "r", encoding="utf-8") as handle:
        return json.load(handle), {"mode": "LOCAL", "path": local_path}


def _resolve_remote(manifest_uri: str) -> Tuple[Optional[Dict], Dict[str, str]]:
    parsed = urlparse(manifest_uri)
    if parsed.scheme != "spatialdds":
        return None, {"mode": "HTTPS_UNSUPPORTED", "path": ""}
    authority = parsed.netloc
    path = parsed.path.lstrip("/")
    encoded_path = quote(path, safe="")
    url = f"https://{authority}/.well-known/spatialdds/{encoded_path}.json"
    with urlopen(url, timeout=5) as response:
        return json.loads(response.read().decode("utf-8")), {"mode": "HTTPS", "path": url}


def resolve_manifest(manifest_uri: str, ttl_sec: int = 300) -> Tuple[Optional[Dict], Dict[str, str]]:
    cached = _cache_get(manifest_uri)
    if cached:
        data, status = cached
        return data, {**status, "cached": "1"}

    parsed = urlparse(manifest_uri)
    if parsed.scheme == "https":
        if os.getenv("ALLOW_HTTPS", "0") != "1":
            status = {"mode": "HTTPS_DISABLED", "path": ""}
            _cache_put(manifest_uri, None, status, ttl_sec)
            return None, status
        data, status = _resolve_remote(manifest_uri.replace("https://", "spatialdds://", 1))
        _cache_put(manifest_uri, data, status, ttl_sec)
        return data, status

    if parsed.scheme != "spatialdds":
        status = {"mode": "UNSUPPORTED_SCHEME", "path": ""}
        _cache_put(manifest_uri, None, status, ttl_sec)
        return None, status

    data, status = _resolve_local(manifest_uri)
    _cache_put(manifest_uri, data, status, ttl_sec)
    return data, status
