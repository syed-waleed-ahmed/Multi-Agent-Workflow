# Accessibility

Campaign Forge should be usable by everyone who wants to try it. This page says what is in place, what is known not to work well, and how to report a barrier.

## Supported environments

- **Web app** (the public demo and `streamlit run app/streamlit_app.py`): current desktop versions of Chrome, Edge, Firefox and Safari. The app is built on [Streamlit](https://streamlit.io/), so keyboard navigation, focus handling and screen-reader support are largely Streamlit's own.
- **CLI:** any terminal. Output is plain text and Markdown. `--json` gives machine-readable output for other tools, including screen-reader workflows.
- **HTTP API:** JSON over HTTP. The OpenAPI schema is in [`docs/openapi.json`](docs/openapi.json).

## What is in place

- **Charts are never the only source.** Every workflow graph (the plan preview and the Run Inspector) has a table next to it that lists every step, its agent, status, tries, review score and timing.
- **Text in text form.** Results are Markdown text, and download as Markdown, JSON or HTML. None of the content is an image.
- **Status is never colour alone.** Run and step states are written out ("succeeded", "failed", "waiting for approval"), not only coloured.
- **Read-only alternatives.** Everything the web app does is also available from the CLI and the API.

## Known limitations

- **The workflow graphs** are rendered images, without detailed text alternatives. The adjacent step tables carry the same information.
- **Data tables** are Streamlit's interactive grid, which can be hard to navigate with a screen reader. The downloads (Markdown, JSON, HTML) contain the same data.
- **The live progress view** refreshes every two seconds while a run executes, which some assistive technology announces repeatedly. The completed run's page does not refresh.
- **The theme** is Streamlit's dark theme, with no separate high-contrast mode.

## Reporting a barrier

Open an issue (the bug report template works) describing:
- what you tried to do;
- the page or command;
- your browser or terminal, and any assistive technology you use.

Accessibility problems are treated as bugs.
