"""Parametric desk enclosure for the LCDWIKI ES3C28P (2.8" ESP32-S3 display).

Run headless:
  /Applications/Blender.app/Contents/MacOS/Blender -b -P gen_enclosure.py

Outputs (next to this script):
  stl/front_bezel.stl   print face-down
  stl/back_shell.stl    print back-down
  enclosure.blend       assembled model with dummy board/battery/speaker
  renders/*.png         preview images

Coordinates (mm, Blender units = mm): X = board width, 0 at the USB-C edge;
Z = board height, 0 at the bottom edge (landscape, as the UI is drawn);
Y = depth, 0 at the bezel's front face, increasing toward the back.
Board numbers come from the official outline drawing
(LCDWIKI ES3C28P Specification V1.0, p.13).
"""
from __future__ import annotations

import math
from pathlib import Path

import bmesh
import bpy
from mathutils import Matrix, Vector

OUT = Path(__file__).resolve().parent

# ---------------------------------------------------------------- board (official drawing)
PCB_W, PCB_H, PCB_T = 86.0, 50.0, 1.6
FRONT_STACK = 4.3          # touch glass top above the PCB front face
BACK_PARTS = 4.7           # tallest SMD part on the back
HOLES = [(4.0, 4.0), (82.0, 4.0), (4.0, 46.0), (82.0, 46.0)]  # Ø3.2
HOLE_PAD_D = 5.6           # keep-out ring around each hole on the back
GLASS_X = (8.22, 77.42)    # glass spans the full board height
VIEW_X = (16.99, 75.04)    # visible area; offset toward the mic end
VIEW_Z = (3.20, 46.80)

# Positions in front-view coordinates. The back photo shows the board flipped top-to-bottom
# (USB-C stays on the left), so the drawing's back-view offsets measured from the top edge
# are heights from the bottom here. Drawing values, cross-checked against the photo (±6%).
MIC_X, MIC_Z = (79.5, 10.0)            # front-side port of the downward-facing MEMS mic
USBC_Z, USBC_OPEN_H = 23.1, 14.0       # USB-C on the X=0 edge, back side
BUTTON_X = 3.3                         # RESET / BOOT on the back, near the USB-C edge
BUTTON_SLOTS_Z = [(8.0, 15.0), (33.0, 40.0)]  # RESET at z≈11.6, BOOT at z≈36.6

# Power switch (SS12D00-style slide switch) on the X=0 wall above the USB-C, spliced into
# the battery's red lead. Body lies against the inner wall, long axis along Y; the lever
# pokes out through a slot and slides along Y.
SWITCH_BODY = (8.6, 3.6, 3.5)  # length (Y), lever-side depth (X), height (Z)
SWITCH_Y, SWITCH_Z = 17.0, 38.0
SWITCH_SLOT = (5.2, 2.4)       # Y x Z: lever 1.5 + travel 1.5 + clearance
SWITCH_RIB = 1.2

# ---------------------------------------------------------------- parts we house
SPEAKER = (28.0, 10.0, 40.0)   # X, Y(depth), Z  (user-measured 40 x 28 x 10)
SPEAKER_CENTER = (63.0, 25.0)
BATTERY = (34.0, 10.0, 50.0)   # 103450 LiPo (34 x 50 x 10, e.g. 2300 mAh pack) standing on its long side, leads up
BATTERY_X0 = 9.5

# ---------------------------------------------------------------- enclosure
WALL = 2.0                     # side walls: 5 perimeters at 0.4 mm on a P1S
BACK_WALL = 2.5                # thicker back: stiffens the grille and sets the screw depth below
CLEAR = 0.5                    # board-to-wall gap
CORNER_R = 4.0
BEZEL_T = 1.6
BEZEL_GAP = 0.2                # bezel-to-shell fit clearance
WINDOW_MARGIN = 0.6            # beyond the visible area
WINDOW_CHAMFER = 1.2           # 45° bevel on the front face
BACK_CLEAR = 16.0              # PCB back -> back-wall inner: parts 4.7 + slack 1.3 + battery/speaker 10.
                               # The battery sits over the plugged BAT connector (~4.5 mm + lead), so keep >= 6 mm.
