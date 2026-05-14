"""
Column mapping dictionaries for all 4 source schemas → canonical CanonicalTicket field names.

Each map is {source_column_name: canonical_field_name}.
Only mapped columns are preserved; all others are silently dropped.
"""
from __future__ import annotations

# ── CSV / XLS (SpreadsheetML) ─────────────────────────────────────────────────
# Both files share nearly identical column names from the same Freshdesk export template.

CSV_XLS_COLUMN_MAP: dict[str, str] = {
    # Identity
    "Ticket ID":                        "ticket_id",
    "Subject":                          "subject",
    # Routing
    "Status":                           "status",
    "Priority":                         "priority",
    "Source":                           "source_channel",
    "Type":                             "ticket_type",
    "Agent":                            "agent",
    "Group":                            "group",
    # Timestamps — actual column names from export
    "Created time":                     "created_at",
    "Created at":                       "created_at",
    "Due by Time":                      "resolved_at",
    "Resolved time":                    "resolved_at",
    "Resolved at":                      "resolved_at",
    "Closed time":                      "closed_at",
    "Closed at":                        "closed_at",
    "Last update time":                 "last_updated_at",
    "Last update":                      "last_updated_at",
    "Updated at":                       "last_updated_at",
    # Tags
    "Tags":                             "tags_raw",
    # Metrics — actual column names from export
    "Agent interactions":               "agent_interactions",
    "Nr. of conversations":             "agent_interactions",
    "No. of agent interactions":        "agent_interactions",
    "Customer interactions":            "customer_interactions",
    "Customer conversations":           "customer_interactions",
    "No. of customer interactions":     "customer_interactions",
    "First response time (in hrs)":     "first_response_hrs",
    "Resolution time (in hrs)":         "resolution_hrs",
    "Resolution status":                "resolution_status",
    "First response status":            "first_response_status",
    # Operational classification — actual column names
    "Query Type":                       "query_type",
    "Query type":                       "query_type",
    "Issue Area":                       "issue_area",
    "Issue area":                       "issue_area",
    "Environment":                      "environment",
    "SOP Status":                       "sop_status",
    "SOP status":                       "sop_status",
    "Resolution Classification":        "resolution_classification",
    "Resolution classification":        "resolution_classification",
    "Issue Recurrence":                 "issue_recurrence",
    "Issue recurrence":                 "issue_recurrence",
    "Impact":                           "impact",
    "RCA status":                       "rca_status",
    "RCA Status":                       "rca_status",
    # Tenant
    "Clients":                          "client_name",
    "Client":                           "client_name",
    # References
    "StackOverflow Link":               "stackoverflow_link",
    "Stack Overflow Link":              "stackoverflow_link",
    "Asana Ticket Link":                "asana_ticket_link",
    "BajajFin Azure Ticket Id":         "bajaj_azure_ticket_id",
    "Bajaj Azure Ticket ID":            "bajaj_azure_ticket_id",
}

# ── RBL_RCA 1.xlsx (Sheet1) ───────────────────────────────────────────────────
# Has Description, RCA, Handling Time, Session IDs, StackOverflow links

