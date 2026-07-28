from __future__ import annotations

from dataclasses import dataclass
import base64
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
import platform
from typing import Any, Protocol
from urllib import error as urllib_error
from urllib import request as urllib_request


class SecretProviderError(RuntimeError):
    pass


class SecretProviderUnavailable(SecretProviderError):
    pass


class SecretProviderReadOnly(SecretProviderError):
    pass


@dataclass(frozen=True, slots=True)
class StoredSecretMaterial:
    encrypted_payload: str | None
    provider_version: str = ""
    material_hash: str = ""


class SecretProviderAdapter(Protocol):
    key: str
    read_only: bool

    def health(self, config: dict[str, Any]) -> tuple[bool, str]: ...

    def store(
        self,
        *,
        provider_ref: str,
        value: str,
        config: dict[str, Any],
    ) -> StoredSecretMaterial: ...

    def resolve(
        self,
        *,
        provider_ref: str,
        encrypted_payload: str | None,
        provider_version: str,
        config: dict[str, Any],
    ) -> str: ...


def material_hash(value: str | bytes) -> str:
    data = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(data).hexdigest()


def _split_provider_ref(provider_ref: str, *, default_field: str = "value") -> tuple[str, str]:
    raw = str(provider_ref).strip()
    if not raw:
        raise SecretProviderError("Provider reference is required.")
    if "#" in raw:
        path, field = raw.rsplit("#", 1)
        return path, field or default_field
    return raw, default_field


def _json_field(value: str, field: str) -> str:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        if field == "value":
            return value
        raise SecretProviderError(
            f"Remote secret is not JSON; field {field!r} cannot be selected."
        )
    if not isinstance(parsed, dict) or field not in parsed:
        raise SecretProviderError(f"Remote secret JSON field {field!r} was not found.")
    result = parsed[field]
    if isinstance(result, (dict, list)):
        return json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    return str(result)


class EnvironmentSecretProvider:
    key = "env"
    read_only = True

    def health(self, config: dict[str, Any]) -> tuple[bool, str]:
        prefix = str(config.get("prefix") or "")
        return True, f"Environment provider ready (prefix={prefix!r})."

    def store(self, **_: Any) -> StoredSecretMaterial:
        raise SecretProviderReadOnly("Environment provider is read-only.")

    def resolve(
        self,
        *,
        provider_ref: str,
        encrypted_payload: str | None,
        provider_version: str,
        config: dict[str, Any],
    ) -> str:
        del encrypted_payload, provider_version
        prefix = str(config.get("prefix") or "")
        variable = f"{prefix}{provider_ref}"
        value = os.getenv(variable)
        if value is None:
            raise SecretProviderUnavailable(
                f"Environment variable {variable!r} is not configured."
            )
        return value


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def _make_blob(data: bytes) -> tuple[_DATA_BLOB, ctypes.Array[ctypes.c_char]]:
    buffer = ctypes.create_string_buffer(data)
    blob = _DATA_BLOB(
        len(data),
        ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)),
    )
    return blob, buffer


