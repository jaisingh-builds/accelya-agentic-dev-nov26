"""All configuration comes from the environment (set in template.yaml). Nothing is hard-coded elsewhere."""
import os
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    learner_id: str
    service: str
    raw_prefix: str
    validated_prefix: str
    quarantine_prefix: str
    manifest_prefix: str
    rules_path: Path
    ref_dir: Path


def load_config(env=None):
    env = os.environ if env is None else env
    learner = env.get("LEARNER_ID", "").strip()
    if not learner:
        raise ConfigError("LEARNER_ID is not set")
    return Config(
        learner_id=learner,
        service=env.get("SERVICE_NAME", f"accelya-m4-{learner}-processor"),
        raw_prefix=env.get("RAW_PREFIX", "raw/"),
        validated_prefix=env.get("VALIDATED_PREFIX", "validated/"),
        quarantine_prefix=env.get("QUARANTINE_PREFIX", "quarantine/"),
        manifest_prefix=env.get("MANIFEST_PREFIX", "manifests/"),
        rules_path=Path(env.get("RULES_PATH", HERE / "rules" / "validation_rules.json")),
        ref_dir=Path(env.get("REF_DIR", HERE / "rules" / "ref")),
    )
