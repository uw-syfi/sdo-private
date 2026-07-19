from pathlib import Path


def test_runtime_smoke_uses_explicit_test_lifecycle_unless_real_agents_are_opted_in() -> None:
    script = (Path(__file__).parents[5] / "scripts/smoke_sdo_runtime_kind.sh").read_text()

    assert 'real_lifecycle="${SDO_SMOKE_REAL_LIFECYCLE:-0}"' in script
    assert 'if os.environ["REAL_LIFECYCLE"] == "1":' in script
    assert "run_initial_lifecycle(" in script
    assert "ensure_operational_memory(" in script
    assert 'REAL_LIFECYCLE="${real_lifecycle}"' in script
    assert 'allow_test_lifecycle=os.environ["REAL_LIFECYCLE"] != "1"' in script


def test_image_build_checks_the_benchmark_adapter_as_the_runtime_uid() -> None:
    script = (Path(__file__).parents[5] / "scripts/build_sdo_images.sh").read_text()

    assert "--target sregym-responder" in script
    assert "--user 65532:65532" in script
    assert "benchmarks.sregym.adapter.submission --help" in script
