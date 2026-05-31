"""MakoSync — push live swim results to makosmeets.

Two modes share one app and one ingest client:

  * **Dolphin** — watch a CTS Dolphin output folder, parse each ``.do3``/``.do4``
    heat file, POST unofficial times (and optionally the raw file).
  * **Manager** — runs on the Meet Manager PC and does both halves of its job at
    once: pulls the relayed Dolphin ``.do3`` files into the folder MM imports
    from (fast), and reads the Hy-Tek MM ``.mdb`` to POST the reconciled
    *official* results (places, DQs) — each on its own poll cadence.
"""

__version__ = "0.3.2"
