# future

A single-file Raspberry Pi project. `servo.py` reads eight push-buttons and drives
four servo motors via GPIO PWM (BOARD pin numbering, 50 Hz signal, duty cycle 4–11%).

## Cursor Cloud specific instructions

### What this codebase is
- The only application is `servo.py`. It targets **Raspberry Pi hardware** through
  `RPi.GPIO` (physical GPIO pins / `/dev/gpiomem`). There are no tests and no build system.

### Running / developing off-Pi (the cloud VM is x86, not a Raspberry Pi)
- Real `RPi.GPIO` cannot run here: it needs to compile against a Pi and, even when built,
  its runtime requires actual Pi hardware. For development on the cloud VM, use the
  pure-Python `fake_rpi` GPIO mock (installed by the update script). Shim it before importing:
  ```python
  import sys, fake_rpi
  sys.modules["RPi"] = fake_rpi.RPi
  sys.modules["RPi.GPIO"] = fake_rpi.RPi.GPIO
  import RPi.GPIO as GPIO  # now resolves to the mock
  ```
  `fake_rpi` prints `<<< WARNING: using fake raspberry pi interfaces >>>` and logs every
  GPIO call — that is expected, not an error.

### Lint
- `python3 -m flake8 servo.py`  (flake8 is installed to `~/.local/bin`, which is not on
  PATH, so invoke it via `python3 -m flake8`).
- Note: `servo.py` currently fails lint with `E999 TabError` at line 39 (it mixes tabs and
  spaces and has CRLF line endings), so it will not `py_compile` as-is. This is a
  pre-existing state of the committed code, not an environment problem.

### Tests / build
- There are no automated tests and no build step.
