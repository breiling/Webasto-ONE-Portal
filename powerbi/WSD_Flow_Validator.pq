// WSD Flow Validator — Power BI Query
// =====================================================================
// How to use:
//   1. Start the data-feed server:  python powerbi/serve.py
//   2. In Power BI Desktop:
//        Home → Transform Data → New Source → Blank Query
//        Home → Advanced Editor → paste this entire file → Done
//   3. Rename the query to "WSD Requests"
//   4. Click "Close & Apply"
//
// Change the server URL below if running on a different host/port.
// =====================================================================

let
    ServerUrl = "http://localhost:8081/data",

    // ── Fetch & parse ────────────────────────────────────────────────
    RawJson   = Web.Contents(ServerUrl),
    Records   = Json.Document(RawJson),
    AsTable   = Table.FromList(Records, Splitter.SplitByNothing(), null, null, ExtraValues.Error),

    // ── Expand the nested record into columns ────────────────────────
    Expanded  = Table.ExpandRecordColumn(
                    AsTable, "Column1",
                    {
                        "generatedAt", "id", "severity",
                        "crmAccountNumber", "contactName", "contactEmail",
                        "requestedSystems", "status",
                        "submittedAt", "lastUpdatedAt",
                        "ageHours", "staleHours",
                        "assignedTo", "notes", "message", "flags"
                    }
                ),

    // ── Set column types ────────────────────────────────────────────
    Typed     = Table.TransformColumnTypes(
                    Expanded,
                    {
                        {"generatedAt",   type datetimezone},
                        {"submittedAt",   type datetimezone},
                        {"lastUpdatedAt", type datetimezone},
                        {"ageHours",      type number},
                        {"staleHours",    type number},
                        {"id",            type text},
                        {"severity",      type text},
                        {"crmAccountNumber", type text},
                        {"contactName",   type text},
                        {"contactEmail",  type text},
                        {"requestedSystems", type text},
                        {"status",        type text},
                        {"assignedTo",    type text},
                        {"notes",         type text},
                        {"message",       type text},
                        {"flags",         type text}
                    }
                ),

    // ── Add a severity sort order column (for correct chart ordering) ─
    WithOrder = Table.AddColumn(
                    Typed, "SeverityOrder", each
                        if [severity] = "ESCALATION" then 1
                        else if [severity] = "STUCK"       then 2
                        else if [severity] = "DELAYED"     then 3
                        else 4,
                    Int64.Type
                )

in
    WithOrder


// =====================================================================
// Suggested report structure
// =====================================================================
//
// Page 1 — Summary
//   • Card: Total Active Requests       (COUNT of id)
//   • Card: Escalation                  (COUNTIF severity = "ESCALATION")
//   • Card: Stuck                       (COUNTIF severity = "STUCK")
//   • Card: Delayed                     (COUNTIF severity = "DELAYED")
//   • Donut chart: Count by Severity    (legend = severity, values = COUNT id)
//   • Bar chart:   Count by Status      (axis = status, values = COUNT id)
//
// Page 2 — Flagged Requests
//   • Table columns:
//       id | severity | status | contactName | contactEmail
//       staleHours | assignedTo | flags | message
//   • Conditional formatting on severity:
//       ESCALATION → red   (#FF4444)
//       STUCK      → amber (#FFA500)
//       DELAYED    → blue  (#4472C4)
//       OK         → green (#70AD47)
//   • Slicer: severity (multi-select)
//   • Slicer: status   (multi-select)
//
// Page 3 — Unassigned / At-Risk
//   • Filter: severity <> "OK"
//   • Table: id | contactName | status | staleHours | assignedTo
//     Sorted by staleHours DESC
//
// Refresh schedule (Power BI Service):
//   • Publish the report to Power BI Service
//   • Dataset settings → Scheduled refresh → set interval (e.g. every hour)
//   • The gateway must be able to reach the data-feed server URL
// =====================================================================