RBL_RCA1_COLUMN_MAP: dict[str, str] = {
    # Identity
    "Ticket ID":                        "ticket_id",
    "Subject":                          "subject",
    "Description":                      "raw_description",
    # Routing
    "Status":                           "status",
    "Priority":                         "priority",
    "Source":                           "source_channel",
    "Type":                             "ticket_type",
    "Agent":                            "agent",
    "Group":                            "group",
    # Timestamps
    "Created time":                     "created_at",
    "Created at":                       "created_at",
    "Resolved time":                    "resolved_at",
    "Resolved at":                      "resolved_at",
    "Closed time":                      "closed_at",
    "Closed at":                        "closed_at",
    "Last update time":                 "last_updated_at",
    "Last Update at":                   "last_updated_at",
    "Updated at":                       "last_updated_at",
    # Tags
    "Tags":                             "tags_raw",
    # Metrics
    "Agent interactions":               "agent_interactions",
    "Nr. of conversations":             "agent_interactions",
    "No. of Agent interactions":        "agent_interactions",
    "Customer interactions":            "customer_interactions",
    "No. of Customer interactions":     "customer_interactions",
    "Handling Time":                    "handling_time_minutes",
    "First response time (in hrs)":     "first_response_hrs",
    "Resolution time (in hrs)":         "resolution_hrs",
    "Resolution status":                "resolution_status",
    "First response status":            "first_response_status",
    # Operational classification
    "Query Type":                       "query_type",
    "Query type":                       "query_type",
    "Issue Area":                       "issue_area",
    "Issue area":                       "issue_area",
    "Environment":                      "environment",
    "SOP Status":                       "sop_status",
    "SOP status":                       "sop_status",
    "Resolution Classification":        "resolution_classification",
    "Resolution classification":        "resolution_classification",
    "Issue Recurrence":                 "issue_recurrence",
    "Issue recurrence":                 "issue_recurrence",
    "Impact":                           "impact",
    "RCA status":                       "rca_status",
    "RCA Status":                       "rca_status",
    "RCA":                              "rca",
    # Session data
    "Session IDs":                      "session_ids_raw",
    "Session ID":                       "session_ids_raw",
    "session_ids":                      "session_ids_raw",
    # References
    "StackOverflow Link":               "stackoverflow_link",
    "Stack Overflow Link":              "stackoverflow_link",
    "Stackoverflow link":               "stackoverflow_link",
    "Asana Ticket Link":                "asana_ticket_link",
    "Asana ticket link":                "asana_ticket_link",
    # Tenant
    "Clients":                          "client_name",
    "Client":                           "client_name",
    # Misc
    "BajajFin Azure Ticket Id":         "bajaj_azure_ticket_id",
    "Bajaj Azure Ticket ID":            "bajaj_azure_ticket_id",
}

# ── RBL_RCA.xlsx (Unity sheet) ───────────────────────────────────────────────
# Same structure as RBL_RCA1; also has Description, RCA, session IDs

RBL_RCA_UNITY_COLUMN_MAP: dict[str, str] = RBL_RCA1_COLUMN_MAP  # same schema

# ── Source key → column map lookup ───────────────────────────────────────────

SOURCE_COLUMN_MAPS: dict[str, dict[str, str]] = {
    "csv":      CSV_XLS_COLUMN_MAP,
    "xls":      CSV_XLS_COLUMN_MAP,
    "rbl_rca1": RBL_RCA1_COLUMN_MAP,
    "rbl_rca":  RBL_RCA_UNITY_COLUMN_MAP,
}


def get_column_map(source_key: str) -> dict[str, str]:
    """Return the column name → canonical field map for the given source key."""
    try:
        return SOURCE_COLUMN_MAPS[source_key]
    except KeyError:
        raise ValueError(
            f"Unknown source key '{source_key}'. "
            f"Expected one of: {list(SOURCE_COLUMN_MAPS)}"
        )


def apply_column_map(
    raw_columns: list[str],
    source_key: str,
) -> dict[str, str]:
    """
    Map raw DataFrame column names to canonical names (case-insensitive).
    Returns {raw_col: canonical_col} for all recognized columns.
    Unrecognized columns are excluded.
    """
    col_map = get_column_map(source_key)
    # Build a lowercase → canonical lookup for case-insensitive matching
    lower_map: dict[str, str] = {k.lower().strip(): v for k, v in col_map.items()}

    result: dict[str, str] = {}
    seen_canonical: set[str] = set()

    for raw_col in raw_columns:
        # Try exact match first, then case-insensitive
        canonical = col_map.get(raw_col) or lower_map.get(raw_col.lower().strip())
        if canonical and canonical not in seen_canonical:
            result[raw_col] = canonical
            seen_canonical.add(canonical)

    return result
