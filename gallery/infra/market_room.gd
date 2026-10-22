# market_room.gd
# Procedural market room for the leak_demo.json story.
# Tested against the Godot 4.2 API. Built-in meshes and materials only,
# no addons, no external assets.
#
# What maps to what, stated plainly:
# leak_demo.json does not export per-symbol returns. Its "curve" holds two
# equity series, honest and naive, and the only symbol named anywhere in the
# file is SYM2 (the RESTATEMENT_LEAK record). So the two columns here are the
# two books from the story, honest and naive, and their heights come from
# each series' total return between the first and last curve points. There
# is no column for SYM2 because the JSON gives no return series for it.
#
# Palette per docs/DESIGN.md: canvas #0B0C10, lavender #D0C4DF is the truth,
# crimson #E5484D is the leak. The verdicts in the file are inverted from
# what a newcomer expects: the honest book grades SUSPICIOUS, the naive book
# grades REAL. The labels keep the file's own verdicts.

extends Node3D

const CURVE_PATH := "res://gallery/data/leak_demo.json"

const CANVAS := Color(0.043, 0.047, 0.063)    # #0B0C10
const PANEL := Color(0.071, 0.078, 0.110)     # #12141C
const LAVENDER := Color(0.816, 0.769, 0.875)  # #D0C4DF
const CRIMSON := Color(0.898, 0.282, 0.302)   # #E5484D

const COLUMN_R := 0.6
const STRIP_LEN := 14.0
const HEIGHT_PER_PCT := 0.15
const MIN_H := 0.2
const MAX_H := 9.0
const RIG_SPEED := 0.12  # radians per second, slow orbit

var _rig: Node3D


# Parses leak_demo.json and returns it whole. Empty Dictionary on failure,
# with the reason pushed to the error log.
static func load_curve(path: String) -> Dictionary:
	var parsed: Variant = JSON.parse_string(FileAccess.get_file_as_string(path))
	if typeof(parsed) != TYPE_DICTIONARY:
		push_error("load_curve: not a JSON object: %s" % path)
		return {}
	return parsed as Dictionary


# Total return of one equity series over the curve, in percent.
static func _total_return_pct(data: Dictionary, series: String) -> float:
	var curve: Array = data.get("curve", [])
	if curve.size() < 2:
		return 0.0
	var first: Dictionary = curve[0]
	var last: Dictionary = curve[curve.size() - 1]
	var start: float = first.get(series, 0.0)
	var end: float = last.get(series, 0.0)
	if start == 0.0:
		return 0.0
	return (end / start - 1.0) * 100.0


func _make_material(c: Color, emissive: bool) -> StandardMaterial3D:
	var mat := StandardMaterial3D.new()
	mat.albedo_color = c
	mat.roughness = 0.85
	if emissive:
		mat.emission_enabled = true
		mat.emission = c
		mat.emission_energy_multiplier = 1.2
	return mat


func _column(height: float, c: Color) -> MeshInstance3D:
	var mesh_instance := MeshInstance3D.new()
	var cyl := CylinderMesh.new()
	cyl.top_radius = COLUMN_R
	cyl.bottom_radius = COLUMN_R
	cyl.height = height
	mesh_instance.mesh = cyl
	mesh_instance.material_override = _make_material(c, false)
	mesh_instance.position.y = height / 2.0
	return mesh_instance


func _strip(c: Color, x: float) -> MeshInstance3D:
	var mesh_instance := MeshInstance3D.new()
	var box := BoxMesh.new()
	box.size = Vector3(0.5, 0.04, STRIP_LEN)
	mesh_instance.mesh = box
	mesh_instance.material_override = _make_material(c, true)
	mesh_instance.position = Vector3(x, 0.02, 0.0)
	return mesh_instance


func _label(text: String, c: Color, pos: Vector3) -> Label3D:
	var label := Label3D.new()
	label.text = text
	label.modulate = c
	label.font_size = 64
	label.outline_size = 12
	label.billboard = BaseMaterial3D.BILLBOARD_ENABLED
	label.position = pos
	return label


func _ready() -> void:
	var data := load_curve(CURVE_PATH)
	var honest_ret := _total_return_pct(data, "honest")
	var naive_ret := _total_return_pct(data, "naive")

	# Canvas and light. Dark mode is the default, per DESIGN.md.
	var env := Environment.new()
	env.background_mode = Environment.BG_COLOR
	env.background_color = CANVAS
	env.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	env.ambient_light_color = Color(0.55, 0.57, 0.64)
	env.ambient_light_energy = 0.6
	var world_env := WorldEnvironment.new()
	world_env.environment = env
	add_child(world_env)

	var sun := DirectionalLight3D.new()
	sun.rotation_degrees = Vector3(-55.0, -30.0, 0.0)
	sun.light_energy = 0.9
	add_child(sun)

	# Floor.
	var floor_mesh := MeshInstance3D.new()
	var plane := PlaneMesh.new()
	plane.size = Vector2(26.0, 18.0)
	floor_mesh.mesh = plane
	floor_mesh.material_override = _make_material(PANEL, false)
	add_child(floor_mesh)

	# Equity strips on the floor. Lavender is the honest book, crimson the
	# naive one. Crimson earns its place: that book is the leak.
	add_child(_strip(LAVENDER, -2.0))
	add_child(_strip(CRIMSON, 2.0))

	# One column per book, at the end of its strip. Height is that series'
	# total return, mapped linearly: 1.0 unit base plus 0.15 per percent.
	# honest: about +21.9 percent, naive: about +43.8 percent on the demo file.
	var honest_h := clampf(1.0 + honest_ret * HEIGHT_PER_PCT, MIN_H, MAX_H)
	var naive_h := clampf(1.0 + naive_ret * HEIGHT_PER_PCT, MIN_H, MAX_H)

	var honest_col := _column(honest_h, LAVENDER)
	honest_col.position = Vector3(-2.0, 0.0, -6.0)
	add_child(honest_col)
	add_child(_label("HONEST  SUSPICIOUS", LAVENDER,
			Vector3(-2.0, honest_h + 0.8, -6.0)))

	var naive_col := _column(naive_h, CRIMSON)
	naive_col.position = Vector3(2.0, 0.0, -6.0)
	add_child(naive_col)
	add_child(_label("NAIVE  REAL", CRIMSON,
			Vector3(2.0, naive_h + 0.8, -6.0)))

	# The leak line, in sulfur would be the WARN color, but this is the flag
	# itself, so it stays crimson per the palette rule.
	var leak: Dictionary = data.get("leak", {}).get("detail", {})
	var note := "RESTATEMENT_LEAK  %s  43 decision days  first 2024-01-02" \
			% str(leak.get("key", "SYM2.revenue"))
	add_child(_label(note, CRIMSON, Vector3(0.0, 1.2, 4.0)))

	# Rotating camera rig. Slow orbit, the room reads from any angle.
	_rig = Node3D.new()
	add_child(_rig)
	var cam := Camera3D.new()
	cam.position = Vector3(0.0, 6.0, 12.0)
	_rig.add_child(cam)
	cam.look_at(Vector3(0.0, 2.0, 0.0))
	cam.current = true


func _process(delta: float) -> void:
	if _rig != null:
		_rig.rotate_y(RIG_SPEED * delta)
