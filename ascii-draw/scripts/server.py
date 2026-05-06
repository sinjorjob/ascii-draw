"""ASCII Draw local bridge server.

Browser form -> POST /generate -> spawn `claude -p <prompt>` -> return ASCII art.
Uses the user's existing Claude Code login (no API key needed).
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import subprocess
import json
import os
import re
import sys
import shutil
import time
import tempfile
import threading

PORT = 8765
HOST = "127.0.0.1"
HERE = os.path.dirname(os.path.abspath(__file__))         # <skill>/scripts
SKILL_ROOT = os.path.dirname(HERE)                        # <skill>
ASSETS_DIR = os.path.join(SKILL_ROOT, "assets")
# Inherit the launcher's cwd as PROJECT_ROOT so claude CLI Read/Glob/Grep
# resolve against the user's project. Falls back to SKILL_ROOT when the
# server was started from inside scripts/ (e.g. start.bat double-click).
try:
    _launcher_cwd = os.getcwd()
    PROJECT_ROOT = SKILL_ROOT if os.path.samefile(_launcher_cwd, HERE) else _launcher_cwd
except OSError:
    PROJECT_ROOT = SKILL_ROOT
CLAUDE_TIMEOUT = 900  # seconds — claude -p can take a while for complex diagrams

CLAUDE = (
    shutil.which("claude")
    or shutil.which("claude.cmd")
    or shutil.which("claude.exe")
)

SYSTEM_PROMPT = """You are an adaptive diagram designer. Output STRUCTURED JSON
that is rendered server-side into perfectly aligned ASCII. Pick the section
types that fit the user's request — DO NOT cram everything into boxes.

Tools available: Read, Glob, Grep (read-only). Be minimal. Never ask clarifying questions.

# Output format (STRICT)
Return EXACTLY ONE JSON object:
{
  "title": "<optional top heading>",
  "sections": [ <section>, <section>, ... ]
}
Sections stack vertically with a blank line between them.

# Section types — pick the right tool for each part of the answer

## 1. flow — boxes connected by arrows (architecture, dataflow)
{
  "type": "flow",
  "rows": [ [<node>, ...], [<node>, ...] ],
  "edges": [ {"from":"id1","to":"id2","label":"short"} ]
}
Node: {"id":"...", "title":"short", "lines":["detail1","detail2"], "color":"B"}

## 2. list — numbered or bulleted items (steps, features, descriptions)
{
  "type": "list",
  "ordered": true,
  "items": [
    {"label":"Step name", "desc":"what happens here", "color":"B"},
    ...
  ]
}
ordered=false → bullets (•). Use when explaining steps in detail
or when user wants per-component descriptions BELOW the diagram.

## 3. text — free prose paragraph
{"type":"text", "content":"Multi-line\\nprose."}
Use for intros, summaries, footnotes.

## 4. legend — color key
{"type":"legend", "items":[{"color":"B","label":"UI / client"}, ...]}
Use when several colors are used in the diagram.

## 5. heading — sub-section heading
{"type":"heading", "text":"## 詳細"}
Use to separate major parts of the answer.

## 6. divider — horizontal rule
{"type":"divider"}

# Adaptive design — CRITICAL — read carefully
DO NOT force everything into flow boxes. Mix sections by intent:
- "Show architecture" → just flow
- "Show flow with each step explained in detail" → flow + list
- "Concept overview" → text + flow
- "Explain X with components and their roles" → text + flow + list
- "Compare A vs B" → list (or 2 flow sections)
- "Color-rich diagram" → flow + legend at bottom
- "Line-by-line breakdown of a script" → list only (no flow!)

If the user asks for "step descriptions BELOW the diagram", you MUST add a
LIST section after the FLOW section. NEVER cram descriptions inside more boxes.

# Color codes (1 letter)
B=blue T=teal G=green L=oLive M=aMber O=orange R=red P=plum N=neutral

# Flow layout rules (when using flow sections)
Canvas is landscape (~110 cols × 28 rows). Aim wider-than-tall:
- 1-3 nodes: 1 row
- 4 nodes: 2 rows × 2
- 5-6 nodes: 2 rows × 2-3
- 7-9 nodes: 3 rows × 2-3
- 10+ nodes: 3-4 rows × 3-4
NEVER produce a single column of more than 3 boxes. NEVER more rows than columns when ≥4 nodes.

