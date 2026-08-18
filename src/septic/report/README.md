# report

Report composition and rendering.

The report presents what the rule engine already decided. It does not recompute or
soften the verdict, and a language model used for wording is given the verdict as
an input, never asked for it.

Start here: `compose.py` assembles the structured payload from rule evaluations,
facts and screening results. `render.py` turns that payload into HTML, both as a
standalone page and in embedded mode for the console. `wording.py` holds the
explanatory text that accompanies each outcome. `assets.py` manages the design
tokens, logos and static assets used by both the report and the console.
