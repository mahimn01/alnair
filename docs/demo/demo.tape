# What this does: records docs/demo/verify-gate.gif, a terminal recording of examples/verify-gate.sh.
# Why: the README shows that example's output as text; a GIF of the same run can sit above it.
# How to run: install vhs (https://github.com/charmbracelet/vhs), then from the repository root:
#   vhs docs/demo/demo.tape
# What it changes: writes docs/demo/verify-gate.gif. The example itself makes no model call, uses no
#   network, and writes only to a temporary folder that it deletes.

Output docs/demo/verify-gate.gif

Require bash

Set Shell "bash"
Set FontSize 15
Set Width 1280
Set Height 800
Set Padding 24
Set TypingSpeed 45ms

# A plain prompt, so no machine name or folder shows in the recording.
Hide
Type "export PS1='$ ' && clear"
Enter
Show

Type "./examples/verify-gate.sh"
Sleep 800ms
Enter

# The example takes a second or two; the rest is time to read the result.
Sleep 10s