BOSS_OD, PILOT_D = 5.4, 2.5    # M3 self-tapping into the bezel bosses
SCREW_LEN = 25.0               # M3 x 25 flat head (length includes the countersunk head)
PILOT_SKIN = 0.8               # bezel front skin left in front of the blind pilot holes
STANDOFF_OD, SCREW_CLEAR_D = 5.6, 3.4
CSK_D, CSK_DEPTH = 6.6, 1.6    # 90° countersink for M3 flat heads
TILT_DEG = 15.0
POCKET_RIM, POCKET_H, POCKET_GAP = 1.6, 4.0, 0.4
GRILLE_HOLE_D, GRILLE_PITCH = 2.0, 3.2

# Derived Y planes
Y_BEZEL_IN = BEZEL_T
Y_PCB_FRONT = Y_BEZEL_IN + FRONT_STACK
Y_PCB_BACK = Y_PCB_FRONT + PCB_T
Y_WALL_IN = Y_PCB_BACK + BACK_CLEAR
Y_BACK = Y_WALL_IN + BACK_WALL

IN_X0, IN_X1 = -CLEAR, PCB_W + CLEAR
IN_Z0, IN_Z1 = -CLEAR, PCB_H + CLEAR
OUT_X0, OUT_X1 = IN_X0 - WALL, IN_X1 + WALL
OUT_Z0, OUT_Z1 = IN_Z0 - WALL, IN_Z1 + WALL


# ---------------------------------------------------------------- mesh helpers
def link(name: str, bm: bmesh.types.BMesh) -> bpy.types.Object:
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    obj = bpy.data.objects.new(name, me)
    bpy.context.collection.objects.link(obj)
    return obj


def prism(name: str, outline_xz: list[tuple[float, float]], y0: float, y1: float) -> bpy.types.Object:
    """Extrudes a convex or concave XZ outline (CCW) along Y."""
    bm = bmesh.new()
    front = [bm.verts.new((x, y0, z)) for x, z in outline_xz]
    back = [bm.verts.new((x, y1, z)) for x, z in outline_xz]
    bm.faces.new(list(reversed(front)))
    bm.faces.new(back)
    n = len(outline_xz)
    for i in range(n):
        j = (i + 1) % n
        bm.faces.new([front[i], front[j], back[j], back[i]])
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    return link(name, bm)


def rounded_rect(x0: float, x1: float, z0: float, z1: float, r: float, seg: int = 10) -> list[tuple[float, float]]:
    r = max(0.0, min(r, (x1 - x0) / 2, (z1 - z0) / 2))
    pts = []
    for cx, cz, a0 in ((x1 - r, z0 + r, -90), (x1 - r, z1 - r, 0), (x0 + r, z1 - r, 90), (x0 + r, z0 + r, 180)):
        for k in range(seg + 1):
            a = math.radians(a0 + 90 * k / seg)
            pts.append((cx + r * math.cos(a), cz + r * math.sin(a)))
    return pts


def box(name, x0, x1, y0, y1, z0, z1):
    return prism(name, [(x0, z0), (x1, z0), (x1, z1), (x0, z1)], y0, y1)


def cylinder_y(name, cx, cz, d, y0, y1, seg=40):
    # Half-step phase keeps vertices off the axis-aligned extremes, which otherwise can
    # coincide with other geometry and leave EXACT-boolean slivers.
    ph = math.pi / seg
    pts = [(cx + d / 2 * math.cos(2 * math.pi * k / seg + ph), cz + d / 2 * math.sin(2 * math.pi * k / seg + ph))
           for k in range(seg)]
    return prism(name, pts, y0, y1)


def cone_y(name, cx, cz, d0, d1, y0, y1, seg=40):
    """Truncated cone along Y: diameter d0 at y0, d1 at y1."""
    bm = bmesh.new()
    a = [bm.verts.new((cx + d0 / 2 * math.cos(2 * math.pi * k / seg), y0, cz + d0 / 2 * math.sin(2 * math.pi * k / seg))) for k in range(seg)]
    b = [bm.verts.new((cx + d1 / 2 * math.cos(2 * math.pi * k / seg), y1, cz + d1 / 2 * math.sin(2 * math.pi * k / seg))) for k in range(seg)]
    bm.faces.new(list(reversed(a)))
    bm.faces.new(b)
    for i in range(seg):
        j = (i + 1) % seg
        bm.faces.new([a[i], a[j], b[j], b[i]])
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    return link(name, bm)


def frustum_y(name, rect0, rect1, y0, y1):
    """Rectangular frustum: rect0=(x0,x1,z0,z1) at y0, rect1 at y1."""
    bm = bmesh.new()
    def ring(r, y):
        x0, x1, z0, z1 = r
        return [bm.verts.new(v) for v in ((x0, y, z0), (x1, y, z0), (x1, y, z1), (x0, y, z1))]
    a, b = ring(rect0, y0), ring(rect1, y1)
    bm.faces.new(list(reversed(a)))
    bm.faces.new(b)
    for i in range(4):
        j = (i + 1) % 4
        bm.faces.new([a[i], a[j], b[j], b[i]])
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    return link(name, bm)


