# Mood wheel pointer and colour follow-up

Use brighter saturated yellow, orange, magenta, violet, blue and mint segments, with a smaller central shading area. Preserve existing wheel geometry and delayed pulsing labels.

The physical pointer now follows the normal main-arc path even while the wheel is open. It is neither intercepted nor discarded. LEFT/RIGHT retain side-aware layer selection, GO plays, and hold GO exits. Touch/mouse movement on the disc still selects a mood point. Active-wheel buttons remain isolated from generic playback actions.

Regression coverage executes the real laser handler with an open wheel and confirms arc update, pointer angle and event completion.
