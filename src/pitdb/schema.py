"""
kdb-x table schemas for pitdb.

Every table has event_time (when it happened) and knowledge_time
(when it became publicly known). All writes are append-only.

Named query functions live in functions.q alongside this file and are
loaded at PitDB init. Query methods in connection.py delegate to those
functions rather than inlining q logic.
"""

SCHEMA_Q = """
prices:([]
  event_time:  `timestamp$();
  knowledge_time: `timestamp$();
  ticker:      `symbol$();
  open:        `float$();
  high:        `float$();
  low:         `float$();
  close:       `float$();
  volume:      `long$()
);

fundamentals:([]
  event_time:   `timestamp$();
  knowledge_time:`timestamp$();
  ticker:       `symbol$();
  eps:          `float$();
  pe_ratio:     `float$();
  revenue:      `float$()
);

earnings_history:([]
  event_time:   `timestamp$();
  knowledge_time:`timestamp$();
  ticker:       `symbol$();
  period:       `symbol$();
  eps_estimate: `float$();
  eps_actual:   `float$();
  surprise_pct: `float$()
);

corporate_actions:([]
  event_time:    `timestamp$();
  knowledge_time:`timestamp$();
  ticker:        `symbol$();
  action_type:   `symbol$();
  factor:        `float$()
);

provenance:([]
  event_time:       `timestamp$();
  knowledge_time:   `timestamp$();
  ticker:           `symbol$();
  table_name:       `symbol$();
  source_name:      `symbol$();
  source_url:       `symbol$();
  source_timestamp: `timestamp$()
);
"""