def stadium_y(name, cx, z0, z1, w, y0, y1, seg=12):
    """Vertical slot (rounded ends) of width w from z0 to z1, along Y."""
    r = w / 2
    pts = []
    for k in range(seg + 1):
        a = math.pi + math.pi * k / seg
        pts.append((cx + r * math.cos(a), z0 + r + r * math.sin(a)))
    for k in range(seg + 1):
        a = math.pi * k / seg
        pts.append((cx + r * math.cos(a), z1 - r + r * math.sin(a)))
    return prism(name, pts, y0, y1)


def boolean(target, tool, op):
    mod = target.modifiers.new(name=f"{op}_{tool.name}", type="BOOLEAN")
    mod.operation = op
    mod.solver = "EXACT"
    mod.object = tool
    bpy.context.view_layer.objects.active = target
    bpy.ops.object.modifier_apply(modifier=mod.name)
    bpy.data.objects.remove(tool, do_unlink=True)


def union_all(target, tools):
    for t in tools:
        boolean(target, t, "UNION")


def cut_all(target, tools):
    for t in tools:
        boolean(target, t, "DIFFERENCE")


# ---------------------------------------------------------------- parts
def build_bezel() -> bpy.types.Object:
    gx = BEZEL_GAP
    plate = prism("front_bezel", rounded_rect(IN_X0 + gx, IN_X1 - gx, IN_Z0 + gx, IN_Z1 - gx, CORNER_R - WALL - gx),
                  0.0, Y_BEZEL_IN)
    bosses = [cylinder_y(f"boss{i}", x, z, BOSS_OD, Y_BEZEL_IN - 0.01, Y_PCB_FRONT) for i, (x, z) in enumerate(HOLES)]
    union_all(plate, bosses)

    m, c = WINDOW_MARGIN, WINDOW_CHAMFER
    win = (VIEW_X[0] - m, VIEW_X[1] + m, VIEW_Z[0] - m, VIEW_Z[1] + m)
    bevel = frustum_y("win_bevel", (win[0] - c, win[1] + c, win[2] - c, win[3] + c), win, -0.01, c)
    straight = box("win", *win[:2], c - 0.01, Y_BEZEL_IN + 0.01, *win[2:])
    mic = stadium_y("mic", MIC_X, MIC_Z - 3.0, MIC_Z + 3.0, 3.0, -0.01, Y_BEZEL_IN + 0.01)
    # Blind pilot holes: leave 0.8 mm of front skin so the screws never show.
    pilots = [cylinder_y(f"pilot{i}", x, z, PILOT_D, PILOT_SKIN, Y_PCB_FRONT + 0.01) for i, (x, z) in enumerate(HOLES)]
    cut_all(plate, [bevel, straight, mic, *pilots])
    return plate


