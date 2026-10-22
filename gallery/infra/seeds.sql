-- seeds.sql
-- Point-in-time fact store for the nohindsight demo, in PostgreSQL.
--
-- Model matches the Rust ledger in src/core.rs: one key per symbol,
-- "<SYM>.revenue", each row a revision. event_time is the period the number
-- describes, knowledge_time is when the world could first read it. The gap
-- between them is where leaks live.

-- ---------------------------------------------------------------------------
-- Table. The check is named for the error the Rust core raises on the same
-- condition: EntryError::ImpossibleOrder, src/core.rs. The world cannot know
-- a number before it happened.
-- ---------------------------------------------------------------------------

CREATE TABLE facts (
    key            text        NOT NULL,
    value          numeric     NOT NULL,
    event_time     timestamptz NOT NULL,
    knowledge_time timestamptz NOT NULL,
    revision       int         NOT NULL DEFAULT 1,
    PRIMARY KEY (key, knowledge_time),
    CONSTRAINT facts_impossible_order_check CHECK (knowledge_time >= event_time)
);

-- ---------------------------------------------------------------------------
-- Seeding: 8 symbols x 12 quarters, quarter ends 2022-03-31 through
-- 2024-12-31, each filed with a 45-day lag. Values are deterministic
-- arithmetic, no rand(), so re-running the seed produces the same ledger.
-- ---------------------------------------------------------------------------

INSERT INTO facts (key, value, event_time, knowledge_time, revision)
SELECT
    'SYM' || s || '.revenue',
    round((8000 + s * 250 + q * 40 + ((s * 7 + q * 13) % 97) * 10)::numeric, 2),
    q_end::timestamptz,
    q_end::timestamptz + interval '45 days',
    1
FROM generate_series(0, 7) AS s
CROSS JOIN generate_series(1, 12) AS q
CROSS JOIN LATERAL (
    -- quarter end for quarter q counting from January 2022
    SELECT (date '2022-01-01' + make_interval(months => q * 3) - 1) AS q_end
) qe;

-- ---------------------------------------------------------------------------
-- Restatement: the demo's planted crime. SYM2's most recent quarter gets a
-- second revision, same key, same period, knowledge_time 21 days after the
-- original filing, value 30 percent lower. In the real demo the restatement
-- was first published 2024-03-01 and the naive backtest read it up to 58
-- days before the world had it.
--
-- The WHERE guard makes the insert idempotent: it fires only if no revision
-- past 1 exists yet for that key and period.
-- ---------------------------------------------------------------------------

INSERT INTO facts (key, value, event_time, knowledge_time, revision)
SELECT
    f.key,
    round(f.value * 0.70, 2),
    f.event_time,
    f.knowledge_time + interval '21 days',
    f.revision + 1
FROM facts f
WHERE f.key = 'SYM2.revenue'
  AND f.revision = 1
  AND f.event_time = (
        SELECT max(event_time) FROM facts
        WHERE key = 'SYM2.revenue' AND revision = 1
  )
  AND NOT EXISTS (
        SELECT 1 FROM facts r
        WHERE r.key = f.key
          AND r.event_time = f.event_time
          AND r.revision > f.revision
  );

-- ---------------------------------------------------------------------------
-- Decision log stand-in: one decision per day for SYM2, spanning 15 days
-- before the original filing to 15 days after the restatement. The demo's
-- real window was 43 decision days, 2024-01-02 to 2024-03-01. The shape is
-- the same: a run of decisions that straddles a restatement.
-- ---------------------------------------------------------------------------

CREATE TEMP TABLE decisions (
    decision_id   serial PRIMARY KEY,
    key           text NOT NULL,
    decision_date date NOT NULL
);

INSERT INTO decisions (key, decision_date)
SELECT
    'SYM2.revenue',
    d::date
FROM generate_series(
    (SELECT min(knowledge_time) FROM facts
      WHERE key = 'SYM2.revenue' AND revision = 2) - interval '15 days',
    (SELECT max(knowledge_time) FROM facts
      WHERE key = 'SYM2.revenue' AND revision = 2) + interval '15 days',
    interval '1 day'
) AS d;

-- ---------------------------------------------------------------------------
-- The honest read: the AS-OF query the backtest should run. For each
-- decision, the lateral picks the newest revision whose knowledge_time does
-- not exceed the decision date. This one line is the whole point of the
-- store: no decision can read a number the world did not have yet.
-- ---------------------------------------------------------------------------

SELECT d.decision_id,
       d.key,
       d.decision_date,
       f.revision  AS revision_used,
       f.value     AS value_used
FROM decisions d
CROSS JOIN LATERAL (
    SELECT *
    FROM facts p
    WHERE p.key = d.key
      AND p.knowledge_time <= d.decision_date::timestamptz
    ORDER BY p.knowledge_time DESC
    LIMIT 1
) f
ORDER BY d.decision_date;

-- ---------------------------------------------------------------------------
-- RESTATED_BUT_NOT_YET_KNOWN: the query that would have caught the demo
-- leak. Same AS-OF lateral, then a second lateral finds a later revision of
-- the same key and period. The final WHERE keeps only decisions where that
-- restatement's knowledge_time is still in the future: the decision used a
-- number that was later restated, and the operator had no way to know. In
-- the demo this is the naive book's 43 tainted decision days.
--
-- Rows with no matching restatement drop out of the CROSS JOIN, which is
-- the flag semantics: only tainted decisions survive.
-- ---------------------------------------------------------------------------

SELECT d.decision_id,
       d.key,
       d.decision_date,
       f.revision       AS revision_used,
       f.value          AS value_used,
       r.value          AS restated_value,
       r.knowledge_time AS restated_at
FROM decisions d
CROSS JOIN LATERAL (
    SELECT *
    FROM facts p
    WHERE p.key = d.key
      AND p.knowledge_time <= d.decision_date::timestamptz
    ORDER BY p.knowledge_time DESC
    LIMIT 1
) f
CROSS JOIN LATERAL (
    SELECT *
    FROM facts p2
    WHERE p2.key = f.key
      AND p2.event_time = f.event_time
      AND p2.revision > f.revision
) r
WHERE r.knowledge_time > d.decision_date::timestamptz
ORDER BY d.decision_date;