# Worked example: "Show Browser→Server→DB flow with step descriptions"
{
  "title":"Web App Architecture",
  "sections":[
    {"type":"flow",
     "rows":[[
       {"id":"b","title":"ブラウザ","color":"B"},
       {"id":"s","title":"Server","color":"O"},
       {"id":"d","title":"DB","color":"M"}
     ]],
     "edges":[
       {"from":"b","to":"s","label":"HTTP"},
       {"from":"s","to":"d","label":"SQL"}
     ]
    },
    {"type":"list","ordered":true,"items":[
      {"label":"ブラウザ","desc":"ユーザーが操作するUI。リクエスト発行","color":"B"},
      {"label":"Server","desc":"Pythonで実装。リクエストを処理しDBへ問い合わせ","color":"O"},
      {"label":"DB","desc":"PostgreSQL。永続化されたデータを保持","color":"M"}
    ]},
    {"type":"legend","items":[
      {"color":"B","label":"UI / クライアント"},
      {"color":"O","label":"処理ロジック"},
      {"color":"M","label":"データストア"}
    ]}
  ]
}

# Final output regulation (HIGHEST PRIORITY)
- ONLY the JSON object. First char `{`, last char `}`.
- No preamble, no explanation, no markdown, no code fences.
- Even if file inspection fails, still output JSON based on the prompt alone.
"""

CREATE_TEMPLATE = "{prompt}"

EDIT_TEMPLATE = """Modify this existing ASCII diagram according to the user's instruction.

Current diagram:
{current}

User instruction: {prompt}

Output the COMPLETE modified diagram (full output, not a diff)."""


EDIT_SELECTION_SYSTEM_PROMPT = """You are an ASCII fragment editor.
The user has selected a portion of a larger ASCII diagram and wants you to
modify only that fragment.

