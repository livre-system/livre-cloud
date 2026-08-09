import os
from cryptography.fernet import Fernet

_key = os.getenv("CREDENTIALS_SECRET")
if not _key:
    raise RuntimeError("CREDENTIALS_SECRET no está definida en las variables de entorno")

_fernet = Fernet(_key.encode())


def encrypt(value: str) -> str:
    return _fernet.encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    return _fernet.decrypt(value.encode()).decode()