def build_shell() -> bpy.types.Object:
    shell = prism("back_shell", rounded_rect(OUT_X0, OUT_X1, OUT_Z0, OUT_Z1, CORNER_R), 0.0, Y_BACK)
    cavity = prism("cavity", rounded_rect(IN_X0, IN_X1, IN_Z0, IN_Z1, CORNER_R - WALL), -0.01, Y_WALL_IN)
    boolean(shell, cavity, "DIFFERENCE")

    # Tilt foot: a wedge under the bottom, thick at the front and zero at the back, so the
    # box rests on its back-bottom edge and the screen leans back by TILT_DEG.
    t = math.tan(math.radians(TILT_DEG))
    fx0, fx1 = OUT_X0 + CORNER_R, OUT_X1 - CORNER_R
    bm = bmesh.new()
    prof = [(0.0, OUT_Z0 + 0.01), (Y_BACK, OUT_Z0 + 0.01), (Y_BACK, OUT_Z0 - 0.01), (0.0, OUT_Z0 - Y_BACK * t)]
    left = [bm.verts.new((fx0, y, z)) for y, z in prof]
    right = [bm.verts.new((fx1, y, z)) for y, z in prof]
    bm.faces.new(left)
    bm.faces.new(list(reversed(right)))
    for i in range(4):
        j = (i + 1) % 4
        bm.faces.new([left[i], right[i], right[j], left[j]])
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    foot = link("foot", bm)

    standoffs = [cylinder_y(f"standoff{i}", x, z, STANDOFF_OD, Y_PCB_BACK, Y_WALL_IN + 0.01) for i, (x, z) in enumerate(HOLES)]

    # Speaker and battery pockets: rims on the back wall's inner face.
    sx, sz = SPEAKER_CENTER
    sw, _, sh = SPEAKER
    g, r = POCKET_GAP, POCKET_RIM
    y_rim0 = Y_WALL_IN - POCKET_H
    spk_outer = box("spk_rim", sx - sw / 2 - g - r, sx + sw / 2 + g + r, y_rim0, Y_WALL_IN + 0.01,
                    sz - sh / 2 - g - r, sz + sh / 2 + g + r)
    bw = BATTERY[0]
    bat_l = box("bat_rim_l", BATTERY_X0 - g - r, BATTERY_X0 - g, y_rim0, Y_WALL_IN + 0.01, IN_Z0, IN_Z1)
    bat_r = box("bat_rim_r", BATTERY_X0 + bw + g, BATTERY_X0 + bw + g + r, y_rim0, Y_WALL_IN + 0.01, IN_Z0, IN_Z1)
    # Switch cradle: two ribs above/below the body, plus a back stop behind it.
    sl, sd, sh_ = SWITCH_BODY
    rib_x1 = IN_X0 + sd + 0.2
    gap = sh_ / 2 + 0.15
    sw_ribs = [
        box("sw_rib_lo", IN_X0 - 0.01, rib_x1, SWITCH_Y - sl / 2 - 1, SWITCH_Y + sl / 2 + 1,
            SWITCH_Z - gap - SWITCH_RIB, SWITCH_Z - gap),
        box("sw_rib_hi", IN_X0 - 0.01, rib_x1, SWITCH_Y - sl / 2 - 1, SWITCH_Y + sl / 2 + 1,
            SWITCH_Z + gap, SWITCH_Z + gap + SWITCH_RIB),
        box("sw_stop", IN_X0 - 0.01, rib_x1, SWITCH_Y + sl / 2 + 0.2, SWITCH_Y + sl / 2 + 1.2,
            SWITCH_Z - gap, SWITCH_Z + gap),
    ]
    union_all(shell, [foot, *standoffs, spk_outer, bat_l, bat_r, *sw_ribs])
    spk_inner = box("spk_in", sx - sw / 2 - g, sx + sw / 2 + g, y_rim0 - 0.01, Y_WALL_IN, sz - sh / 2 - g, sz + sh / 2 + g)
    boolean(shell, spk_inner, "DIFFERENCE")

    cuts = []
    for i, (x, z) in enumerate(HOLES):
        cuts.append(cylinder_y(f"screw{i}", x, z, SCREW_CLEAR_D, Y_PCB_BACK - 0.01, Y_BACK + 0.01))
        cuts.append(cone_y(f"csk{i}", x, z, SCREW_CLEAR_D, CSK_D, Y_BACK - CSK_DEPTH, Y_BACK + 0.01))
    # Speaker grille: hex grid of holes inside an ellipse a bit smaller than the cone.
    ax, az = sw / 2 - 2.5, sh / 2 - 2.5
    row = 0
    z = sz - az
    while z <= sz + az + 1e-6:
        offs = (GRILLE_PITCH / 2) if row % 2 else 0.0
        x = sx - ax + offs
        while x <= sx + ax + 1e-6:
            if ((x - sx) / ax) ** 2 + ((z - sz) / az) ** 2 <= 1.0:
                cuts.append(cylinder_y(f"g{len(cuts)}", x, z, GRILLE_HOLE_D, Y_WALL_IN - 0.01, Y_BACK + 0.01, seg=16))
            x += GRILLE_PITCH
        z += GRILLE_PITCH * math.sqrt(3) / 2
        row += 1
    # USB-C opening in the X=0 side wall, centred on the connector (back side of the PCB).
    cuts.append(box("usbc", OUT_X0 - 0.01, IN_X0 + 0.01, Y_PCB_BACK - 2.5, Y_PCB_BACK + 5.5,
                    USBC_Z - USBC_OPEN_H / 2, USBC_Z + USBC_OPEN_H / 2))
    # Power switch lever slot.
    cuts.append(box("sw_slot", OUT_X0 - 0.01, IN_X0 + 0.01, SWITCH_Y - SWITCH_SLOT[0] / 2, SWITCH_Y + SWITCH_SLOT[0] / 2,
                    SWITCH_Z - SWITCH_SLOT[1] / 2, SWITCH_Z + SWITCH_SLOT[1] / 2))
    # Paper-clip slots for RESET / BOOT.
    for i, (z0, z1) in enumerate(BUTTON_SLOTS_Z):
        cuts.append(stadium_y(f"btn{i}", BUTTON_X, z0, z1, 3.0, Y_WALL_IN - 0.01, Y_BACK + 0.01))
    cut_all(shell, cuts)
    return shell


