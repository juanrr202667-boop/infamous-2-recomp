import re
from pathlib import Path

path = Path("tools/ps3recomp/libs/input/cellPad.c")
content = path.read_text(encoding="utf-8")

old_pattern = re.compile(
    r"#ifdef _WIN32\s+static int pad_host_window_focused\(void\).*?#endif",
    re.DOTALL
)

new_code = """#ifdef _WIN32
static int pad_host_window_focused(void)
{
    static int ignore_focus = -1;
    if (ignore_focus < 0) ignore_focus = getenv("PAD_IGNORE_FOCUS") ? 1 : 0;
    if (ignore_focus) return 1;

    HWND fg = GetForegroundWindow();
    if (!fg) return 0;
    DWORD pid = 0;
    GetWindowThreadProcessId(fg, &pid);
    return pid == GetCurrentProcessId();
}

static void pad_poll_keyboard(void)
{
    static int off = -1;
    if (off < 0) off = getenv("PAD_NO_KEYBOARD") ? 1 : 0;
    if (off) return;

    PadHostState* hs = &s_host_state[0];
    if (!pad_host_window_focused()) {
        /* Release everything on focus loss, or a key held while alt-tabbing
         * would stay down forever. */
        hs->buttons = 0;
        hs->analog_lx = hs->analog_ly = 128;
        hs->analog_rx = hs->analog_ry = 128;
        hs->trigger_l2 = hs->trigger_r2 = 0;
        hs->press_up = hs->press_down = hs->press_left = hs->press_right = 0;
        hs->press_triangle = hs->press_circle = hs->press_cross = hs->press_square = 0;
        hs->press_l1 = hs->press_r1 = 0;
        hs->connected = 1;
        return;
    }

    static const struct { int vk; u16 btn; } map[] = {
        /* D-Pad (Arrows) */
        { VK_UP,     CELL_PAD_CTRL_UP },      { VK_DOWN,  CELL_PAD_CTRL_DOWN },
        { VK_LEFT,   CELL_PAD_CTRL_LEFT },    { VK_RIGHT, CELL_PAD_CTRL_RIGHT },

        /* Face buttons */
        /* Cross (Jump / Accept): Z, Space, E */
        { 'Z',       CELL_PAD_CTRL_CROSS },   { VK_SPACE, CELL_PAD_CTRL_CROSS },
        { 'E',       CELL_PAD_CTRL_CROSS },

        /* Circle (Roll / Cancel): X, C, Escape */
        { 'X',       CELL_PAD_CTRL_CIRCLE },  { 'C',      CELL_PAD_CTRL_CIRCLE },
        { VK_ESCAPE, CELL_PAD_CTRL_CIRCLE },

        /* Square (Melee attack): F */
        { 'F',       CELL_PAD_CTRL_SQUARE },

        /* Triangle (Interact / Drain / Tether): R, T */
        { 'R',       CELL_PAD_CTRL_TRIANGLE },{ 'T',      CELL_PAD_CTRL_TRIANGLE },

        /* Shoulders & Triggers */
        { VK_SHIFT,  CELL_PAD_CTRL_L1 },      { 'Q',      CELL_PAD_CTRL_L1 },
        { VK_LBUTTON,CELL_PAD_CTRL_R1 },      { 'V',      CELL_PAD_CTRL_R1 },
        { '1',       CELL_PAD_CTRL_L2 },      { '2',      CELL_PAD_CTRL_R2 },

        /* Stick clicks (L3: Radar pulse / R3: Zoom) */
        { 'B',       CELL_PAD_CTRL_L3 },      { 'N',      CELL_PAD_CTRL_R3 },

        /* Menus */
        { VK_RETURN, CELL_PAD_CTRL_START },   { 'P',      CELL_PAD_CTRL_START },
        { VK_TAB,    CELL_PAD_CTRL_SELECT },  { 'M',      CELL_PAD_CTRL_SELECT },
    };

    u16 btns = 0;
    for (unsigned i = 0; i < sizeof map / sizeof map[0]; i++)
        if (GetAsyncKeyState(map[i].vk) & 0x8000) btns |= map[i].btn;

    hs->buttons   = btns;
    hs->connected = 1;

    /* Left Analog Stick: WASD movement (with Arrow keys fallback) */
    int move_x = 0, move_y = 0;
    if (GetAsyncKeyState('A') & 0x8000) move_x -= 1;
    if (GetAsyncKeyState('D') & 0x8000) move_x += 1;
    if (GetAsyncKeyState('W') & 0x8000) move_y -= 1;
    if (GetAsyncKeyState('S') & 0x8000) move_y += 1;

    if (move_x == 0 && (btns & CELL_PAD_CTRL_LEFT))  move_x = -1;
    if (move_x == 0 && (btns & CELL_PAD_CTRL_RIGHT)) move_x = 1;
    if (move_y == 0 && (btns & CELL_PAD_CTRL_UP))    move_y = -1;
    if (move_y == 0 && (btns & CELL_PAD_CTRL_DOWN))  move_y = 1;

    hs->analog_lx = (u8)(move_x < 0 ? 0 : (move_x > 0 ? 255 : 128));
    hs->analog_ly = (u8)(move_y < 0 ? 0 : (move_y > 0 ? 255 : 128));

    /* Right Analog Stick: IJKL camera (with Numpad 8/2/4/6 fallback) */
    int cam_x = 0, cam_y = 0;
    if (GetAsyncKeyState('J') & 0x8000) cam_x -= 1;
    if (GetAsyncKeyState('L') & 0x8000) cam_x += 1;
    if (GetAsyncKeyState('I') & 0x8000) cam_y -= 1;
    if (GetAsyncKeyState('K') & 0x8000) cam_y += 1;

    if (cam_x == 0 && (GetAsyncKeyState(VK_NUMPAD4) & 0x8000)) cam_x = -1;
    if (cam_x == 0 && (GetAsyncKeyState(VK_NUMPAD6) & 0x8000)) cam_x = 1;
    if (cam_y == 0 && (GetAsyncKeyState(VK_NUMPAD8) & 0x8000)) cam_y = -1;
    if (cam_y == 0 && (GetAsyncKeyState(VK_NUMPAD2) & 0x8000)) cam_y = 1;

    hs->analog_rx = (u8)(cam_x < 0 ? 0 : (cam_x > 0 ? 255 : 128));
    hs->analog_ry = (u8)(cam_y < 0 ? 0 : (cam_y > 0 ? 255 : 128));

    hs->trigger_l2 = (u8)((btns & CELL_PAD_CTRL_L2) ? 255 : 0);
    hs->trigger_r2 = (u8)((btns & CELL_PAD_CTRL_R2) ? 255 : 0);

    /* Pressure-sensitive buttons: full deflection (255) when active */
    hs->press_up       = (btns & CELL_PAD_CTRL_UP)       ? 255 : 0;
    hs->press_down     = (btns & CELL_PAD_CTRL_DOWN)     ? 255 : 0;
    hs->press_left     = (btns & CELL_PAD_CTRL_LEFT)     ? 255 : 0;
    hs->press_right    = (btns & CELL_PAD_CTRL_RIGHT)    ? 255 : 0;
    hs->press_triangle = (btns & CELL_PAD_CTRL_TRIANGLE) ? 255 : 0;
    hs->press_circle   = (btns & CELL_PAD_CTRL_CIRCLE)   ? 255 : 0;
    hs->press_cross    = (btns & CELL_PAD_CTRL_CROSS)    ? 255 : 0;
    hs->press_square   = (btns & CELL_PAD_CTRL_SQUARE)   ? 255 : 0;
    hs->press_l1       = (btns & CELL_PAD_CTRL_L1)       ? 255 : 0;
    hs->press_r1       = (btns & CELL_PAD_CTRL_R1)       ? 255 : 0;

    { static int said = 0;
      if (!said && btns) { said = 1;
          printf("[cellPad] keyboard fallback active on port 0 (no XInput device)\\n");
          fflush(stdout); } }
}
#endif"""

match = old_pattern.search(content)
assert match is not None, "Pattern not found in cellPad.c"
updated = content[:match.start()] + new_code + content[match.end():]
# Normalize to CRLF as in original file
updated = updated.replace("\r\n", "\n").replace("\n", "\r\n")
path.write_bytes(updated.encode("utf-8"))
print("Patched cellPad.c successfully!")
