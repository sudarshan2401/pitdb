"""
kdb-x table schemas for pitdb.

Every table has event_time (when it happened) and knowledge_time
(when it became publicly known). All writes are append-only.
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

LOAD_Q = """
loadTable:{[dir;name]
  path: hsym `$string[dir],"/",string[name];
  if[()~key path; :()];
  t: get path;
  name set t;
  }

saveTable:{[dir;name]
  path: hsym `$string[dir],"/",string[name];
  path set value name;
  }
"""

QUERY_Q = """
/ Point-in-time lookup: latest known value for ticker at event_time,
/ as known at knowledge_time.
pitGet:{[tbl;tkr;et;kt]
  t: value tbl;
  select from t where ticker=tkr, event_time=et, knowledge_time<=kt
  }

/ Price history: all daily prices for a ticker over a range,
/ as known at knowledge_time kt.
pitPriceHistory:{[tkr;startDt;endDt;kt]
  select last open, last high, last low, last close, last volume
    by event_time
    from prices
    where ticker=tkr,
          event_time within (startDt;endDt),
          knowledge_time<=kt
  }

/ Latest fundamentals for a ticker as known at knowledge_time kt,
/ for any event at or before et.
pitFundamentals:{[tkr;et;kt]
  select last eps, last pe_ratio, last revenue
    by ticker
    from fundamentals
    where ticker=tkr,
          event_time<=et,
          knowledge_time<=kt
  }

/ Earnings history for a ticker over time, as known at kt.
pitEarningsHistory:{[tkr;startDt;endDt;kt]
  select last eps_estimate, last eps_actual, last surprise_pct
    by event_time, period
    from earnings_history
    where ticker=tkr,
          event_time within (startDt;endDt),
          knowledge_time<=kt
  }

/ Corporate actions for a ticker over a range, as known at kt.
pitCorpActions:{[tkr;startDt;endDt;kt]
  select from corporate_actions
    where ticker=tkr,
          event_time within (startDt;endDt),
          knowledge_time<=kt
  }

/ Provenance for a ticker at a specific event_time.
pitProvenance:{[tkr;et]
  select from provenance where ticker=tkr, event_time=et
  }

/ Check what data is available for a ticker over a range.
pitAvailability:{[tkr;startDt;endDt]
  priceCount:  count select from prices where ticker=tkr, event_time within (startDt;endDt);
  fundCount:   count select from fundamentals where ticker=tkr, event_time within (startDt;endDt);
  actionCount: count select from corporate_actions where ticker=tkr, event_time within (startDt;endDt);
  `prices`fundamentals`corporate_actions!(priceCount;fundCount;actionCount)
  }
"""