def build_dummies() -> list[bpy.types.Object]:
    """Board, battery and speaker stand-ins for the assembly preview (not exported)."""
    pcb = box("dummy_pcb", 0, PCB_W, Y_PCB_FRONT, Y_PCB_BACK, 0, PCB_H)
    glass = box("dummy_glass", GLASS_X[0], GLASS_X[1], Y_BEZEL_IN, Y_PCB_FRONT, 0, PCB_H)
    parts = box("dummy_parts", 6, 80, Y_PCB_BACK, Y_PCB_BACK + BACK_PARTS, 6, 44)
    sx, sz = SPEAKER_CENTER
    spk = box("dummy_speaker", sx - SPEAKER[0] / 2, sx + SPEAKER[0] / 2, Y_WALL_IN - SPEAKER[1], Y_WALL_IN,
              sz - SPEAKER[2] / 2, sz + SPEAKER[2] / 2)
    bat = box("dummy_battery", BATTERY_X0, BATTERY_X0 + BATTERY[0], Y_WALL_IN - BATTERY[1], Y_WALL_IN,
              (IN_Z0 + IN_Z1) / 2 - BATTERY[2] / 2, (IN_Z0 + IN_Z1) / 2 + BATTERY[2] / 2)
    sl, sd, sh_ = SWITCH_BODY
    sw = box("dummy_switch", IN_X0, IN_X0 + sd, SWITCH_Y - sl / 2, SWITCH_Y + sl / 2,
             SWITCH_Z - sh_ / 2, SWITCH_Z + sh_ / 2)
    return [pcb, glass, parts, spk, bat, sw]


