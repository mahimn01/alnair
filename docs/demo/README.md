# Recording the demo

## The verify gate (scripted)

[verify-gate.gif](verify-gate.gif) is the recording at the top of the README. It shows [examples/verify-gate.sh](../../examples/verify-gate.sh) running: one answer passes and one is refused.

[demo.tape](demo.tape) is the script that records it, for [vhs](https://github.com/charmbracelet/vhs). To record it again, from the repository root:

```sh
brew install vhs          # or see the vhs README for other systems
vhs docs/demo/demo.tape   # writes docs/demo/verify-gate.gif
```

Two things to know:

- The example prints the folder it runs from, so record from a checkout whose path you don't mind showing. The GIF here was recorded from a clone in `/tmp/alnair`.
- The tape sets `DEMO_PAUSE=1.5`, which makes the example wait 1.5 seconds before each step so the recording can be read. The output is the same without it, and the example then takes about a second.

Look at the result before you commit it. If lines wrap badly, change `Set Width` or `Set FontSize` in the tape.

## A real session (by hand)

There is no recording of a real session yet. vhs can't drive a model session in a way that comes out the same twice, so record this one yourself with any screen recorder. Keep it under 30 seconds. A sequence that shows what Alnair adds:

1. `alnair demo`, so the session opens on the sample notes and nothing private is on screen.
2. Ask "What is my AMZN exit plan?" and let the lookup, read and verify steps show.
3. Show the status line with the model the router picked.
4. Type `/lab-model opus` and send a second prompt, so the switch is visible.

Before recording: use a clean terminal profile, a scratch folder, and a window about 1280 by 720 so the text stays readable when the GIF is scaled down.
