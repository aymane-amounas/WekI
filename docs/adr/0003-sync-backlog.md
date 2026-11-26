# 3. Persistent sync backlog

Status: accepted

## Context

A sync run lists everything that changed on the wiki since the cursor, then fetches each changed page over a rate-limited, interruptible connection. Listing is cheap and bounded; fetching is slow, can fail per page, and can be cancelled or killed at any moment. If fetching drove straight off the listing in memory, a crash or a stop would lose the knowledge that those pages need fetching, and the next run would only see changes newer than the advanced cursor.

## Decision

Split sync into two phases with a durable queue between them.

1. List changes and write them to a `backlog` table, advancing the `last_ts` cursor, in a single transaction. The backlog row is the unit of outstanding work.
2. Drain the backlog up to `cap` pages per run. Each fetched page is stored and its backlog row deleted in one transaction. Pages that fail stay queued.

Ordering out of the backlog is: pages the user has read first, then new pages, then the rest, newest first within each. `enqueue` merges duplicates, keeping the strongest priority and newest revision.

## Consequences

- The cursor only advances once the changes it covers are safely in the backlog, so an interrupted fetch phase never loses work. The next run re-drains the backlog before listing anything new. See the recovery doc for the exact interruption cases and the tests that cover them.
- Re-processing is idempotent. Storing a page is an upsert keyed by path; a revision already stored is skipped by a revision-id check. Replaying the same change is a no-op.
- Work is prioritized toward what the user actually reads, so a capped run spends its budget where it matters instead of fetching in listing order.
- The backlog can grow faster than `cap` drains it during heavy upstream activity. That is visible on the status page as a non-empty queue and is the main reason full English Wikipedia at scale is still an open problem.
