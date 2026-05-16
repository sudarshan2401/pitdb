/ pitdb named query functions.
/ Loaded at PitDB init alongside the table schema.

/ --- Persistence ---

pitLoadTable:{[n;p]
  path:hsym `$string p;
  if[not ()~key path; n set get path]
  }

pitSaveTable:{[p;t]
  (hsym `$string p) set t
  }

/ --- Point-in-time queries ---

pitGet:{[tbl;tkr;et;kt]
  select from (value tbl) where ticker=tkr, event_time=et, knowledge_time<=kt
  }

pitPriceHistory:{[tkr;startDt;endDt;kt]
  select last open, last high, last low, last close, last volume
    by event_time
    from prices
    where ticker=tkr,
          event_time within (startDt;endDt),
          knowledge_time<=kt
  }

pitFundamentals:{[tkr;et;kt]
  t:select from fundamentals where ticker=tkr, event_time<=et, knowledge_time<=kt;
  if[0=count t; :()];
  e:t`eps; r:t`revenue;
  ([]ticker:enlist tkr;eps:enlist last e where not null e;revenue:enlist last r where not null r)
  }

pitLatestClose:{[tkr;et;kt]
  last exec close from (select last close by event_time from prices
    where ticker=tkr, event_time<=et, knowledge_time<=kt)
  }

pitEarningsHistory:{[tkr;startDt;endDt;kt]
  select last eps_estimate, last eps_actual, last surprise_pct
    by event_time, period
    from earnings_history
    where ticker=tkr,
          event_time within (startDt;endDt),
          knowledge_time<=kt
  }

pitCorpActions:{[tkr;startDt;endDt;kt]
  select from corporate_actions
    where ticker=tkr,
          event_time within (startDt;endDt),
          knowledge_time<=kt
  }

pitProvenance:{[tkr]
  select from provenance where ticker=tkr
  }

pitProvenanceAt:{[tkr;et]
  select from provenance where ticker=tkr, event_time=et
  }

pitAvailability:{[tkr;startDt;endDt]
  priceCount:  count select from prices where ticker=tkr, event_time within (startDt;endDt);
  fundCount:   count select from fundamentals where ticker=tkr, event_time within (startDt;endDt);
  actionCount: count select from corporate_actions where ticker=tkr, event_time within (startDt;endDt);
  `prices`fundamentals`corporate_actions!(priceCount;fundCount;actionCount)
  }
