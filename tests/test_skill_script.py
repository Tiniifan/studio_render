"""Visibility of the objects of a technique script, cut by cut: the game's scripts have up to 3 attackers and 3
defenders, the previews made for studio_render (BleuRusse's HissatsuScene4) up to 4 of each."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from project import Project, Template
from skill_script import SkillScript


GAME = """
function SkillFunc( _success )
{
    SetupNum(3, 3, 1, 1, 0);
    SetupEnable(1, "atk", 1, 0, 0);
    SetupEnable(2, "atk", 1, 1, 1);
    SetupEnable(3, "def", 1);
}
"""

FOUR = """
function SkillFunc( _success )
{
    SetupNum(6, 4, 4, 1, 0);
    SetupEnable(1, "atk", 1, 0, 0, 0);
    SetupEnable(2, "atk", 0, 0, 0, 0);
    SetupEnable(3, "atk", 0, 0, 0, 0);
    SetupEnable(4, "atk", 0, 1, 1, 1);
    SetupEnable(5, "atk", 1, 1, 1, 1);
    SetupEnable(6, "atk", 0, 0, 0, 0);
    SetupEnable(1, "def", 0, 0, 0, 0);
    SetupEnable(2, "def", 1, 1, 1, 1);
    SetupEnable(3, "def", 1, 1, 1);
    SetupEnable(4, "def", 0, 0, 0, 0);
    SetupEnable(5, "def", 0, 0, 0, 0);
    SetupEnable(6, "def", 0, 0, 0, 0);
}
"""


def test_game_script_with_three_attackers_is_unchanged():
    script = SkillScript.parse(GAME)
    assert script.counts["atk"] == 3 and script.counts["def"] == 1
    assert [script.visible(f"atk{i}", 0, 10) for i in (1, 2, 3)] == [True, False, False]
    assert [script.visible(f"atk{i}", 1, 10) for i in (1, 2, 3)] == [True, True, True]
    # Beyond SetupNum: never drawn
    assert not script.visible("atk4", 1, 10)
    assert not script.visible("def2", 2, 10)
    assert script.visible("def1", 2, 10)


def test_four_attackers_and_defenders():
    script = SkillScript.parse(FOUR)
    assert script.counts["atk"] == 4 and script.counts["def"] == 4
    assert [script.visible("atk4", cut, 10) for cut in range(6)] == [False, False, False, True, True, False]
    assert [script.visible("atk1", cut, 10) for cut in range(6)] == [True, False, False, False, True, False]
    assert script.visible("def4", 1, 10)
    # A missing 4th flag hides the 4th object
    assert not script.visible("def4", 2, 10)
    assert script.visible("def3", 2, 10)


class _Custom:
    def __init__(self, name):
        self.name = name


def test_template_slot_bound_to_a_fourth_attacker_is_hidden_cut_by_cut():
    project = Project("IE4")
    project.template = Template({
        "name": "HissatsuScene4",
        "script_visibility": True,
        "auto_bind": {"hide_unbound": True, "slots": [
            {"target": "Fei Rune", "archive": "*_aa4.xc", "swapped": "*_ad4.xc", "role": "atk4", "swapped_role": "def4"},
            {"target": "Tom Gato", "archive": "*_ad4.xc", "swapped": "*_aa4.xc", "role": "def4", "swapped_role": "atk4"},
        ]},
    })
    fei, tom = _Custom("Fei Rune"), _Custom("Tom Gato")
    targets = {"Fei Rune": fei, "Tom Gato": tom}
    project.slot_target = lambda slot: targets[slot["target"]]
    project.animation_of = lambda target: object()
    project.skill_script = lambda: SkillScript.parse(FOUR)
    project.scene.owned = lambda owner: [owner]
    for cut, (fei_shown, tom_shown) in enumerate([(False, False), (False, True), (False, False),
                                                  (True, False), (True, False), (False, False)]):
        project.cut_at = lambda frame, cut=cut: (cut, 10)
        hidden = project.hidden_at(0)
        assert (fei not in hidden, tom not in hidden) == (fei_shown, tom_shown), cut
    project.template.swapped = True
    project.cut_at = lambda frame: (3, 10)
    hidden = project.hidden_at(0)
    assert tom not in hidden and fei in hidden
