"""一次性部署憑證；秘密僅進入受保護檔案或明確的 SSH 傳輸資料。"""

import hmac
import json
from pathlib import Path
import secrets
import tomllib

from .client.connection import config_directory, connection_name, default_name, endpoint, profile_path, read_profile
from .client.credentials import TokenSource, validate_token
from .configured_launcher import config_text
from .protected_files import private_directory, read_private, write_private


def in_repository(path: Path) -> bool:
    repository = Path(__file__).resolve().parents[2]
    try:
        path = path.resolve()
    except (OSError, RuntimeError):
        raise ValueError("憑證路徑無法解析，請確認目錄或符號連結。") from None
    return (repository / ".git").exists() and (path == repository or repository in path.parents)


def root_directory(directory: Path | None) -> Path:
    root = (directory or config_directory()).expanduser().absolute()
    if in_repository(root):
        raise ValueError("憑證目錄須放在版本庫之外，請使用預設使用者目錄。")
    private_directory(root)
    return root


def encoded(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def create_profile(directory: Path, files: dict[str, bytes]) -> None:
    private_directory(directory.parent)
    try:
        directory.mkdir(mode=0o700)
    except FileExistsError:
        raise ValueError("連線已存在；未覆蓋或重新產生憑證。請選其他名稱或使用既有連線。") from None
    created = []
    try:
        private_directory(directory)
        for filename, data in files.items():
            path = directory / filename
            write_private(path, data)
            created.append(path)
    except BaseException:
        for path in reversed(created):
            path.unlink(missing_ok=True)
        directory.rmdir()
        raise


def select_default(name: str, *, directory: Path | None = None) -> None:
    root = root_directory(directory)
    name = connection_name(name)
    read_profile(root, name)
    write_private(root / "default-connection", (name + "\n").encode(), replace=True)


def maybe_default(root: Path, name: str) -> None:
    target = root / "default-connection"
    if not target.exists() and not target.is_symlink():
        write_private(target, (name + "\n").encode())


def initialize(source: Path, *, name: str = "default", directory: Path | None = None) -> Path:
    """建立新 Gateway 副本與兩個固定角色；不啟動服務或讀取原秘密。"""
    from .server.app import assemble
    name = connection_name(name)
    try:
        with source.open("rb") as stream:
            raw = stream.read(65537)
        if len(raw) > 65536:
            raise ValueError("Gateway 設定不得超過 64 KiB。")
        config = tomllib.loads(raw.decode("utf-8-sig"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError):
        raise ValueError("無法讀取 Gateway UTF-8 TOML，請確認來源檔案。") from None
    assemble(config, fake=False, resolve_secret=lambda _: secrets.token_urlsafe(32))
    root = root_directory(directory)
    target = profile_path(root, name)
    tokens = {role: secrets.token_urlsafe(32) for role in ("read", "manage")}
    channels = [c["id"] for c in config["channels"]]
    config["auth"] = {"principals": [{"id": "remote_reader" if role == "read" else "remote_manager",
        "token_secret_ref": "file:" + str(target / (role + ".secret")), "channels": channels,
        "permissions": ["read"] if role == "read" else ["read", "configure"]} for role in tokens]}
    if config.get("recording", {}).get("enabled"):
        config["recording"]["directory"] = str(Path(config["recording"]["directory"]).expanduser().absolute())
    assemble(config, fake=False, resolve_secret=lambda reference: tokens[Path(reference[5:]).stem])
    profile = {"version": 1, "deployment_id": secrets.token_hex(16), "host": "127.0.0.1",
               "port": config.get("network", {}).get("port", 18081),
               "credentials": {role: "file:" + role + ".secret" for role in tokens},
               "gateway_config": str(target / "gateway.toml")}
    files = {role + ".secret": (token + "\n").encode() for role, token in tokens.items()}
    files.update({"gateway.toml": config_text(config).encode("utf-8"), "client.json": encoded(profile)})
    create_profile(target, files)
    maybe_default(root, name)
    return target / "gateway.toml"


def export_credential(*, name: str | None = None, access: str = "read", output: Path | None = None,
                      directory: Path | None = None) -> Path:
    if access not in ("read", "manage"):
        raise ValueError("匯出角色僅接受 read 或 manage。")
    root = (directory or config_directory()).expanduser().absolute()
    selected = connection_name(name) if name is not None else default_name(root)
    profile = read_profile(root, selected)
    reference = profile["credentials"].get(access)
    if reference is None:
        raise ValueError("此連線沒有該角色的憑證，未匯出其他身分。")
    if reference.startswith("file:") and not Path(reference[5:]).is_absolute():
        reference = "file:" + str(profile_path(root, selected) / reference[5:])
    token = TokenSource(reference).read()
    target = output or root / "transfers" / f"{selected}.{access}.{secrets.token_hex(4)}.uart-credential"
    if target.suffix != ".uart-credential":
        raise ValueError("憑證傳輸包請使用 .uart-credential 副檔名，不是一般連線設定。")
    if in_repository(target.absolute()):
        raise ValueError("憑證傳輸包須放在版本庫之外。")
    write_private(target, encoded({"version": 1, "deployment_id": profile["deployment_id"], "access": access,
                                  "token": token, "port": profile["port"]}))
    return target


def import_credential(data: bytes, *, name: str = "default", port: int | None = None,
                      directory: Path | None = None) -> str:
    if len(data) > 16384:
        raise ValueError("憑證傳輸包超過 16 KiB。")
    try:
        bundle = json.loads(data)
        if (not isinstance(bundle, dict) or set(bundle) != {"version", "deployment_id", "access", "token", "port"}
                or type(bundle["version"]) is not int or bundle["version"] != 1
                or bundle["access"] not in ("read", "manage")):
            raise ValueError
        if not isinstance(bundle["token"], str):
            raise ValueError
        validate_token(bundle["token"])
        endpoint("127.0.0.1", bundle["port"])
        identifier = bundle["deployment_id"]
        if not isinstance(identifier, str) or len(identifier) != 32 or any(c not in "0123456789abcdef" for c in identifier):
            raise ValueError
    except (ValueError, TypeError, RecursionError):
        raise ValueError("憑證傳輸包格式不合法；未安裝，也未顯示秘密內容。") from None
    role = bundle["access"]
    root = root_directory(directory)
    target = profile_path(root, connection_name(name))
    number = bundle["port"] if port is None else port
    endpoint("127.0.0.1", number)
    if target.exists() or target.is_symlink():
        profile = read_profile(root, name)
        if profile["deployment_id"] != identifier or profile["port"] != number:
            raise ValueError("既有連線屬於不同部署或 Port，請使用其他連線名稱；未覆蓋。")
        if role in profile["credentials"]:
            ref = profile["credentials"][role]
            if ref.startswith("file:") and not Path(ref[5:]).is_absolute():
                ref = "file:" + str(target / ref[5:])
            if not hmac.compare_digest(TokenSource(ref).read(), bundle["token"]):
                raise ValueError("該角色已有不同憑證；未覆蓋，輪替須協調 Gateway 與 Client。")
            return role
        secret = target / (role + ".secret")
        write_private(secret, (bundle["token"] + "\n").encode())
        try:
            profile["credentials"][role] = "file:" + role + ".secret"
            write_private(target / "client.json", encoded(profile), replace=True)
        except BaseException:
            secret.unlink(missing_ok=True)
            raise
    else:
        create_profile(target, {role + ".secret": (bundle["token"] + "\n").encode(),
            "client.json": encoded({"version": 1, "deployment_id": identifier, "host": "127.0.0.1",
                                     "port": number, "credentials": {role: "file:" + role + ".secret"}})})
    maybe_default(root, name)
    return role
