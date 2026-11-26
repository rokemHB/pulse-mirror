"""Garmin Connect source (adapter over python-garminconnect).

Public surface for the rest of the app:

* :func:`client.login_interactive` / :meth:`client.GarminClient.from_tokenstore`
* :func:`service.sync_day`, :func:`service.sync_activities`, :func:`service.remap_all`
* :func:`backfill.run_backfill`
* errors in :mod:`errors`

Only :mod:`client` imports ``garminconnect``.
"""
