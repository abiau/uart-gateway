"""Gateway 設定版本的離線檢查、預覽與明確套用。"""

from dataclasses import asdict
from hashlib import sha256
from pathlib import Path
import tomllib

from .configured_launcher import config_text
from .server.app import assemble


def read_version(path: Path, *, fake: bool) -> tuple[dict, dict]:
    with path.open("rb") as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise ValueError("Gateway 設定不得超過 64 KiB。")
    try:
        config = tomllib.loads(raw.decode("utf-8-sig"))
    except (UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise ValueError("Gateway 設定必須是有效的 UTF-8 TOML。") from exc

    def validate_reference(reference: str) -> str:
        kind, _, value = reference.partition(":")
        if kind not in ("env", "file") or not value:
            raise ValueError("秘密參照必須是 env: 或 file:，並且不得為空。")
        # 只驗證結構。實際啟動仍須由既有載入器取得真實憑證。
        return sha256(reference.encode()).hexdigest()

    gateway, host, port = assemble(config, fake=fake, resolve_secret=validate_reference)
    sections = {"network": {"host": host, "port": port},
                "gateway": config.get("gateway", {}),
                "recording": config.get("recording", {}),
                "audit": config.get("audit", {}),
                "channels": {key: {"label": channel.config.label,
                    "target_note": channel.config.target_note, "auto_open_rx": channel.config.auto_open_rx,
                    "serial": asdict(channel.config.serial),
                    "adapter": None if fake else asdict(channel.port.selector)}
                    for key, channel in gateway.channels.items()},
                "grants": {principal.id: {key: sorted(value) for key, value in principal.grants.items()}
                           for principal in gateway.principals}}
    return config, sections


def manage_config(current: Path, *, candidate: Path | None, output: Path | None,
                  apply: str | None, fake: bool) -> dict:
    original, before = read_version(current, fake=fake)
    if candidate is None:
        if output is not None or apply is not None:
            raise ValueError("套用時必須指定候選 TOML。")
        return {"settings": before}
    proposed, after = read_version(candidate, fake=fake)
    before_auth, after_auth = original["auth"]["principals"], proposed["auth"]["principals"]
    old_refs = {entry["id"]: entry["token_secret_ref"] for entry in before_auth}
    new_refs = {entry["id"]: entry["token_secret_ref"] for entry in after_auth}
    changes = {key: {"before": before[key], "after": after[key]}
               for key in before if before[key] != after[key]}
    refs_changed = sorted(key for key in old_refs.keys() | new_refs.keys() if old_refs.get(key) != new_refs.get(key))
    serialized = config_text(proposed).encode()
    plan = sha256(config_text(original).encode() + b"\0" + serialized).hexdigest()
    result = {"plan": plan, "changes": changes, "credential_references_changed": refs_changed}
    if apply is not None:
        if plan != apply or output is None:
            raise ValueError("套用需要目前預覽識別與新檔路徑，請重新預覽。")
        if len(serialized) > 65536:
            raise ValueError("產生的 Gateway 設定超過 64 KiB。")
        with output.open("xb") as stream:
            try:
                stream.write(serialized)
                stream.flush()
            except BaseException:
                stream.close()
                output.unlink(missing_ok=True)
                raise
        result["output"] = str(output.absolute())
    elif output is not None:
        raise ValueError("指定輸出需要先預覽並提供 --apply 識別。")
    return result