def _windows_crypto_libraries():
    if platform.system().lower() != "windows":
        raise SecretProviderUnavailable(
            "Windows DPAPI is only available on Windows."
        )
    crypt32 = ctypes.WinDLL("Crypt32.dll", use_last_error=True)
    kernel32 = ctypes.WinDLL("Kernel32.dll", use_last_error=True)
    blob_pointer = ctypes.POINTER(_DATA_BLOB)
    crypt32.CryptProtectData.argtypes = [
        blob_pointer,
        wintypes.LPCWSTR,
        blob_pointer,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        blob_pointer,
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = [
        blob_pointer,
        ctypes.c_void_p,
        blob_pointer,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        blob_pointer,
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    return crypt32, kernel32


class WindowsDpapiSecretProvider:
    key = "windows_dpapi"
    read_only = False

    CRYPTPROTECT_UI_FORBIDDEN = 0x1
    CRYPTPROTECT_LOCAL_MACHINE = 0x4

    def health(self, config: dict[str, Any]) -> tuple[bool, str]:
        del config
        if platform.system().lower() != "windows":
            return False, "Windows DPAPI is only available on Windows."
        return True, "Windows DPAPI is available."

    def _crypt_protect(self, data: bytes, *, machine_scope: bool) -> bytes:
        if platform.system().lower() != "windows":
            raise SecretProviderUnavailable(
                "Windows DPAPI is only available on Windows."
            )
        in_blob, in_buffer = _make_blob(data)
        out_blob = _DATA_BLOB()
        flags = self.CRYPTPROTECT_UI_FORBIDDEN
        if machine_scope:
            flags |= self.CRYPTPROTECT_LOCAL_MACHINE
        crypt32, kernel32 = _windows_crypto_libraries()
        _ = in_buffer
        if not crypt32.CryptProtectData(
            ctypes.byref(in_blob),
            None,
            None,
            None,
            None,
            flags,
            ctypes.byref(out_blob),
        ):
            raise ctypes.WinError()
        try:
            return ctypes.string_at(out_blob.pbData, out_blob.cbData)
        finally:
            kernel32.LocalFree(ctypes.cast(out_blob.pbData, ctypes.c_void_p))

    def _crypt_unprotect(self, data: bytes) -> bytes:
        if platform.system().lower() != "windows":
            raise SecretProviderUnavailable(
                "Windows DPAPI is only available on Windows."
            )
        in_blob, in_buffer = _make_blob(data)
        out_blob = _DATA_BLOB()
        crypt32, kernel32 = _windows_crypto_libraries()
        _ = in_buffer
        if not crypt32.CryptUnprotectData(
            ctypes.byref(in_blob),
            None,
            None,
            None,
            None,
            self.CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(out_blob),
        ):
            raise ctypes.WinError()
        try:
            return ctypes.string_at(out_blob.pbData, out_blob.cbData)
        finally:
            kernel32.LocalFree(ctypes.cast(out_blob.pbData, ctypes.c_void_p))

    def store(
        self,
        *,
        provider_ref: str,
        value: str,
        config: dict[str, Any],
    ) -> StoredSecretMaterial:
        del provider_ref
        scope = str(config.get("scope") or "user").strip().lower()
        if scope not in {"user", "machine"}:
            raise SecretProviderError("DPAPI scope must be 'user' or 'machine'.")
        ciphertext = self._crypt_protect(
            value.encode("utf-8"),
            machine_scope=scope == "machine",
        )
        encoded = base64.b64encode(ciphertext).decode("ascii")
        return StoredSecretMaterial(
            encrypted_payload=encoded,
            provider_version="dpapi-v1",
            material_hash=material_hash(ciphertext),
        )

    def resolve(
        self,
        *,
        provider_ref: str,
        encrypted_payload: str | None,
        provider_version: str,
        config: dict[str, Any],
    ) -> str:
        del provider_ref, provider_version, config
        if not encrypted_payload:
            raise SecretProviderError("Encrypted DPAPI payload is missing.")
        try:
            ciphertext = base64.b64decode(encrypted_payload.encode("ascii"), validate=True)
        except Exception as exc:
            raise SecretProviderError("Encrypted DPAPI payload is invalid.") from exc
        plaintext = self._crypt_unprotect(ciphertext)
        try:
            return plaintext.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SecretProviderError("DPAPI payload is not valid UTF-8.") from exc


class HashiCorpVaultKv2SecretProvider:
    """HashiCorp Vault KV v2 adapter using only the Python standard library.

    Authentication intentionally uses an environment variable referenced by
    ``auth_env``. Provider configuration itself never stores a Vault token.
    ``provider_ref`` uses ``path/to/secret#field`` syntax.
    """

    key = "hashicorp_vault_kv2"
    read_only = False

    @staticmethod
    def _config(config: dict[str, Any]) -> tuple[str, str, str, str, float]:
        address = str(config.get("address") or "").rstrip("/")
        mount = str(config.get("mount") or "secret").strip("/")
        token_env = str(config.get("auth_env") or "VAULT_TOKEN").strip()
        namespace = str(config.get("namespace") or "").strip()
        timeout = float(config.get("timeout_seconds") or 10)
        if not address.startswith(("https://", "http://")):
            raise SecretProviderError("Vault provider requires an http(s) address.")
        if not token_env:
            raise SecretProviderError("Vault provider auth_env is required.")
        return address, mount, token_env, namespace, max(1.0, min(timeout, 60.0))

    @staticmethod
    def _request(
        url: str,
        *,
        token: str,
        namespace: str,
        timeout: float,
        method: str = "GET",
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        headers = {"X-Vault-Token": token, "Accept": "application/json"}
        if namespace:
            headers["X-Vault-Namespace"] = namespace
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib_request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib_request.urlopen(req, timeout=timeout) as response:
                raw = response.read()
        except urllib_error.HTTPError as exc:
            raise SecretProviderUnavailable(
                f"Vault returned HTTP {exc.code}."
            ) from exc
        except OSError as exc:
            raise SecretProviderUnavailable(f"Vault request failed: {exc}.") from exc
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception as exc:
            raise SecretProviderError("Vault response is not valid JSON.") from exc

    def health(self, config: dict[str, Any]) -> tuple[bool, str]:
        address, _mount, token_env, namespace, timeout = self._config(config)
        token = os.getenv(token_env)
        if not token:
            return False, f"Vault token environment variable {token_env!r} is not configured."
        try:
            payload = self._request(
                f"{address}/v1/sys/health",
                token=token,
                namespace=namespace,
                timeout=timeout,
            )
            initialized = payload.get("initialized", True)
            sealed = payload.get("sealed", False)
            if not initialized or sealed:
                return False, "Vault is not ready (uninitialized or sealed)."
            return True, "HashiCorp Vault is reachable and ready."
        except SecretProviderError as exc:
            return False, str(exc)

    def resolve(
        self,
        *,
        provider_ref: str,
        encrypted_payload: str | None,
        provider_version: str,
        config: dict[str, Any],
    ) -> str:
        del encrypted_payload, provider_version
        address, mount, token_env, namespace, timeout = self._config(config)
        token = os.getenv(token_env)
        if not token:
            raise SecretProviderUnavailable(
                f"Vault token environment variable {token_env!r} is not configured."
            )
        path, field = _split_provider_ref(
            provider_ref,
            default_field=str(config.get("value_field") or "value"),
        )
        payload = self._request(
            f"{address}/v1/{mount}/data/{path.lstrip('/')}",
            token=token,
            namespace=namespace,
            timeout=timeout,
        )
        data = payload.get("data", {}).get("data", {})
        if field not in data:
            raise SecretProviderError(f"Vault secret field {field!r} was not found.")
        value = data[field]
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        return str(value)

    def store(
        self,
        *,
        provider_ref: str,
        value: str,
        config: dict[str, Any],
    ) -> StoredSecretMaterial:
        address, mount, token_env, namespace, timeout = self._config(config)
        token = os.getenv(token_env)
        if not token:
            raise SecretProviderUnavailable(
                f"Vault token environment variable {token_env!r} is not configured."
            )
        path, field = _split_provider_ref(
            provider_ref,
            default_field=str(config.get("value_field") or "value"),
        )
        current_data: dict[str, Any] = {}
        try:
            current = self._request(
                f"{address}/v1/{mount}/data/{path.lstrip('/')}",
                token=token,
                namespace=namespace,
                timeout=timeout,
            )
            remote_data = current.get("data", {}).get("data", {})
            if isinstance(remote_data, dict):
                current_data = dict(remote_data)
        except SecretProviderUnavailable as exc:
            if "HTTP 404" not in str(exc):
                raise
        current_data[field] = value
        payload = self._request(
            f"{address}/v1/{mount}/data/{path.lstrip('/')}",
            token=token,
            namespace=namespace,
            timeout=timeout,
            method="POST",
            body={"data": current_data},
        )
        version = payload.get("data", {}).get("version")
        return StoredSecretMaterial(
            encrypted_payload=None,
            provider_version=f"vault-kv2:{version or 'current'}",
            material_hash=material_hash(value),
        )


class AwsSecretsManagerProvider:
    key = "aws_secrets_manager"
    read_only = False

    @staticmethod
    def _client(config: dict[str, Any]):
        try:
            import boto3  # type: ignore
        except ImportError as exc:
            raise SecretProviderUnavailable(
                "AWS Secrets Manager requires optional package 'boto3'."
            ) from exc
        kwargs: dict[str, Any] = {}
        if config.get("region_name"):
            kwargs["region_name"] = str(config["region_name"])
        if config.get("endpoint_url"):
            kwargs["endpoint_url"] = str(config["endpoint_url"])
        return boto3.client("secretsmanager", **kwargs)

    def health(self, config: dict[str, Any]) -> tuple[bool, str]:
        try:
            self._client(config).list_secrets(MaxResults=1)
            return True, "AWS Secrets Manager is reachable."
        except Exception as exc:
            return False, f"AWS Secrets Manager unavailable: {exc}"

    def resolve(self, *, provider_ref: str, encrypted_payload: str | None, provider_version: str, config: dict[str, Any]) -> str:
        del encrypted_payload, provider_version
        secret_id, field = _split_provider_ref(
            provider_ref,
            default_field=str(config.get("value_field") or "value"),
        )
        response = self._client(config).get_secret_value(SecretId=secret_id)
        if response.get("SecretString") is not None:
            return _json_field(str(response["SecretString"]), field)
        binary = response.get("SecretBinary")
        if binary is None:
            raise SecretProviderError("AWS secret contains no material.")
        if isinstance(binary, str):
            raw = base64.b64decode(binary)
        else:
            raw = bytes(binary)
        return _json_field(raw.decode("utf-8"), field)

    def store(self, *, provider_ref: str, value: str, config: dict[str, Any]) -> StoredSecretMaterial:
        secret_id, field = _split_provider_ref(
            provider_ref,
            default_field=str(config.get("value_field") or "value"),
        )
        client = self._client(config)
        payload = value
        if field != "value" or bool(config.get("force_json")):
            current: dict[str, Any] = {}
            try:
                existing = client.get_secret_value(SecretId=secret_id).get("SecretString")
                parsed = json.loads(existing) if existing else {}
                if isinstance(parsed, dict):
                    current = dict(parsed)
            except Exception:
                current = {}
            current[field] = value
            payload = json.dumps(current, ensure_ascii=False)
        response = client.put_secret_value(SecretId=secret_id, SecretString=payload)
        return StoredSecretMaterial(
            encrypted_payload=None,
            provider_version=str(response.get("VersionId") or "aws-current"),
            material_hash=material_hash(value),
        )


class AzureKeyVaultSecretProvider:
    key = "azure_key_vault"
    read_only = False

    @staticmethod
    def _client(config: dict[str, Any]):
        try:
            from azure.identity import DefaultAzureCredential  # type: ignore
            from azure.keyvault.secrets import SecretClient  # type: ignore
        except ImportError as exc:
            raise SecretProviderUnavailable(
                "Azure Key Vault requires optional packages 'azure-identity' and 'azure-keyvault-secrets'."
            ) from exc
        vault_url = str(config.get("vault_url") or "").rstrip("/")
        if not vault_url.startswith("https://"):
            raise SecretProviderError("Azure Key Vault requires an https vault_url.")
        return SecretClient(vault_url=vault_url, credential=DefaultAzureCredential())

    def health(self, config: dict[str, Any]) -> tuple[bool, str]:
        try:
            iterator = self._client(config).list_properties_of_secrets(max_page_size=1)
            next(iter(iterator), None)
            return True, "Azure Key Vault is reachable."
        except Exception as exc:
            return False, f"Azure Key Vault unavailable: {exc}"

    def resolve(self, *, provider_ref: str, encrypted_payload: str | None, provider_version: str, config: dict[str, Any]) -> str:
        del encrypted_payload, provider_version
        name, field = _split_provider_ref(
            provider_ref,
            default_field=str(config.get("value_field") or "value"),
        )
        result = self._client(config).get_secret(name)
        return _json_field(str(result.value), field)

    def store(self, *, provider_ref: str, value: str, config: dict[str, Any]) -> StoredSecretMaterial:
        name, field = _split_provider_ref(
            provider_ref,
            default_field=str(config.get("value_field") or "value"),
        )
        client = self._client(config)
        payload = value
        if field != "value":
            current: dict[str, Any] = {}
            try:
                existing = client.get_secret(name).value
                parsed = json.loads(existing) if existing else {}
                if isinstance(parsed, dict):
                    current = dict(parsed)
            except Exception:
                current = {}
            current[field] = value
            payload = json.dumps(current, ensure_ascii=False)
        result = client.set_secret(name, payload)
        version = getattr(result.properties, "version", None)
        return StoredSecretMaterial(
            encrypted_payload=None,
            provider_version=str(version or "azure-current"),
            material_hash=material_hash(value),
        )


class GcpSecretManagerProvider:
    key = "gcp_secret_manager"
    read_only = False

    @staticmethod
    def _client():
        try:
            from google.cloud import secretmanager  # type: ignore
        except ImportError as exc:
            raise SecretProviderUnavailable(
                "Google Secret Manager requires optional package 'google-cloud-secret-manager'."
            ) from exc
        return secretmanager.SecretManagerServiceClient()

    @staticmethod
    def _name(provider_ref: str, config: dict[str, Any]) -> tuple[str, str]:
        secret_id, field = _split_provider_ref(
            provider_ref,
            default_field=str(config.get("value_field") or "value"),
        )
        if secret_id.startswith("projects/"):
            base = secret_id
            if "/versions/" in base:
                base = base.split("/versions/", 1)[0]
        else:
            project_id = str(config.get("project_id") or "").strip()
            if not project_id:
                raise SecretProviderError("Google Secret Manager requires project_id.")
            base = f"projects/{project_id}/secrets/{secret_id}"
        return base, field

    def health(self, config: dict[str, Any]) -> tuple[bool, str]:
        try:
            project_id = str(config.get("project_id") or "").strip()
            if not project_id:
                raise SecretProviderError("Google Secret Manager requires project_id.")
            client = self._client()
            iterator = client.list_secrets(request={"parent": f"projects/{project_id}"}, page_size=1)
            next(iter(iterator), None)
            return True, "Google Secret Manager is reachable."
        except Exception as exc:
            return False, f"Google Secret Manager unavailable: {exc}"

    def resolve(self, *, provider_ref: str, encrypted_payload: str | None, provider_version: str, config: dict[str, Any]) -> str:
        del encrypted_payload, provider_version
        base, field = self._name(provider_ref, config)
        response = self._client().access_secret_version(request={"name": f"{base}/versions/latest"})
        value = bytes(response.payload.data).decode("utf-8")
        return _json_field(value, field)

    def store(self, *, provider_ref: str, value: str, config: dict[str, Any]) -> StoredSecretMaterial:
        base, field = self._name(provider_ref, config)
        client = self._client()
        payload = value
        if field != "value":
            current: dict[str, Any] = {}
            try:
                existing = client.access_secret_version(
                    request={"name": f"{base}/versions/latest"}
                )
                parsed = json.loads(bytes(existing.payload.data).decode("utf-8"))
                if isinstance(parsed, dict):
                    current = dict(parsed)
            except Exception:
                current = {}
            current[field] = value
            payload = json.dumps(current, ensure_ascii=False)
        response = client.add_secret_version(
            request={"parent": base, "payload": {"data": payload.encode("utf-8")}}
        )
        version = str(getattr(response, "name", "gcp-current")).rsplit("/", 1)[-1]
        return StoredSecretMaterial(
            encrypted_payload=None,
            provider_version=f"gcp:{version}",
            material_hash=material_hash(value),
        )


class ProviderRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, SecretProviderAdapter] = {}
        self.register(EnvironmentSecretProvider())
        self.register(WindowsDpapiSecretProvider())
        self.register(HashiCorpVaultKv2SecretProvider())
        self.register(AwsSecretsManagerProvider())
        self.register(AzureKeyVaultSecretProvider())
        self.register(GcpSecretManagerProvider())

    def register(self, adapter: SecretProviderAdapter) -> None:
        key = str(adapter.key).strip()
        if not key:
            raise ValueError("Secret provider adapter key is required.")
        self._adapters[key] = adapter

    def get(self, key: str) -> SecretProviderAdapter:
        adapter = self._adapters.get(str(key))
        if adapter is None:
            raise SecretProviderUnavailable(
                f"Secret provider adapter {key!r} is not registered."
            )
        return adapter

    def keys(self) -> list[str]:
        return sorted(self._adapters)
