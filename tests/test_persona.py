"""Per-persona methodology config."""
import pytest
from api import db, persona


def test_defaults_are_generic():
    cfg = persona.get_persona_config()
    assert cfg["coach"]["mode"] == "generic" and cfg["dietitian"]["mode"] == "generic"


def test_set_custom_persists():
    persona.set_persona_config("dietitian", "custom", "Low-FODMAP fueling.")
    cfg = persona.get_persona_config()
    assert cfg["dietitian"]["mode"] == "custom" and "FODMAP" in cfg["dietitian"]["text"]


@pytest.mark.parametrize("args", [("nope", "generic", ""), ("coach", "weird", ""), ("coach", "custom", "")])
def test_set_rejects_bad_input(args):
    with pytest.raises(ValueError):
        persona.set_persona_config(*args)
