"""MakoSync — push live swim results to makosmeets.

Two modes share one app and one ingest client:

  * **Dolphin** — watch a CTS Dolphin output folder, parse each ``.do3``/``.do4``
    heat file, POST unofficial times (and optionally the raw file).
  * **Meet Manager** — read the Hy-Tek MM ``.mdb`` on the scoring PC and POST
    the reconciled *official* results (places, DQs, backup-watch times).
"""

__version__ = "0.2.0"