def cleanup(obj, dist: float = 1e-3) -> None:
    """Merges near-coincident verts and dissolves slivers the EXACT boolean can leave behind."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=dist)
    bmesh.ops.dissolve_degenerate(bm, edges=bm.edges, dist=dist)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(obj.data)
    bm.free()


# ---------------------------------------------------------------- checks / export / render
def check(obj) -> dict:
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bad = [e for e in bm.edges if not e.is_manifold]
    non_manifold = len(bad)
    for e in bad[:8]:
        a, b = (tuple(round(c, 2) for c in v.co) for v in e.verts)
        print(f"[enclosure] {obj.name} non-manifold edge {a} -> {b}")
    vol = bm.calc_volume()
    bm.free()
    dims = tuple(round(d, 2) for d in obj.dimensions)
    return {"non_manifold_edges": non_manifold, "volume_cm3": round(vol / 1000, 2), "dims_mm": dims}


def collisions(dummies, solids) -> list[str]:
    """Reports dummy parts that intersect a printed part (boolean intersect volume > 0)."""
    hits = []
    for d in dummies:
        for s in solids:
            probe = s.copy()
            probe.data = s.data.copy()
            bpy.context.collection.objects.link(probe)
            mod = probe.modifiers.new("x", "BOOLEAN")
            mod.operation, mod.solver, mod.object = "INTERSECT", "EXACT", d
            bpy.context.view_layer.objects.active = probe
            bpy.ops.object.modifier_apply(modifier="x")
            bm = bmesh.new()
            bm.from_mesh(probe.data)
            v = abs(bm.calc_volume())
            bm.free()
            bpy.data.objects.remove(probe, do_unlink=True)
            if v > 0.5:
                hits.append(f"{d.name} x {s.name}: {v:.1f} mm^3")
    return hits


def export_stl(obj, path: Path, rot: Matrix) -> None:
    """Exports a copy rotated into its print orientation and dropped onto z=0."""
    tmp = obj.copy()
    tmp.data = obj.data.copy()
    bpy.context.collection.objects.link(tmp)
    tmp.data.transform(rot)
    zmin = min(v.co.z for v in tmp.data.vertices)
    tmp.data.transform(Matrix.Translation((0, 0, -zmin)))
    bpy.ops.object.select_all(action="DESELECT")
    tmp.select_set(True)
    bpy.context.view_layer.objects.active = tmp
    path.parent.mkdir(exist_ok=True)
    bpy.ops.wm.stl_export(filepath=str(path), export_selected_objects=True, global_scale=1.0, ascii_format=False)
    bpy.data.objects.remove(tmp, do_unlink=True)


def material(name, rgba):
    m = bpy.data.materials.new(name)
    m.diffuse_color = rgba
    return m


def render_views(objs: dict) -> None:
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.display.shading.light = "STUDIO"
    scene.display.shading.color_type = "MATERIAL"
    scene.display.shading.show_shadows = True
    scene.render.resolution_x, scene.render.resolution_y = 1400, 1000
    scene.render.film_transparent = False
    scene.world = scene.world or bpy.data.worlds.new("w")
    scene.unit_settings.scale_length = 0.001

    colours = {"front_bezel": (0.12, 0.12, 0.14, 1), "back_shell": (0.85, 0.86, 0.88, 1),
               "dummy_pcb": (0.05, 0.2, 0.1, 1), "dummy_glass": (0.02, 0.05, 0.12, 1),
               "dummy_parts": (0.3, 0.3, 0.3, 1), "dummy_speaker": (0.1, 0.1, 0.1, 1),
               "dummy_battery": (0.2, 0.4, 0.8, 1), "dummy_switch": (0.9, 0.3, 0.1, 1)}
    for o in objs.values():
        o.data.materials.clear()
        o.data.materials.append(material(o.name, colours.get(o.name, (0.6, 0.6, 0.6, 1))))

    # Stand the assembly up on the desk: Z up, leaning back by TILT_DEG about the front-bottom edge.
    pivot = Vector((0, Y_BACK, OUT_Z0))
    tilt = (Matrix.Translation(pivot) @ Matrix.Rotation(math.radians(-TILT_DEG), 4, "X") @ Matrix.Translation(-pivot))
    for o in objs.values():
        o.matrix_world = tilt

    cam_data = bpy.data.cameras.new("cam")
    cam_data.lens = 55
    cam = bpy.data.objects.new("cam", cam_data)
    scene.collection.objects.link(cam)
    scene.camera = cam
    center = Vector((PCB_W / 2, 14, PCB_H / 2 - 6))

    def shoot(name, eye, hide=()):
        for o in objs.values():
            o.hide_render = o.name in hide
        cam.location = center + Vector(eye)
        cam.rotation_euler = (center - cam.location).to_track_quat("-Z", "Y").to_euler()
        scene.render.filepath = str(OUT / "renders" / f"{name}.png")
        bpy.ops.render.render(write_still=True)

    shoot("front", (-90, -190, 70))
    shoot("back", (120, 220, 80))
    shoot("side", (-260, 20, 10))
    shoot("inside", (110, 230, 60), hide=("back_shell",))
    shoot("shell_inside", (60, -200, 60), hide=("front_bezel", "dummy_pcb", "dummy_glass", "dummy_parts"))


def main() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bezel = build_bezel()
    shell = build_shell()
    dummies = build_dummies()

    cleanup(bezel)
    cleanup(shell)
    report = {"front_bezel": check(bezel), "back_shell": check(shell)}
    hits = collisions(dummies, [bezel, shell])
    print("[enclosure] report", report)
    print("[enclosure] collisions", hits or "none")
    # With the flat head flush in its countersink, the tip ends at Y_BACK - SCREW_LEN.
    tip_y = Y_BACK - SCREW_LEN
    bite = Y_PCB_FRONT - tip_y
    print(f"[enclosure] outer {OUT_X1 - OUT_X0:.1f} x {Y_BACK:.1f} x {OUT_Z1 - OUT_Z0:.1f} mm (W x D x H), "
          f"M3x{SCREW_LEN:.0f} tip at y={tip_y:.2f} (skin ends {PILOT_SKIN}), thread bite {bite:.1f} mm")
    if tip_y < PILOT_SKIN + 0.15 or bite < 4.0:
        raise SystemExit(f"screw length mismatch: tip_y={tip_y:.2f}, bite={bite:.1f}")

    # Print orientation: bezel front face (y=0) down; shell back face (y=Y_BACK) down.
    export_stl(bezel, OUT / "stl" / "front_bezel.stl", Matrix.Rotation(math.radians(90), 4, "X"))
    export_stl(shell, OUT / "stl" / "back_shell.stl", Matrix.Rotation(math.radians(-90), 4, "X"))

    render_views({o.name: o for o in [bezel, shell, *dummies]})
    bpy.ops.wm.save_as_mainfile(filepath=str(OUT / "enclosure.blend"))


main()
