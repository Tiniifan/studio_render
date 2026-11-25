"""What the script of a technique (SkillFunc of a whsXXXX.nut) shows, cut by cut, as the game plays it.

Read from the Setup* calls of the script; their meaning comes from the game's system/ie4_ev_sub_skl.nut:
    SetupNum(cuts, attackers, defenders, balls, effects)
        only that many aaN / adN / blN / efN are loaded, the others are never drawn
    SetupEnable(cut, name, f1, f2, f3, f4)
        atk, def, bal, eff: in that cut, object i is hidden when fi is 0 or missing
        map: the map and the goal are hidden in that cut; gol: the goal only
    SetupMotion(cut, motion)
        the camera motion the cut plays ("04a" instead of "04", often when the technique fails); a camera of the .xv
        is found by its hash, the CRC32 of the motion name
    SetupFrameEnable(cut, frame, name, v1[, v2, v3, v4])
        map (map and goal) and bal (balls 1 to 4) only, the game ignores the other names: from frame - 1 of the cut,
        the visibility becomes v; the entries are taken in order, one at a time, and each cut starts from SetupEnable

Cuts are counted from 0 here; cut i plays the camera motion motions[i] ("01", "02"... by default).
SkillFunc( _success ) runs with _success true or false (the preview chooses): the if / else on _success are evaluated,
an if on anything else keeps both branches.
"""
import re
import zlib

KINDS = {"atk": 4, "def": 4, "bal": 4, "eff": 3}
CALL = re.compile(r"\b(SetupNum|SetupEnable|SetupFrameEnable|SetupMotion)\s*\(([^()]*)\)")
ROLE = re.compile(r"^(atk|def|bal|eff)([1-4])$|^(map|gol)$")


def _value(text):
    text = text.strip()
    if text.startswith('"') and text.endswith('"'):
        return text[1:-1]
    if text in ("true", "false"):
        return text == "true"
    try:
        return int(text)
    except ValueError:
        try:
            return float(text)
        except ValueError:
            return text


IF = re.compile(r"\bif\s*\(")
SUCCESS_TESTS = {
    "_success": True, "!_success": False,
    "_success==true": True, "_success==1": True, "_success!=false": True, "_success!=0": True,
    "_success==false": False, "_success==0": False, "_success!=true": False, "_success!=1": False,
}


def _closing(text, start, opening, closing):
    """Index of the bracket that closes the one at start."""
    depth = 0
    for index in range(start, len(text)):
        if text[index] == opening:
            depth += 1
        elif text[index] == closing:
            depth -= 1
            if depth == 0:
                return index
    return len(text)


def _skip_space(text, index):
    while index < len(text) and text[index].isspace():
        index += 1
    return index


def _branch(text, index, success):
    """(resolved text, end) of the statement after an if (cond) or an else: a block, another if, or one statement."""
    index = _skip_space(text, index)
    if text.startswith("{", index):
        end = _closing(text, index, "{", "}")
        return resolve(text[index + 1:end], success), end + 1
    if IF.match(text, index):
        return _if(text, index, success)
    end = text.find(";", index)
    end = len(text) if end < 0 else end + 1
    return text[index:end], end


def _if(text, index, success):
    """(resolved text, end) of the if statement at index."""
    match = IF.match(text, index)
    close = _closing(text, match.end() - 1, "(", ")")
    test = SUCCESS_TESTS.get(re.sub(r"\s+", "", text[match.end():close]))
    then_text, end = _branch(text, close + 1, success)
    else_text = ""
    after = _skip_space(text, end)
    if re.match(r"else\b", text[after:after + 5]):
        else_text, end = _branch(text, after + 4, success)
    if test is None:
        return then_text + "\n" + else_text, end
    return (then_text if test == success else else_text), end


def resolve(source, success=True):
    """The source with each if on _success replaced by the branch that runs."""
    parts, index = [], 0
    while True:
        match = IF.search(source, index)
        if match is None:
            parts.append(source[index:])
            return "".join(parts)
        parts.append(source[index:match.start()])
        text, index = _if(source, match.start(), success)
        parts.append(text)


