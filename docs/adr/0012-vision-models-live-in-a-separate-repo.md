# Vision models live in a separate repo; the app talks to them through a proposals file

The vision models (starting with whistle detection, see CONTEXT.md's **Whistle**) are trained, evaluated and run from a separate ML repo, not from this one. This repo holds only the app side: launching the detection command, importing the **proposals file** it writes, and the review screen where the user resolves each Whistle. The proposals file is the one contract between the two repos. It carries a top-level `format_version`, and every detection is stamped with the model name/version that produced it. The format is defined and tested here, as the consumer, against fixture files. The app refuses a version it doesn't know rather than guessing.

We chose this because the two sides pull in opposite directions. ML work is heavy (torch, often a CUDA build, roughly 1–2 GB), full of data and weights that don't belong in git, and non-deterministic. The app is a light, `pip install`-able, offline desktop app whose CI runs pytest on Windows and macOS. Putting the models here would drag torch into the app's environment and CI, or split the repo into two installs anyway. Inference stays in the ML repo too, not just training: detection's preprocessing (frame sampling, audio windowing) must match training's exactly, and keeping both in one codebase is how that stays true.

## Considered Options

- **Everything in this repo, as an optional `[vision]` extra.** Rejected. Inference code would live away from the training code it must match, and the repo would take on data, weights and GPU dependencies.
- **Inference inside the app process.** Rejected for now. It would put torch into the app's environment. Launching a separate command keeps the app's dependencies unchanged, and the command can be pulled in-process later if detection proves itself.

## Consequences

- The app finds the tool through a setting pointing to the ML environment's interpreter/command, defaulting to a command on PATH. The ML stack is expected to live in its own virtualenv. When the tool is missing or fails, the app greys out detection with a reason; it never crashes.
- Training data flows the other way through an **export** of the app's tagged games (events, resolved Whistles, footage path), not through the app's database or ORM models. Schema changes here then can't silently break training.
- No model runs in this repo's CI.
