"""
rag_engine/ingestion/delta_tracker.py

Delta ingestion support — determines which tickets need to be (re-)ingested
based on last ingestion time or explicit ticket_id list.

Two modes:
  1. since-timestamp: fetch all tickets with ticket_created_at > last_run_at
  2. explicit-list:   ingest only the provided ticket_id list (e.g., from feedback loop)

Used by the CLI's --mode delta command.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

import pandas as pd

LOGGER = logging.getLogger(__name__)


class DeltaTracker:
    """
    Determines which rows from the source dataset need ingestion/re-ingestion.
    Works entirely off the preprocessed parquet/CSV — does NOT query Freshdesk API.
    """

    def __init__(
        self,
        supabase_client: Any,           # supabase.Client
        ingestion_logs_table: str = "rag_ingestion_logs",
    ) -> None:
        self._client = supabase_client
        self._logs_table = ingestion_logs_table

    def get_last_completed_run_time(
        self,
        run_mode: str = "full",
        index_version: str = "v1",
    ) -> Optional[datetime]:
        """
        Fetch the started_at timestamp of the last COMPLETED ingestion run.
        Returns None if no completed run exists (triggers full ingest).
        """
        try:
            response = (
                self._client.table(self._logs_table)
                .select("started_at")
                .eq("status", "COMPLETED")
                .eq("index_version", index_version)
                .order("started_at", desc=True)
                .limit(1)
                .execute()
            )
            rows = response.data or []
            if rows:
                raw = rows[0].get("started_at", "")
                if raw:
                    # Normalize: replace Z with +00:00 for fromisoformat
                    return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Could not fetch last run time: %s", exc)
        return None

    def filter_delta(
        self,
        df: pd.DataFrame,
        since: Optional[datetime] = None,
        ticket_ids: Optional[list[str]] = None,
    ) -> pd.DataFrame:
        """
        Return only rows that need (re-)ingestion.

        Priority:
          1. If ticket_ids provided → filter to only those tickets
          2. If since provided → filter to tickets created/updated after that time
          3. If neither → return full dataframe (full ingest)
        """
        if ticket_ids:
            LOGGER.info("Delta filter: explicit list of %d ticket IDs", len(ticket_ids))
            if "ticket_id" in df.columns:
                mask = df["ticket_id"].astype(str).isin(set(str(t) for t in ticket_ids))
                result = df[mask]
                LOGGER.info("Matched %d rows from explicit ticket ID list", len(result))
                return result
            LOGGER.warning("ticket_id column not found in dataframe; returning full dataset")
            return df

        if since:
            LOGGER.info("Delta filter: tickets created after %s", since.isoformat())
            if "ticket_created_at" in df.columns:
                # Normalize timestamps
                df = df.copy()
                df["_created_dt"] = pd.to_datetime(df["ticket_created_at"], utc=True, errors="coerce")
                since_aware = since.replace(tzinfo=timezone.utc) if since.tzinfo is None else since
                mask = df["_created_dt"] > since_aware
                result = df[mask].drop(columns=["_created_dt"])
                LOGGER.info("Delta filter matched %d rows (of %d total)", len(result), len(df))
                return result
            LOGGER.warning(
                "ticket_created_at column not found; cannot apply delta filter. "
                "Running full ingest."
            )

        LOGGER.info("No delta filter applied; processing all %d rows", len(df))
        return df