def parse_role(role):
    """("atk", 0) for "atk1", ("map", 0) for "map"; None when it is not a role."""
    match = ROLE.match(str(role or "").lower())
    if not match:
        return None
    if match.group(3):
        return match.group(3), 0
    return match.group(1), int(match.group(2)) - 1


class SkillScript:
    def __init__(self):
        self.counts = {kind: 0 for kind in KINDS}
        self.cut_num = 0
        self.off = {kind: [0] * size for kind, size in KINDS.items()}     # hidden cuts (bits) of each object
        self.map_off = 0
        self.goal_off = 0
        self.map_frames = []        # (cut, frame, visible)
        self.ball_frames = []       # (cut, frame, [visible of balls 1 to 4])
        self.motions = [f"{n:02d}" for n in range(1, 13)]     # camera motion of each cut
        self.calls = 0

    @classmethod
    def parse(cls, source, success=True):
        if isinstance(source, (bytes, bytearray)):
            source = bytes(source).decode("utf-8", "replace")
        script = cls()
        for name, arguments in CALL.findall(resolve(source or "", success)):
            values = [_value(a) for a in arguments.split(",")] if arguments.strip() else []
            try:
                getattr(script, "_" + name)(values)
                script.calls += 1
            except (IndexError, TypeError, ValueError):
                continue
        return script

    def _SetupNum(self, values):
        if len(values) >= 5:
            self.cut_num = int(values[0])
            for kind, value in zip(("atk", "def", "bal", "eff"), values[1:5]):
                self.counts[kind] = int(value)

    def _SetupMotion(self, values):
        self.motions[int(values[0]) - 1] = str(values[1])

    def camera_hashes(self):
        """Hash of the camera each cut plays, in order (empty without SetupNum)."""
        return [zlib.crc32(motion.encode("ascii", "replace")) for motion in self.motions[:self.cut_num]]

    def _SetupEnable(self, values):
        bit = 1 << (int(values[0]) - 1)
        name = str(values[1])
        flags = [bool(v) for v in values[2:]] + [False] * 4
        if name == "map":
            self.map_off |= bit
        elif name == "gol":
            self.goal_off |= bit
        elif name in self.off:
            for i in range(KINDS[name]):
                if not flags[i]:
                    self.off[name][i] |= bit

    def _SetupFrameEnable(self, values):
        cut, frame, name = int(values[0]) - 1, int(values[1]) - 1, str(values[2])
        if name == "map":
            self.map_frames.append((cut, frame, bool(values[3])))
        elif name == "bal":
            self.ball_frames.append((cut, frame, [bool(v) for v in values[3:7]] + [True] * (4 - len(values[3:7]))))

    @staticmethod
    def _fired(events, cut, frame):
        """The SetupFrameEnable entries that took effect in this cut by this frame, in the game's order: one pending
        entry at a time, an entry of an earlier cut that was never reached blocks the next ones."""
        index = 0
        for earlier in range(cut):
            while index < len(events) and events[index][0] == earlier:
                index += 1
        fired = []
        if cut == 0 and frame < 3:
            return fired
        while index < len(events) and events[index][0] == cut and frame >= events[index][1]:
            fired.append(events[index])
            index += 1
        return fired

    def visible(self, role, cut, frame):
        """Is the object of a role drawn in a cut (from 0) at a frame of its camera."""
        parsed = parse_role(role)
        if parsed is None:
            return True
        kind, index = parsed
        bit = 1 << cut
        if kind in ("map", "gol"):
            shown = not self.map_off & bit
            if kind == "gol" and shown:
                shown = not self.goal_off & bit
            for _, _, visible in self._fired(self.map_frames, cut, frame):
                shown = visible
            return shown
        if index >= self.counts[kind]:
            return False
        shown = not self.off[kind][index] & bit
        if kind == "bal":
            for _, _, balls in self._fired(self.ball_frames, cut, frame):
                shown = balls[index]
        return shown
