"""Two clocks, one leak.

A short Manim CE scene. Two horizontal number lines: event_time, what
happened, and knowledge_time, what the world knew and when. A playhead
walks event_time. A revision bar appears on knowledge_time only after the
playhead has passed, because the revision is future knowledge. A dashed
crimson arrow connects the now to that future bar. That distance is the
leak.

Targets Manim CE 0.18+. Render:
    manim -qm manim_two_clocks.py TwoClocks

Palette per docs/DESIGN.md. Lavender is the truth. Crimson is the leak,
used only where the leak is.
"""

from manim import (
    DOWN,
    LEFT,
    RIGHT,
    UP,
    Create,
    DashedLine,
    FadeIn,
    FadeOut,
    Flash,
    Line,
    Rectangle,
    Scene,
    Text,
    VGroup,
    Write,
)

CANVAS = "#0B0C10"
HAIRLINE = "#2A2D3A"
TEXT = "#F2F3F7"
MUTED = "#8A8FA3"
LAVENDER = "#D0C4DF"
CRIMSON = "#E5484D"

# event_time tick positions on the line. Same calendar on both clocks:
# a given date sits at the same x on either line.
X_FIRST_READ = -4.0   # 2024-01-02, first decision that read the revision early
X_PUBLISHED = -1.2    # 2024-03-01, the day the revision went public
X_LAST = 4.8          # 2026-11-16, last day of the demo curve
Y_EVENT = 1.3
Y_KNOWLEDGE = -1.3


class TwoClocks(Scene):
    def construct(self):
        self.camera.background_color = CANVAS

        clocks = self.build_clocks()
        self.play(
            FadeIn(clocks[0], shift=DOWN * 0.3),
            FadeIn(clocks[1], shift=UP * 0.3),
            run_time=1.2,
        )
        self.wait(0.6)

        # The playhead walks event_time. It passes the first-read tick,
        # then stops mid-window. 43 decision days live in this span.
        ph = self.build_playhead(X_FIRST_READ)
        self.play(FadeIn(ph), run_time=0.5)
        self.wait(0.4)
        self.play(self.shift_playhead(ph, -2.6), run_time=1.6)
        self.wait(0.5)

        # The bar lands after the playhead has passed. Order matters:
        # the read happened early, the knowledge shows up late.
        bar = Rectangle(
            width=0.22,
            height=0.9,
            stroke_color=CRIMSON,
            stroke_width=1.5,
            fill_color=CRIMSON,
            fill_opacity=1.0,
        ).move_to([X_PUBLISHED, Y_KNOWLEDGE, 0])
        self.play(
            Create(bar),
            Flash(bar, color=CRIMSON, line_length=0.3, flash_radius=0.45),
            run_time=0.9,
        )
        note = Text("SYM2.revenue, revised", font_size=21, color=MUTED)
        note.next_to(bar, RIGHT, buff=0.3)
        self.play(FadeIn(note), run_time=0.5)

        # The span on event_time that the naive backtest spent early-reading.
        window = Line(
            [X_FIRST_READ, Y_EVENT - 0.14, 0],
            [X_PUBLISHED, Y_EVENT - 0.14, 0],
            stroke_color=CRIMSON,
            stroke_width=5,
            stroke_opacity=0.5,
        )
        self.play(Create(window), run_time=0.6)

        # Dashed, crimson: the read reaches from now into the future.
        # Solid lines are facts. Dashed is the theft.
        start = [-2.6, Y_EVENT - 0.08, 0]
        end = [X_PUBLISHED + 0.15, Y_KNOWLEDGE + 0.5, 0]
        reach = (
            DashedLine(start, end, color=CRIMSON, dash_length=0.14)
            .add_tip(tip_length=0.18, tip_width=0.18)
        )
        self.play(Create(reach), run_time=1.2)

        leak = Text("the leak", font_size=32, color=CRIMSON)
        mid = [(start[0] + end[0]) / 2, (start[1] + end[1]) / 2, 0]
        leak.next_to(mid, RIGHT, buff=0.45)
        self.play(Write(leak), run_time=0.9)
        self.wait(1.4)

        # Clear the floor. The card carries no verdict, just the name.
        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.9)
        brand = Text("nohindsight", font_size=68, color=LAVENDER)
        rule = Line(LEFT * 2.6, RIGHT * 2.6, color=LAVENDER, stroke_width=1.5)
        rule.next_to(brand, DOWN, buff=0.32)
        self.play(Write(brand), run_time=1.4)
        self.play(Create(rule), run_time=0.6)
        self.wait(2.0)

    def build_clocks(self):
        # Same calendar, two clocks. event_time above, knowledge_time below,
        # so the arrow from now down to the future bar reads as a reach.
        def clock_line(y, name, ticks):
            line = Line(
                [-6, y, 0],
                [6, y, 0],
                stroke_color=HAIRLINE,
                stroke_width=2,
            )
            label = Text(name, font_size=26, color=MUTED)
            label.next_to(line.get_start(), UP if y > 0 else DOWN, buff=0.28)
            label.align_to(line, LEFT)
            parts = [line, label]
            for x, tick_label in ticks:
                tick = Line(
                    [x, y - 0.1, 0],
                    [x, y + 0.1, 0],
                    stroke_color=MUTED,
                    stroke_width=1.5,
                )
                parts.append(tick)
                text = Text(tick_label, font_size=19, color=MUTED)
                text.next_to(tick, DOWN if y > 0 else UP, buff=0.16)
                parts.append(text)
            return VGroup(*parts)

        event = clock_line(
            Y_EVENT,
            "event_time",
            [(X_FIRST_READ, "2024-01-02"), (X_PUBLISHED, "2024-03-01"), (X_LAST, "2026-11-16")],
        )
        knowledge = clock_line(
            Y_KNOWLEDGE,
            "knowledge_time",
            [(X_PUBLISHED, "2024-03-01")],
        )
        return [event, knowledge]

    def build_playhead(self, x):
        # A small downward pointer riding event_time, with its label above.
        arrow = Line(
            [x, Y_EVENT + 0.85, 0],
            [x, Y_EVENT + 0.16, 0],
            stroke_color=TEXT,
            stroke_width=3.5,
        ).add_tip(tip_length=0.16, tip_width=0.16)
        text = Text("now", font_size=24, color=TEXT)
        text.next_to(arrow, UP, buff=0.14)
        return VGroup(arrow, text)

    def shift_playhead(self, ph, x):
        # move_to keeps the vertical position fixed, only x changes.
        return ph.animate.move_to([x, ph.get_center()[1], 0])
