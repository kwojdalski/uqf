"""Translates q's backfill status files into Airflow's sensor vocabulary.

See the package README for how status reaches Airflow and the ETL-15 boundary this
package does not cross. ``translate`` and ``status_reader`` have no Airflow
dependency at all; ``sensor`` defers its Airflow import to
``build_sensor_class()`` so importing this package never requires Airflow to
be installed.
"""

from __future__ import annotations
