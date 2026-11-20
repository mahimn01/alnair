# What this does: records docs/demo/verify-gate.gif, a terminal recording of examples/verify-gate.sh.
# Why: the README shows this GIF near the top, so a visitor sees the verify gate pass one answer and
#   refuse another before reading anything.
# How to run: install vhs (https://github.com/charmbracelet/vhs), then from the repository root:
#   vhs docs/demo/demo.tape
#   The example prints the folder it runs from, so record from a checkout whose path you don't mind
#   showing. The GIF in the repository was recorded from a clone in /tmp/alnair.
# What it changes: writes docs/demo/verify-gate.gif. The example itself makes no model call, uses no
#   network, and writes only to a temporary folder that it deletes.

Output docs/demo/verify-gate.gif

Require bash

Set Shell "bash"
Set FontSize 16
Set Width 1150
Set Height 870
Set Padding 20
Set TypingSpeed 45ms

# Hidden setup: a plain prompt, so no machine name shows, and a pause before each step of the example, so
# the recording can be read. The example prints the same thing without the pause, in about a second.
Hide
Type "export PS1='$ ' DEMO_PAUSE=1.5 && clear"
Enter
Show

Type "./examples/verify-gate.sh"
Sleep 800ms
Enter

# Five steps with a pause before each take about nine seconds; the rest is time to read the result.
Sleep 15s
