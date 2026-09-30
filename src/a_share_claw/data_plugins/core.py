"""Data acquisition contracts. No model SDK or messaging platform dependencies."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping
from uuid import uuid4
from zoneinfo import ZoneInfo


def iso_date(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("Dates must use YYYY-MM-DD")
    return date.fromisoformat(value).isoformat()


class DataError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.code, self.retryable = code, retryable


@dataclass(frozen=True)
class Manifest:
    plugin_id: str
    capabilities: tuple[str, ...]
    hosts: tuple[str, ...]
    credential_env: str | None = None
    version: str = "1.0.0"
    schema_version: str = "1"


@dataclass(frozen=True)
class Requirement:
    requirement_id: str
    provider: str
    capability: str
    as_of_date: str
    params: Mapping[str, Any] = field(default_factory=dict)
    required: bool = True

    @classmethod
    def parse(cls, obj: dict) -> 'Requirement':
        if not isinstance(obj, dict) or set(obj) - set(cls.__dataclass_fields__):
            raise ValueError("Invalid requirement fields")
        item = cls(**obj)
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", item.requirement_id):
            raise ValueError("Invalid requirement_id")
        iso_date(item.as_of_date)
        if item.as_of_date > datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat():
            raise ValueError("Future availability cutoff is not supported")
        if not isinstance(item.params, dict) or not isinstance(item.required, bool):
            raise ValueError("params must be an object and required must be boolean")
        # JSON round-trip prevents callers mutating the original plan after validation.
        return cls(item.requirement_id, item.provider, item.capability, item.as_of_date,
                   MappingProxyType(json.loads(json.dumps(item.params))), item.required)

    def json(self) -> dict:
        return {"requirement_id": self.requirement_id, "provider": self.provider,
                "capability": self.capability, "as_of_date": self.as_of_date,
                "params": dict(self.params), "required": self.required}


@dataclass
class Payload:
    data: Any
    raw: bytes
    source_url: str
    availability: str
    warnings: list[str] = field(default_factory=list)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Transport:
    """Bounded GET-only transport; credentials cannot cross a redirect."""
    _lock = threading.Lock()
    _last_request: dict[str, float] = {}

    def __init__(self, manifest: Manifest):
        self.manifest = manifest

    def get(self, url: str, params: dict | None = None, headers: dict | None = None) -> bytes:
        parsed = urllib.parse.urlsplit(url)
        if (parsed.scheme != "https" or parsed.hostname not in self.manifest.hosts
                or parsed.username or parsed.password or parsed.port not in (None, 443)
                or parsed.query or parsed.fragment):
            raise DataError("source_denied", "Only declared HTTPS source endpoints are allowed")
        # SEC asks clients to remain below 10 requests/second; use <= 5 per process.
        if self.manifest.plugin_id == "sec":
            with self._lock:
                delay = 0.21 - (time.monotonic() - self._last_request.get("sec", 0))
                if delay > 0:
                    time.sleep(delay)
                self._last_request["sec"] = time.monotonic()
        request = urllib.request.Request(url + ("?" + urllib.parse.urlencode(params) if params else ""),
                                         headers={"User-Agent": "a-share-claw/0.1", **(headers or {})})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        for attempt in range(3):
            try:
                with opener.open(request, timeout=30) as response:
                    raw = response.read(20_000_001)
                if len(raw) > 20_000_000:
                    raise DataError("response_too_large", "Source response exceeds 20 MB")
                return raw
            except urllib.error.HTTPError as exc:
                retryable = exc.code == 429 or exc.code >= 500
                if retryable and attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                code = "not_configured" if exc.code == 401 else "access_denied" if exc.code == 403 else "http_error"
                raise DataError(code, f"Source returned HTTP {exc.code}", retryable) from None
            except (urllib.error.URLError, TimeoutError, OSError):
                if attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                raise DataError("network_error", "Source connection failed", True) from None
        raise AssertionError("unreachable")


class Provider:
    manifest: Manifest

    def __init__(self, settings: Mapping[str, str], transport: Transport | None = None):
        self._settings = dict(settings)
        self.transport = transport or Transport(self.manifest)

    def credential(self) -> str:
        name = self.manifest.credential_env
        value = self._settings.get(name or "", "")
        if name and not value:
            raise DataError("not_configured", f"Configure {name} in the host environment")
        return value

    def fetch(self, requirement: Requirement) -> Payload:
        raise NotImplementedError


class Registry:
    """Trusted application code may hot-register/remove providers; Agents cannot."""
    def __init__(self):
        self._factories: dict[str, type[Provider]] = {}
        self._lock = threading.RLock()

    def register(self, provider: type[Provider]) -> None:
        with self._lock:
            self._factories[provider.manifest.plugin_id] = provider

    def unregister(self, plugin_id: str) -> None:
        with self._lock:
            self._factories.pop(plugin_id, None)

    def snapshot(self, env: Mapping[str, str] | None = None,
                 transport_factory: Callable[[Manifest], Transport] = Transport) -> dict[str, Provider]:
        settings = dict(os.environ if env is None else env)
        enabled = {x.strip() for x in settings.get("ASCLAW_DATA_PROVIDERS", "nbs,pbc,tickflow,fred,sec").split(",") if x.strip()}
        with self._lock:
            return {key: factory(settings, transport_factory(factory.manifest))
                    for key, factory in self._factories.items() if key in enabled}


class DataRun:
    """A plan-gated, scope-isolated, pinned provider run with immutable evidence files."""
    def __init__(self, providers: Mapping[str, Provider], artifact_root: Path, scope: str):
        self.providers = MappingProxyType(dict(providers))
        self.run_id = uuid4().hex
        scope_id = hashlib.sha256(scope.encode()).hexdigest()
        self.directory = artifact_root / scope_id / self.run_id
        self.requirements: dict[str, Requirement] | None = None
        self.results: dict[str, dict] = {}
        self._lock = asyncio.Lock()

    def list_providers(self) -> list[dict]:
        return [{**asdict(p.manifest), "configured": not p.manifest.credential_env or bool(p._settings.get(p.manifest.credential_env))}
                for p in self.providers.values()]

    def plan(self, plan: dict) -> dict:
        if not isinstance(plan, dict) or set(plan) != {"framework", "requirements"}:
            raise ValueError("Plan requires framework and requirements")
        if not isinstance(plan["framework"], str) or not plan["framework"].strip() or len(plan["framework"]) > 16000:
            raise ValueError("Research framework must precede data acquisition")
        if not isinstance(plan["requirements"], list) or len(plan["requirements"]) > 50:
            raise ValueError("Plan accepts at most 50 requirements")
        requirements = [Requirement.parse(item) for item in plan["requirements"]]
        if len({r.requirement_id for r in requirements}) != len(requirements):
            raise ValueError("Duplicate requirement_id")
        new_requirements = {r.requirement_id: r for r in requirements}
        if self.requirements is not None and any(new_requirements.get(key) != value for key, value in self.requirements.items()):
            raise ValueError("Existing requirements cannot be removed or changed; start a new run")
        self.directory.mkdir(parents=True, exist_ok=True)
        revision = len(list(self.directory.glob("plan-*.json"))) + 1
        self._save(f"plan-{revision}.json", {"run_id": self.run_id, "framework": plan["framework"],
                                "requirements": [r.json() for r in requirements],
                                "providers": self.list_providers()})
        self.requirements = new_requirements
        return self.summary()

    def _save(self, name: str, obj: Any) -> None:
        with (self.directory / name).open("x", encoding="utf-8") as stream:
            json.dump(obj, stream, ensure_ascii=False, indent=2, allow_nan=False)

    async def fetch(self, requirement_id: str) -> dict:
        async with self._lock:
            return await self._fetch(requirement_id)

    async def _fetch(self, requirement_id: str) -> dict:
        if self.requirements is None or requirement_id not in self.requirements:
            return {"ok": False, "status": "gap", "error_code": "plan_required", "retryable": False,
                    "data": None, "message": "Declare this requirement in a research plan first"}
        if requirement_id in self.results:
            return self.results[requirement_id]
        r = self.requirements[requirement_id]
        result = {"run_id": self.run_id, "requirement_id": requirement_id, "ok": False,
                  "status": "gap", "error_code": None, "retryable": False, "data": None,
                  "provenance": {}, "fallback_status": "none", "truncated": False}
        started = time.monotonic()
        try:
            provider = self.providers.get(r.provider)
            if provider is None:
                raise DataError("provider_unavailable", "Provider is disabled or not registered")
            if r.capability not in provider.manifest.capabilities:
                raise DataError("unsupported_capability", "Provider does not implement this capability")
            payload = await asyncio.to_thread(provider.fetch, r)
            digest = hashlib.sha256(payload.raw).hexdigest()
            raw_name = f"{requirement_id}-{digest}.raw"
            (self.directory / raw_name).write_bytes(payload.raw)
            result.update(ok=True, status="ok" if payload.availability == "verified" else "unverified",
                          data=payload.data, warnings=payload.warnings,
                          fallback_status="none" if payload.availability == "verified" else "unverified",
                          provenance={"provider": r.provider, "version": provider.manifest.version,
                                      "schema_version": provider.manifest.schema_version,
                                      "source_url": payload.source_url, "sha256": digest,
                                      "artifact": raw_name, "as_of_date": r.as_of_date,
                                      "availability": payload.availability,
                                      "retrieved_at": datetime.now(timezone.utc).isoformat()})
        except DataError as exc:
            result.update(error_code=exc.code, message=str(exc), retryable=exc.retryable)
        except (ValueError, TypeError, KeyError, IndexError, UnicodeError):
            result.update(error_code="invalid_schema", message="Source response or request has an unsupported schema")
        except Exception:
            # Do not leak credential-bearing request URLs from client exceptions.
            result.update(error_code="provider_error", message="Provider failed; no fallback was attempted")
        result["usage"] = {"elapsed_ms": round((time.monotonic() - started) * 1000)}
        self._save(f"{requirement_id}.json", result)
        self.results[requirement_id] = result
        return result

    def summary(self) -> dict:
        gaps = [{"requirement_id": r.requirement_id, "required": r.required,
                 "status": self.results.get(r.requirement_id, {}).get("status", "missing")}
                for r in (self.requirements or {}).values()
                if self.results.get(r.requirement_id, {}).get("status") != "ok"]
        return {"run_id": self.run_id, "planned": self.requirements is not None, "gaps": gaps,
                "required_data_complete": self.requirements is not None and not any(g["required"] for g in gaps),
                "official_output_allowed": False,
                "note": "Research evidence only; legacy scoring migration and full evaluator remain pending"}


def preview(result: dict, max_chars: int = 12000) -> str:
    rendered = json.dumps(result, ensure_ascii=False)
    if len(rendered) <= max_chars:
        return rendered
    copy = dict(result)
    copy["data"] = {"preview": json.dumps(result.get("data"), ensure_ascii=False)[:max(0, max_chars - 2500)]}
    copy["truncated"] = True
    return json.dumps(copy, ensure_ascii=False)
