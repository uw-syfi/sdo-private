print("Importing AppOperator")
from app_operator.operator import AppOperator

print("Imported AppOperator")
from app_operator.config import Config, DeploymentConfig
from app_operator.filesystem import InMemoryFilesystem
from pathlib import Path
from unittest.mock import MagicMock

print("Creating mocks")
agent = MagicMock()
fs = InMemoryFilesystem()
repo_path = Path("/tmp/repo")
fs.mkdir(repo_path)
config = Config(deployment=DeploymentConfig(platform="k8s", target="local"))

print("Instantiating AppOperator")
# We need to mock init_trajectory to avoid real FS usage
import unittest.mock

with unittest.mock.patch("app_operator.operator.init_trajectory") as mock_traj:
    op = AppOperator(
        repo_path=str(repo_path), filesystem=fs, agent=agent, config=config
    )
    print("AppOperator instantiated")

    sds_config = repo_path / ".sds" / "config.toml"
    if fs.exists(sds_config):
        print("Config exists")
        print(fs.read_text(sds_config))
    else:
        print("Config NOT found")