Output rules (CRITICAL):
- Output ONLY the modified ASCII text. Plain text, nothing else.
- NO JSON, NO markdown, NO code fences (no ``` or ~~~), NO preamble, NO explanation.
- Try to keep the same line count and similar widths as the original fragment.
- Box drawing chars: ─ │ ┌ ┐ └ ┘ ├ ┤ ┬ ┴ ┼
- Arrows: only ASCII > < ^ v (1 col, width-stable). NEVER → ← ↑ ↓ ▶ ◀ ▲ ▼.
- Japanese chars count as 2 visual cols, ASCII as 1. Pad with trailing spaces
  so right edges align as in the original fragment.
- If the user asks something that requires more space, you may add lines, but
  keep the changes localized.
"""

EDIT_SELECTION_USER_TEMPLATE = """Selected fragment ({width} visual cols x {height} rows):

{selection}

Modification instruction: {prompt}

Output the modified fragment ONLY (no preamble, no JSON, no fences)."""


def strip_fences(text: str) -> str:
    """Strip ``` code fences and surrounding whitespace if present."""
    text = text.strip()
    if text.startswith("```"):
        nl = text.find("\n")
        if nl >= 0:
            text = text[nl + 1 :]
    text = text.rstrip()
    if text.endswith("```"):
        text = text[: text.rfind("```")].rstrip()
    return text


# =============== Layout engine ===============

def _is_full_width(ch: str) -> bool:
    if not ch:
        return False
    cp = ord(ch)
    return (
        (0x1100 <= cp <= 0x115F) or
        (0x2E80 <= cp <= 0x303E) or
        (0x3041 <= cp <= 0x33FF) or
        (0x3400 <= cp <= 0x4DBF) or
        (0x4E00 <= cp <= 0x9FFF) or
        (0xA000 <= cp <= 0xA4CF) or
        (0xAC00 <= cp <= 0xD7A3) or
        (0xF900 <= cp <= 0xFAFF) or
        (0xFE30 <= cp <= 0xFE4F) or
        (0xFF00 <= cp <= 0xFF60) or
        (0xFFE0 <= cp <= 0xFFE6) or
        (0x20000 <= cp <= 0x3FFFD)
    )


def _vw(s: str) -> int:
    """Visual width of a string (Japanese counts as 2 cols)."""
    return sum(2 if _is_full_width(c) else 1 for c in (s or ""))


def _pad_right(s: str, width: int) -> str:
    delta = width - _vw(s)
    return s + " " * delta if delta > 0 else s


def _pad_center(s: str, width: int) -> str:
    delta = width - _vw(s)
    if delta <= 0:
        return s
    left = delta // 2
    return " " * left + s + " " * (delta - left)


def _node_inner_width(node: dict) -> int:
    """Required INNER width (excluding borders) to fit node contents."""
    title_w = _vw(node.get("title", ""))
    body_w = max((_vw(l) for l in (node.get("lines") or [])), default=0)
    # +2 for left/right padding inside the box
    return max(title_w, body_w) + 2


def _render_node(node: dict, inner_w: int):
    """Render a node at given inner width. Returns list of (line, color_str)."""
    color = (node.get("color") or "N").upper()[:1]
    if color not in "BTGLMORPN":
        color = "N"
    title = node.get("title", "")
    lines = node.get("lines") or []

    rows = []
    # Top border
    top = "┌" + "─" * inner_w + "┐"
    rows.append((top, color * len(top)))
    # Title (centered)
    title_inner = _pad_center(title, inner_w)
    title_inner = _pad_right(title_inner, inner_w)
    title_line = "│" + title_inner + "│"
    rows.append((title_line, color * len(title_line)))
    # Body lines
    if lines:
        sep = "├" + "─" * inner_w + "┤"
        rows.append((sep, color * len(sep)))
        for line in lines:
            content = " " + _pad_right(line, inner_w - 1)
            content = _pad_right(content, inner_w)
            full = "│" + content + "│"
            rows.append((full, color * len(full)))
    # Bottom border
    bot = "└" + "─" * inner_w + "┘"
    rows.append((bot, color * len(bot)))
    return rows


def _render_horizontal_row(row_nodes, edges, forced_inner_w=None):
    """Render a single horizontal row of nodes side-by-side with arrows.
    If forced_inner_w is given, every node uses that inner width."""
    if not row_nodes:
        return [], []
    if forced_inner_w is not None:
        inner_widths = [forced_inner_w] * len(row_nodes)
    else:
        inner_widths = [_node_inner_width(n) for n in row_nodes]
    boxes = [_render_node(n, w) for n, w in zip(row_nodes, inner_widths)]

    # Pre-compute arrow strings and their VISUAL widths for each gap. The
    # gap on non-arrow rows is padded to the same visual width so box columns
    # stay aligned across all rows of the boxes.
    arrows = []  # list of (arrow_str, visual_w)
    for i in range(len(row_nodes) - 1):
        edge = next(
            (e for e in edges
             if e.get("from") == row_nodes[i].get("id")
             and e.get("to") == row_nodes[i + 1].get("id")),
            None
        )
        label = (edge.get("label") if edge else "") or ""
        if label:
            arrow_str = " " + label + " >> "
        else:
            arrow_str = "  >>  "
        arrows.append((arrow_str, _vw(arrow_str)))

    # Pad each box to the same height (max of all)
    max_h = max(len(b) for b in boxes)
    box_widths = [_vw(b[0][0]) for b in boxes]
    for bi, b in enumerate(boxes):
        while len(b) < max_h:
            blank = " " * box_widths[bi]
            b.append((blank, " " * box_widths[bi]))

    middle = max_h // 2
    out_lines, out_colors = [], []
    for r in range(max_h):
        line_parts, color_parts = [], []
        for bi, box in enumerate(boxes):
            line_parts.append(box[r][0])
            color_parts.append(box[r][1])
            if bi < len(boxes) - 1:
                arrow_str, arrow_vw = arrows[bi]
                if r == middle:
                    line_parts.append(arrow_str)
                    color_parts.append("N" * len(arrow_str))
                else:
                    # Pad with plain spaces sized to the arrow's VISUAL width
                    # so the next box's left border lands on the same column.
                    blank = " " * arrow_vw
                    line_parts.append(blank)
                    color_parts.append(" " * arrow_vw)
        out_lines.append("".join(line_parts))
        out_colors.append("".join(color_parts))
    return out_lines, out_colors


def render_layout(struct: dict):
    """Render a structured layout JSON to (diagram, colormap)."""
    rows = struct.get("rows") or []
    edges = struct.get("edges") or []
    title = struct.get("title", "")

    # Auto: if no rows but has 'nodes', wrap each into its own row (vertical)
    if not rows and "nodes" in struct:
        rows = [[n] for n in (struct.get("nodes") or [])]

    if not rows:
        return "", ""

    # Always force ALL boxes (across all rows) to share the same inner width.
    # This guarantees vertical alignment of every box border across the diagram.
    all_widths = [_node_inner_width(n) for r in rows if r for n in r]
    uniform_w = max(all_widths) if all_widths else 10
    uniform_w = max(uniform_w, 10)

    all_lines, all_colors = [], []

    # Optional title at top
    if title and isinstance(title, str):
        all_lines.append(title)
        all_colors.append("N" * len(title))
        all_lines.append("")
        all_colors.append("")

    for ri, row in enumerate(rows):
        if not row:
            continue
        lines, colors = _render_horizontal_row(row, edges, forced_inner_w=uniform_w)
        all_lines.extend(lines)
        all_colors.extend(colors)

        # Vertical arrow between rows
        if ri < len(rows) - 1 and rows[ri + 1]:
            # Arrow centered under the FIRST node of current row.
            # Use forced uniform width if active so arrows line up across rows.
            first_inner = uniform_w if uniform_w is not None else _node_inner_width(row[0])
            first_total = first_inner + 2  # incl borders
            arrow_col = first_total // 2

            current_ids = {n.get("id") for n in row}
            next_ids = {n.get("id") for n in rows[ri + 1]}
            cross = next(
                (e for e in edges
                 if e.get("from") in current_ids
                 and e.get("to") in next_ids),
                None
            )
            label = (cross.get("label") if cross else "") or ""

            ar1 = " " * arrow_col + "│"
            all_lines.append(ar1)
            all_colors.append(" " * arrow_col + "N")

            if label:
                ar2 = " " * arrow_col + "v" + "  " + label
                col2 = " " * arrow_col + "N" + "  " + "N" * len(label)
                all_lines.append(ar2)
                all_colors.append(col2)
            else:
                ar2 = " " * arrow_col + "v"
                all_lines.append(ar2)
                all_colors.append(" " * arrow_col + "N")

    return "\n".join(all_lines), "\n".join(all_colors)


def _render_text_section(section):
    content = section.get("content", "") or ""
    color_code = (section.get("color") or "N").upper()[:1]
    if color_code not in "BTGLMORPN":
        color_code = "N"
    out_lines, out_colors, out_weights = [], [], []
    for line in str(content).split("\n"):
        out_lines.append(line)
        out_colors.append(color_code * len(line))
        out_weights.append(" " * len(line))
    return out_lines, out_colors, out_weights


def _render_list_section(section):
    items = section.get("items") or []
    ordered = bool(section.get("ordered", True))
    out_lines, out_colors, out_weights = [], [], []
    for i, item in enumerate(items):
        if isinstance(item, str):
            label, desc, color_code = item, "", "N"
        else:
            label = item.get("label", "") or ""
            desc = item.get("desc", "") or ""
            color_code = (item.get("color") or "N").upper()[:1]
            if color_code not in "BTGLMORPN":
                color_code = "N"
        prefix = f"{i+1}. " if ordered else "• "
        line = prefix + label
        col = "N" * len(prefix) + color_code * len(label)
        # Bold the label only
        wgt = " " * len(prefix) + "B" * len(label)
        if desc:
            sep = "  —  "
            line += sep + desc
            col += "N" * len(sep) + "N" * len(desc)
            wgt += " " * len(sep) + " " * len(desc)
        out_lines.append(line)
        out_colors.append(col)
        out_weights.append(wgt)
    return out_lines, out_colors, out_weights


def _render_legend_section(section):
    items = section.get("items") or []
    out_lines, out_colors, out_weights = [], [], []
    header = "凡例:"
    out_lines.append(header)
    out_colors.append("N" * len(header))
    out_weights.append("B" * len(header))  # legend header is bold
    for item in items:
        if not isinstance(item, dict):
            continue
        color_code = (item.get("color") or "N").upper()[:1]
        if color_code not in "BTGLMORPN":
            color_code = "N"
        label = item.get("label", "") or ""
        prefix = "  ■ "
        line = prefix + label
        col_chars = []
        for ch in line:
            col_chars.append(color_code if ch == "■" else "N")
        out_lines.append(line)
        out_colors.append("".join(col_chars))
        out_weights.append(" " * len(line))
    return out_lines, out_colors, out_weights


def _render_heading_section(section):
    text = section.get("text", "") or ""
    # Strip leading markdown # markers
    stripped = text
    while stripped and stripped[0] == "#":
        stripped = stripped[1:]
    stripped = stripped.strip()
    if not stripped:
        return [], [], []
    # No explicit color (uses default dark ink), bold weight
    return [stripped], [" " * len(stripped)], ["B" * len(stripped)]


def _render_divider_section(section):
    width = int(section.get("width", 60))
    line = "─" * width
    return [line], ["N" * width], [" " * width]


def _render_flow_section(section):
    """Render a flow section using the existing layout engine. Flow has no bold."""
    text, colormap = render_layout({
        "rows": section.get("rows", []),
        "edges": section.get("edges", []),
    })
    lines = text.split("\n")
    colors = colormap.split("\n")
    weights = [" " * len(l) for l in lines]
    return lines, colors, weights


_SECTION_RENDERERS = {
    "flow": _render_flow_section,
    "list": _render_list_section,
    "text": _render_text_section,
    "legend": _render_legend_section,
    "heading": _render_heading_section,
    "divider": _render_divider_section,
}


def render_sections(struct: dict):
    """Top-level renderer. Returns (text, colormap, weightmap).
    Backward compat: if struct has 'rows' at top level, wrap as one flow section."""
    if "sections" not in struct and ("rows" in struct or "nodes" in struct):
        struct = {
            "title": struct.get("title", ""),
            "sections": [{"type": "flow",
                          "rows": struct.get("rows", []),
                          "edges": struct.get("edges", []),
                          "nodes": struct.get("nodes", [])}],
        }
    title = struct.get("title", "")
    sections = struct.get("sections") or []

    out_lines, out_colors, out_weights = [], [], []

    if title and isinstance(title, str) and title.strip():
        out_lines.append(title)
        out_colors.append(" " * len(title))    # default ink
        out_weights.append("B" * len(title))   # bold
        out_lines.append("")
        out_colors.append("")
        out_weights.append("")

    for i, section in enumerate(sections):
        if not isinstance(section, dict):
            continue
        sec_type = section.get("type", "flow")
        renderer = _SECTION_RENDERERS.get(sec_type)
        if not renderer:
            continue
        result = renderer(section)
        # Tolerate 2-tuple legacy returns
        if len(result) == 2:
            sec_lines, sec_colors = result
            sec_weights = [" " * len(l) for l in sec_lines]
        else:
            sec_lines, sec_colors, sec_weights = result
        if i > 0 or (title and out_lines):
            out_lines.append("")
            out_colors.append("")
            out_weights.append("")
        out_lines.extend(sec_lines)
        out_colors.extend(sec_colors)
        out_weights.extend(sec_weights)

    return "\n".join(out_lines), "\n".join(out_colors), "\n".join(out_weights)


# =============== End layout engine ===============


_TAG_RE = re.compile(r"\[([BTGLMORPN])\]|\[/[BTGLMORPN]?\]")


def parse_tagged_diagram(tagged: str):
    """Strip inline [X]...[/X] color tags. Return (clean_diagram, colormap)
    where both strings share the same shape — colormap has one char per
    diagram char, holding either a color code letter or ' '."""
    out_lines = []
    cm_lines = []
    for line in tagged.split("\n"):
        clean_chars = []
        cm_chars = []
        active = " "
        i = 0
        while i < len(line):
            m = _TAG_RE.match(line, i)
            if m:
                token = m.group(0)
                if token.startswith("[/"):
                    active = " "
                else:
                    active = m.group(1)
                i = m.end()
                continue
            clean_chars.append(line[i])
            cm_chars.append(active)
            i += 1
        out_lines.append("".join(clean_chars))
        cm_lines.append("".join(cm_chars))
    return "\n".join(out_lines), "\n".join(cm_lines)


def _extract_first_json(text: str):
    """Find the first balanced {...} JSON object embedded in text.
    Returns the parsed dict or None."""
    if not text:
        return None
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if esc:
            esc = False
            continue
        if ch == "\\" and in_str:
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def _from_obj(obj):
    """Render a JSON dict to (diagram, colormap, weightmap)."""
    if not isinstance(obj, dict):
        return "", "", ""
    # New section-based schema
    if "sections" in obj:
        clean, colormap, weightmap = render_sections(obj)
        if clean.strip():
            return clean, colormap, weightmap
    # Legacy: rows/nodes at top level
    if "rows" in obj or "nodes" in obj:
        clean, colormap, weightmap = render_sections(obj)
        if clean.strip():
            return clean, colormap, weightmap
    # Legacy: tagged ASCII diagram (no bold)
    diagram = obj.get("diagram", "")
    if isinstance(diagram, str) and diagram.strip():
        clean, cm = parse_tagged_diagram(diagram)
        wm = "\n".join(" " * len(l) for l in clean.split("\n"))
        return clean, cm, wm
    return "", "", ""


def parse_response(stdout: str):
    """Parse claude's stdout. Returns (diagram, colormap, weightmap)."""
    text = strip_fences(stdout)

    # 1) Try plain JSON
    try:
        obj = json.loads(text)
        clean, cm, wm = _from_obj(obj)
        if clean.strip():
            return clean, cm, wm
    except json.JSONDecodeError:
        pass

    # 2) Try extracting first {...} block
    obj = _extract_first_json(stdout)
    if obj is not None:
        clean, cm, wm = _from_obj(obj)
        if clean.strip():
            return clean, cm, wm

    # 3) Fallback: legacy tagged ASCII without JSON
    if "[" in text and "]" in text:
        clean, cm = parse_tagged_diagram(text)
        if clean.strip():
            wm = "\n".join(" " * len(l) for l in clean.split("\n"))
            return clean, cm, wm

    # 4) Last resort: plain text
    wm = "\n".join(" " * len(l) for l in text.split("\n"))
    return text, "", wm


def call_claude(prompt: str, system_prompt: str = None, structured: bool = True,
                timeout: int = CLAUDE_TIMEOUT):
    """Run claude CLI with the given prompt.
    If structured=True (default), parse response as structured JSON and return
    (diagram, colormap, weightmap). If False, return (raw_text, '', '')."""
    if not CLAUDE:
        raise RuntimeError(
            "`claude` CLI が PATH に見つかりません。Claude Code をインストールしてください。"
        )
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    sp = system_prompt if system_prompt is not None else SYSTEM_PROMPT

    work_dir = PROJECT_ROOT if os.path.isdir(PROJECT_ROOT) else HERE

    cmd = [
        CLAUDE,
        "-p",
        "--output-format", "text",
        "--system-prompt", sp,
        "--tools", "Read,Glob,Grep",
        "--allowed-tools", "Read,Glob,Grep",
        "--no-session-persistence",
        "--effort", "low",
        "--model", "opus",
        prompt,
    ]
    sys.stderr.write(f"[claude] structured={structured} sp={len(sp)}ch user={len(prompt)}ch cwd={work_dir}\n")
    sys.stderr.flush()

    start = time.time()
    popen_kwargs = dict(
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=work_dir,
    )
    # On Windows, put claude in its own process group so we can taskkill the whole tree.
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    proc = subprocess.Popen(cmd, **popen_kwargs)

    # Heartbeat so user can see it's still alive.
    stop_hb = threading.Event()
    def _heartbeat():
        while not stop_hb.wait(5):
            elapsed = int(time.time() - start)
            sys.stderr.write(f"[claude] still working... ({elapsed}s)\n")
            sys.stderr.flush()
    hb = threading.Thread(target=_heartbeat, daemon=True)
    hb.start()

    def _kill_tree():
        """Kill the entire process tree (claude.exe + spawned children)."""
        if os.name == "nt":
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    capture_output=True, timeout=10,
                )
            except Exception:
                proc.kill()
        else:
            proc.kill()

    try:
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            sys.stderr.write(f"[claude] timeout after {timeout}s — killing process tree\n")
            sys.stderr.flush()
            _kill_tree()
            try:
                proc.communicate(timeout=5)
            except Exception:
                pass
            raise
    finally:
        stop_hb.set()
        # Make sure no orphans remain even if everything completed normally.
        if proc.poll() is None:
            _kill_tree()

    elapsed = time.time() - start
    sys.stderr.write(f"[claude] done in {elapsed:.1f}s, exit={proc.returncode}, stdout={len(stdout or '')}ch, stderr={len(stderr or '')}ch\n")
    if stderr and stderr.strip():
        sys.stderr.write(f"[claude stderr] {stderr.strip()[:600]}\n")
    sys.stderr.flush()

    if proc.returncode != 0:
        err = (stderr or "").strip()[:600] or "(stderr empty)"
        raise RuntimeError(f"claude CLI failed (exit {proc.returncode}): {err}")
    if not (stdout or "").strip():
        raise RuntimeError("claude CLI returned empty output")
    if structured:
        diagram, colormap, weightmap = parse_response(stdout)
        sys.stderr.write(f"[claude] structured: diagram={len(diagram)}ch colormap={len(colormap)}ch weightmap={len(weightmap)}ch\n")
        sys.stderr.flush()
        return diagram, colormap, weightmap
    else:
        text = strip_fences(stdout)
        sys.stderr.write(f"[claude] raw text {len(text)}ch\n")
        sys.stderr.flush()
        return text, "", ""


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, ctype: str, body):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, obj, status: int = 200):
        self._send(status, "application/json; charset=utf-8",
                   json.dumps(obj, ensure_ascii=False))

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            try:
                with open(os.path.join(ASSETS_DIR, "index.html"), "rb") as f:
                    self._send(200, "text/html; charset=utf-8", f.read())
            except FileNotFoundError:
                self._send(404, "text/plain; charset=utf-8", "index.html not found")
            return
        if path == "/health":
            self._send_json({"ok": True, "claude": CLAUDE})
            return
        self._send(404, "text/plain; charset=utf-8", "not found")

    def do_POST(self):
        if self.path != "/generate":
            self._send(404, "text/plain; charset=utf-8", "not found")
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            prompt = (body.get("prompt") or "").strip()
            mode = body.get("mode", "create")
            current = (body.get("current") or "").strip()
            if not prompt:
                self._send_json({"ok": False, "error": "プロンプトが空です"}, 400)
                return

            sys.stderr.write(f"[generate] mode={mode} prompt={prompt!r}\n")
            sys.stderr.flush()

            # ------ edit-selection: modify only a fragment ------
            if mode == "edit-selection":
                sel = body.get("selection") or {}
                sel_text = (sel.get("text") or "").strip("\n")
                if not sel_text.strip():
                    self._send_json({"ok": False, "error": "選択範囲が空です"}, 400)
                    return
                full = EDIT_SELECTION_USER_TEMPLATE.format(
                    selection=sel_text,
                    width=int(sel.get("width", 0)),
                    height=int(sel.get("height", 0)),
                    prompt=prompt,
                )
                art, _, _ = call_claude(
                    full,
                    system_prompt=EDIT_SELECTION_SYSTEM_PROMPT,
                    structured=False,
                )
                self._send_json({
                    "ok": True,
                    "art": art,
                    "colormap": "",
                    "weightmap": "",
                    "patch_only": True,
                })
                return

            # ------ create / edit-full (structured) ------
            if mode == "edit" and current:
                full = EDIT_TEMPLATE.format(prompt=prompt, current=current)
            else:
                full = CREATE_TEMPLATE.format(prompt=prompt)

            art, colormap, weightmap = call_claude(full)
            sys.stderr.write(f"[generate] -> art={len(art)}ch colormap={len(colormap)}ch weightmap={len(weightmap)}ch\n")
            sys.stderr.flush()
            self._send_json({"ok": True, "art": art, "colormap": colormap, "weightmap": weightmap})
        except subprocess.TimeoutExpired:
            self._send_json({"ok": False, "error": f"タイムアウト ({CLAUDE_TIMEOUT}秒) — Claude CLI が応答しません"}, 504)
        except Exception as e:  # noqa: BLE001
            sys.stderr.write(f"[error] {type(e).__name__}: {e}\n")
            sys.stderr.flush()
            self._send_json({"ok": False, "error": f"{type(e).__name__}: {e}"}, 500)

    def log_message(self, fmt, *args):
        sys.stderr.write(f"[{self.log_date_time_string()}] {fmt % args}\n")


def main():
    import webbrowser

    if not CLAUDE:
        print("WARNING: `claude` CLI が PATH に見つかりません", file=sys.stderr)
    else:
        print(f"claude CLI: {CLAUDE}")

    try:
        server = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError as e:
        print()
        print(f"ERROR: ポート {PORT} を使えません ({e})")
        print("既に別のサーバーが {PORT} を占有しています。".format(PORT=PORT))
        print("以下を試してください:")
        print(f"  1. タスクマネージャーで python.exe を全て終了")
        print(f"  2. または PowerShell で: Get-NetTCPConnection -LocalPort {PORT} | Select-Object OwningProcess")
        print()
        input("Enter で閉じる...")
        return

    url = f"http://{HOST}:{PORT}/"
    print(f"ASCII Draw server ready: {url}")
    print("Ctrl+C で停止")

    # Server is bound and ready -> safe to open browser
    try:
        webbrowser.open(url)
    except Exception:
        pass

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n停止します...")
        server.shutdown()


if __name__ == "__main__":
    main()
