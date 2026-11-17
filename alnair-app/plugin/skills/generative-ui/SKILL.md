---
name: generative-ui
description: Show task-shaped cards, tables, charts, code, diffs, checklists, markdown, images, forms and choices on Alnair's canvas.
---
Use mcp__alnair_ui__ui_render when visual structure improves an answer. Always keep the conversation useful in the terminal too. Use short stable ids; ui_update replaces the complete component for that id. ui_clear removes one id, or all when omitted. Follow the exact tool input schema. Never invent HTML, JavaScript, CSS or component types. Markdown is sanitized; images must be embedded PNG/JPEG/WebP data URLs, not remote URLs or SVG. Never put secrets on the canvas.

For a human decision, use ui_ask with a form or choices component and timeout (1–600 seconds). It waits for an actual click or submission; a timeout is not consent. ui_render can show nonblocking form/choices but use ui_ask when you need the answer. Presenting an action is not authorization to execute it. Require the user's explicit approval before irreversible external actions. Do not launch subprocesses, spend API credits, or bypass trust/permission prompts to make a panel work.
