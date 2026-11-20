# Recording the demo

The README shows the output of [examples/verify-gate.sh](../../examples/verify-gate.sh) as text. A moving picture of the same run, and of a real session, would do more on the first screen. Neither is in the repository yet, because a recording has to be made by a person at a terminal.

## The verify gate (scripted)

[demo.tape](demo.tape) is a script for [vhs](https://github.com/charmbracelet/vhs). From the repository root:

```sh
brew install vhs          # or see the vhs README for other systems
vhs docs/demo/demo.tape   # writes docs/demo/verify-gate.gif
```

The tape was written without vhs installed, so check the result before you use it. If lines wrap badly, change `Set Width` or `Set FontSize` in the tape. When the GIF looks right, put it in the README above the text output:

```markdown
![The verify gate passing one answer and refusing another](docs/demo/verify-gate.gif)
```

## A real session (by hand)

vhs can't drive a model session in a way that comes out the same twice, so record this one yourself with any screen recorder. Keep it under 30 seconds. A sequence that shows what Alnair adds:

1. `alnair demo`, so the session opens on the sample notes and nothing private is on screen.
2. Ask "What is my AMZN exit plan?" and let the lookup, read and verify steps show.
3. Show the status line with the model the router picked.
4. Type `/lab-model opus` and send a second prompt, so the switch is visible.

Before recording: use a clean terminal profile, a scratch folder, and a window about 1280 by 720 so the text stays readable when the GIF is scaled down.
