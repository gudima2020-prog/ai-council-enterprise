from __future__ import annotations


class SecretReferenceError(ValueError):
    pass


def parse_secret_reference(reference: str) -> tuple[str, str]:
    prefix = "secret://"
    if not reference.startswith(prefix):
        raise SecretReferenceError("Secret reference must start with secret://")
    remainder = reference[len(prefix):]
    provider_key, separator, secret_key = remainder.partition("/")
    if not separator or not provider_key or not secret_key:
        raise SecretReferenceError(
            "Secret reference must have form secret://provider/secret-key"
        )
    if ".." in secret_key.split("/"):
        raise SecretReferenceError("Secret reference contains an invalid path segment.")
    return provider_key, secret_key
